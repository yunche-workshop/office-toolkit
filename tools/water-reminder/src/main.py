# -*- coding: utf-8 -*-
"""
main.py —— 入口与主调度

后台只有一个线程在跑托盘消息循环；提醒判断挂在 Tk 的一条 500ms 心跳上，
到点才做调度（最长 15 秒判断一次），空闲时 CPU 占用接近 0。
"""

import datetime as dt
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk

import core
import icon
import win32ext
from ui import HintWindow, ReminderWindow, SettingsWindow

TICK_MS = 15000        # 调度判断的最长间隔
POLL_MS = 500          # 主循环心跳：托盘点击的响应速度由它决定
MISSED_LIMIT = 2       # 弹窗连续几次没人点就不再追着顺延提醒，回归正常间隔
QUIT_CONFIRM_SECONDS = 8   # "退出"要点两次，第二次在这个窗口内才算数（和提示条停留时间对齐）
INSTANCE_NAME = "WaterReminder_SingleInstance_v2"

MENU_SETTINGS = 1001
MENU_REMIND = 1002
MENU_DRINK = 1003
MENU_PAUSE = 1004
MENU_EXIT = 1005
MENU_AUTOSTART = 1006
MENU_RESUME = 1007
MENU_UNDO = 1008
MENU_SKIP = 1009
TRAY_DOUBLE_CLICK = 90001
TRAY_SINGLE_CLICK = 90002
TRAY_ACTIVATE = win32ext.TRAY_ACTIVATE   # 第二实例把设置窗叫出来


def report_exception(where, *args):
    """
    异常必须落进 run.log。

    windowed 打包后没有控制台，Tk 回调里抛出的异常默认只往 stderr 喷一屏，
    用户什么都看不见；表现就是"程序还在，但到点不提醒了"。
    在 except 块里可以直接调用，不带参数就从 sys.exc_info() 取。
    """
    try:
        exc_type, exc, tb = (args or sys.exc_info())[:3]
        if exc_type is None:
            detail = "未知错误（拿不到异常信息）"
        else:
            detail = "".join(traceback.format_exception(exc_type, exc, tb)).strip()
        core.log("%s 未捕获异常：%s" % (where, detail))
    except Exception:
        pass


def install_exception_hooks(root):
    """主线程回调、后台线程、解释器出口三条路都要接住，漏一条就是静默死亡。"""
    def tk_hook(exc_type, exc, tb):
        report_exception("Tk 回调", exc_type, exc, tb)

    root.report_callback_exception = tk_hook
    sys.excepthook = lambda t, v, tb: report_exception("主线程", t, v, tb)
    if hasattr(threading, "excepthook"):   # 3.8 有；老版本没有就只兜住主线程
        def thread_hook(args):
            report_exception("后台线程（托盘）", args.exc_type,
                             args.exc_value, args.exc_traceback)

        threading.excepthook = thread_hook


class App(object):
    def __init__(self, root):
        self.root = root
        self.cfg = core.Config()
        self.store = core.Store()
        self.icon_path = icon.icon_path(core.DATA_DIR)
        self.icon_bundled = bool(icon.bundled_icon())
        self.tasks = queue.Queue()

        self.next_at = None          # 下次提醒时间
        self._active_now = False     # 上一次判断时是否在活跃时段（用于识别"刚进时段"）
        self._miss_streak = 0        # 连续几次弹窗没人点（自动收起）
        self.skip_date = None        # “今天不提醒了”的日期
        self.pause_until = None      # 临时暂停到某时刻
        self.settings_win = None
        self.reminder_win = None
        self.hint_win = None
        self.tray = None
        self._running = True
        self._wake = 0.0             # 下次做调度判断的墙钟时刻
        self._hide_hint_shown = False
        self._quit_armed = 0.0       # 上次点"退出程序"的时刻（二次确认用）

        # 配置里的自启动开关以注册表实际状态为准
        actual = win32ext.get_autostart()
        if actual != bool(self.cfg.get("autoStart")):
            self.cfg.data["autoStart"] = actual

    # ------------------------------------------------------------ 启动
    def start(self, minimized=False):
        self.tray = win32ext.TrayIcon(
            self.icon_path,
            self.tooltip_text(),
            self.tray_menu(),
            self.on_tray_command,
        )
        if not self.tray.start():
            core.log("托盘图标创建失败，程序仍然会在后台运行")

        moved = self.store.archive_old()
        core.log(
            "启动 v%s：配置=%s 数据=%s（%s）图标=%s 外观=%s 提示音=%s"
            % (core.VERSION, core.CONFIG_PATH, core.DATA_DIR,
               "exe 同目录" if core.BASE_DIR_KIND == "portable" else "回落到用户目录",
               "随包" if self.icon_bundled else "自算兜底",
               self.cfg.get("theme", "auto"),
               "开" if self.cfg.get("sound", True) else "关")
        )
        if moved:
            core.log("归档：把 %d 个超过 %d 天的日记录挪进 data/archive/"
                     % (moved, core.ARCHIVE_AFTER_DAYS))
        if self.cfg.corrupt_backup:
            core.log("配置文件损坏，已另存为 %s 并用默认值启动"
                     % os.path.basename(self.cfg.corrupt_backup))
        elif self.cfg.clamp_report():
            core.log("配置加载时有纠正项，设置窗口里已回显")
        if not win32ext.get_autostart():
            core.log("提示：未开启开机自启，重启电脑后不会自动提醒")

        self._wake = time.time() + POLL_MS / 1000.0
        self.root.after(POLL_MS, self.loop)

        if not minimized or self.cfg.created_new:
            self.root.after(300, self.open_settings)
        else:
            # 自启动/最小化启动时什么都没弹，用户容易以为"程序没开"
            self.root.after(1500, lambda: self.show_hint(
                "喝水提醒已在后台运行，到点会弹右下角", seconds=5))
        core.log("状态：%s" % self.status_line())

    # ------------------------------------------------------------ 托盘
    def tooltip_text(self):
        """
        鼠标停在托盘图标上的那一句。以前只有"今日 X / Y ml"，
        暂停中 / 今天不提醒了 / 不在时段这几种"其实不会弹"的状态完全看不出来。
        """
        total = self.store.total()
        goal = self.cfg.get("dailyGoal", 2000)
        unit = core.unit_label(self.cfg)
        now = dt.datetime.now()
        head = "喝水提醒：今日 %s / %s %s" % (
            core.to_display(self.cfg, total), core.to_display(self.cfg, goal), unit)
        if self.pause_until and now < self.pause_until:
            return "%s · 已暂停到 %s" % (head, self.pause_until.strftime("%H:%M"))
        if self.skip_date == now.strftime("%Y-%m-%d"):
            return "%s · 今天已设为不再提醒" % head
        active, reason = core.is_active(self.cfg, now)
        if not active:
            return "%s · 现在不打扰（%s）" % (head, reason)
        if self.next_at:
            return "%s · 下一杯 %s" % (head, self.next_at.strftime("%H:%M"))
        return head

    def tray_menu(self):
        now = dt.datetime.now()
        paused = bool(self.pause_until and now < self.pause_until)
        skipped = self.skip_date == now.strftime("%Y-%m-%d")
        return [
            (MENU_SETTINGS, "打开设置"),
            (MENU_REMIND, "立刻提醒一次"),
            (MENU_DRINK, "记一杯 %s" % core.fmt_vol(
                self.cfg, self.cfg.get("amountPerReminder", 250))),
            # 手滑多点一次就多 250 ml，以前只能自己翻 JSON 删
            (MENU_UNDO, "撤销上一次记录"),
            None,
            (MENU_PAUSE, "取消暂停" if paused else "暂停 1 小时", paused),
            (MENU_AUTOSTART, "开机自启", bool(self.cfg.get("autoStart"))),
            # 菜单项写"要做的事"：今天已经静音了，这一条就该是「恢复今天的提醒」
            (MENU_SKIP, "恢复今天的提醒" if skipped else "今天不再提醒"),
            None,
            (MENU_EXIT, "退出"),
        ]

    def on_tray_command(self, cmd):
        """托盘线程回调 —— 只投递任务，UI 操作全部回主线程执行。"""
        self.tasks.put(cmd)

    def loop(self):
        """
        主循环只剩一个：500ms 醒一次收托盘任务，到点才做调度判断。
        以前是 after(200, poll_tasks) + after(15000, tick) 两条链，
        托盘点击最慢要等 200ms，两条链各自还都在异常时会静默断掉。
        """
        if not self._running:
            return
        try:
            while True:
                self.handle_command(self.tasks.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            report_exception("托盘任务")

        now = time.time()
        if now >= self._wake:
            try:
                self.check()
            except Exception:
                report_exception("调度判断")
            wake = now + TICK_MS / 1000.0
            if self.next_at is not None:
                try:
                    wake = min(wake, self.next_at.timestamp())
                except Exception:
                    pass
            self._wake = wake
        self.root.after(POLL_MS, self.loop)

    def handle_command(self, cmd):
        if cmd in (MENU_SETTINGS, TRAY_DOUBLE_CLICK, TRAY_SINGLE_CLICK,
                   TRAY_ACTIVATE):
            self.open_settings()
        elif cmd == MENU_REMIND:
            self.show_reminder_now()
        elif cmd == MENU_DRINK:
            self.record_drink(self.cfg.get("amountPerReminder", 250))
        elif cmd == MENU_UNDO:
            self.undo_last()
        elif cmd == MENU_PAUSE:
            # 一条菜单项做开关：正在暂停时这一条就是「取消暂停」
            moment = dt.datetime.now()
            if self.pause_until and moment < self.pause_until:
                self.resume_pause()
            else:
                self.pause_until = moment + dt.timedelta(hours=1)
                self._active_now = False   # 暂停结束后算"重新进入时段"，第一杯不等满间隔
                core.log("暂停 1 小时，到 %s" % self.pause_until.strftime("%H:%M"))
                self.refresh_tray()
        elif cmd == MENU_RESUME:
            self.resume_pause()
        elif cmd == MENU_SKIP:
            today = dt.datetime.now().strftime("%Y-%m-%d")
            if self.skip_date == today:
                self.skip_date = None
                self._active_now = False   # 恢复算"重新进入时段"，先补一杯
                core.log("已恢复今天的提醒")
                self.show_hint("已恢复，今天继续提醒", seconds=4)
            else:
                self.skip_today()
                self.show_hint("今天不再弹提醒，明天进入时段自动恢复", seconds=4)
            self.refresh_tray()
        elif cmd == MENU_AUTOSTART:
            self.set_autostart(not bool(self.cfg.get("autoStart")))
        elif cmd == MENU_EXIT:
            # 托盘那条"退出"同样要确认：右键菜单点错一下，今天就不会再提醒了
            self.quit_from_ui()

    def resume_pause(self):
        """取消暂停：算"重新进入时段"，第一杯不等满一个间隔。"""
        self.pause_until = None
        self.next_at = None
        self._active_now = False
        core.log("已取消暂停")
        self.refresh_tray()

    def set_autostart(self, want):
        """
        自启的唯一写入口（托盘菜单和设置窗口都走这里）。
        返回值是注册表里的实际状态：写失败时调用方要按真相回退界面，
        以前是"界面显示已开、注册表里根本没有"，重启后不提醒又要重新排查一遍。
        """
        try:
            win32ext.set_autostart(bool(want))
            actual = bool(win32ext.get_autostart())
        except Exception as exc:
            core.log("开机自启设置异常：%s" % exc)
            actual = False if not want else bool(self.cfg.get("autoStart"))
        self.cfg.data["autoStart"] = actual
        self.cfg.save()
        core.log("开机自启：%s%s" % ("开" if actual else "关",
                                   "" if actual == bool(want) else "（写入未成功）"))
        self.refresh_tray()
        return actual

    def undo_last(self):
        """撤销托盘/弹窗手滑记下的那一杯。"""
        amount, remaining = self.store.pop_last()
        if not amount and not remaining:
            self.show_hint("今天还没有可撤销的记录", seconds=4)
            return
        if self.store.total() < self.cfg.get("dailyGoal", 2000):
            self.store.clear_celebrated()   # 撤回到目标以下，达标庆祝要能重新弹
        self._miss_streak = 0
        core.log("撤销记录 -%d ml，还剩 %d 条，今日 %d ml"
                 % (amount, remaining, self.store.total()))
        self.show_hint("已撤销 %s，今日 %s" % (
            core.fmt_vol(self.cfg, amount),
            core.fmt_vol(self.cfg, self.store.total())), seconds=4)
        self.refresh_tray()

    def status_line(self, now=None):
        """
        给设置窗口顶部那一行用：现在到底是什么状态、下一杯几点。
        这轮"到点不提醒"排查下来，最缺的就是这条——程序在跑、时段没到、
        今天被设成不再提醒、暂停中，四种情况在界面上长得一模一样。
        """
        now = now or dt.datetime.now()
        if self.skip_date == now.strftime("%Y-%m-%d"):
            return "今天已设为不再提醒 · 明天进入时段自动恢复（托盘里可点「恢复今天的提醒」）"
        if self.pause_until and now < self.pause_until:
            return "已暂停 · %s 自动恢复提醒" % self.pause_until.strftime("%H:%M")
        active, reason = core.is_active(self.cfg, now)
        interval = self.cfg.get("intervalMinutes", 60)
        if not active:
            return "现在不打扰（%s）· 进入时段的第一分钟先弹一杯" % reason
        if self.next_at is None:
            return "提醒中 · 下一杯约 %s（每 %d 分钟）" % (
                (now + dt.timedelta(minutes=interval)).strftime("%H:%M"), interval)
        if self.next_at <= now:
            return "提醒中 · 下一杯就是现在（%s）" % now.strftime("%H:%M")
        return "提醒中 · 下一杯 %s（每 %d 分钟）" % (
            self.next_at.strftime("%H:%M"), interval)

    def refresh_tray(self):
        if self.tray:
            self.tray.update(tooltip=self.tooltip_text())
            self.tray.set_menu(self.tray_menu())
        if self.settings_win and self.settings_win.winfo_exists():
            try:
                self.settings_win.refresh()
            except Exception:
                pass

    def check(self):
        now = dt.datetime.now()
        today = now.strftime("%Y-%m-%d")

        if self.skip_date == today:
            return
        if self.pause_until and now < self.pause_until:
            return
        if self.pause_until and now >= self.pause_until:
            self.pause_until = None
            self.next_at = None
            self._active_now = False   # 暂停结束算重新进入时段，立刻补一杯
            self.refresh_tray()   # 托盘文字要从"已暂停"变回今日进度

        active, reason = core.is_active(self.cfg, now)
        if not active:
            if self.next_at is not None:
                core.log("离开活跃时段（%s），重置计时" % reason)
            self.next_at = None
            self._active_now = False
            return

        interval = self.cfg.get("intervalMinutes", 60)
        if self.next_at is None:
            if self._active_now:
                # 还在一个连续的工作时段里（改完配置重排），这种"重置"不该吵人
                self.next_at = now + dt.timedelta(minutes=interval)
                core.log("重新计时，%s 后提醒" % self.next_at.strftime("%H:%M"))
            else:
                # 真正"刚进入工作时段"：早上开工、午休结束、暂停到期。
                # 以前这里排的是 now + 一整段间隔，8:30 打卡后要干等到 9:30，
                # 人的感受就是"它根本没提醒"。第一杯立刻提醒，之后按间隔走。
                self.next_at = now
                core.log("进入工作时段，先提醒一次（此后每 %d 分钟）" % interval)
        self._active_now = True

        if now < self.next_at:
            return

        # 到点了
        self.next_at = now + dt.timedelta(minutes=interval)
        total = self.store.total()
        goal = self.cfg.get("dailyGoal", 2000)
        if total >= goal:
            self.celebrate(total, goal)
            return
        self.popup(self.cfg.pick_message(), total, goal,
                   drink_amount=self.cfg.get("amountPerReminder", 250))

    def celebrate(self, total, goal):
        """
        达标庆祝一天只弹一次。
        记录刚满目标时也要当场弹（以前只等下一次定时提醒，可能是一小时后，
        那点成就感早就没了）。
        """
        if self.store.celebrated() or total < goal:
            return False
        self.store.mark_celebrated()
        self.popup(self.cfg.get("goalReachedMessage", "今日目标达成"),
                   total, goal, drink_amount=0, tail="达标了，不用再回我")
        return True

    def popup(self, message, total, goal, drink_amount=0, missable=None, tail=None):
        if self.reminder_win and self.reminder_win.winfo_exists():
            try:
                self.reminder_win.destroy()
            except Exception:
                pass
        self.reminder_win = None
        if missable is None:
            missable = bool(drink_amount)   # 达标庆祝那种不用追提醒
        self.reminder_win = ReminderWindow(
            self.root,
            self,
            message,
            total,
            goal,
            drink_amount or self.cfg.get("amountPerReminder", 250),
            self.cfg.get("snoozeMinutes", 15),
            missable=missable,
            tail=tail,
        )
        # 人不在屏幕前时只有视觉提醒必然漏；声音用系统提示音，异步播放不卡主循环，
        # 设置里能关。故意放在建窗之后：窗没弹出来就别响得莫名其妙。
        if self.cfg.get("sound", True):
            win32ext.play_cue()
        core.log("弹出提醒：%s（今日 %d ml）" % (core.strip_emoji(message), total))

    def show_reminder_now(self):
        """
        手动测试 / 立刻提醒。
        这种"我自己点出来看看"的窗不算没看见：穆总点完测试提醒去倒水，
        窗 30 秒自收，再被追一发 15 分钟后的提醒，只会莫名其妙。
        """
        total = self.store.total()
        goal = self.cfg.get("dailyGoal", 2000)
        self.popup(self.cfg.pick_message(), total, goal,
                   self.cfg.get("amountPerReminder", 250), missable=False,
                   tail="这条是手动测试，不追提醒")

    # ------------------------------------------------------------ 动作
    def record_drink(self, amount):
        total = self.store.add(amount)
        self._miss_streak = 0
        core.log("记录 +%d ml，今日累计 %d ml" % (amount, total))
        self.refresh_tray()
        # 达到目标当场就庆祝，别等下一次定时提醒（那可能是一小时后）
        self.root.after(220, lambda: self.celebrate(total, self.cfg.get("dailyGoal", 2000)))
        return total

    def snooze(self, minutes):
        self.next_at = dt.datetime.now() + dt.timedelta(minutes=minutes)
        active, reason = core.is_active(self.cfg, self.next_at)
        core.log("稍后提醒：%s%s" % (
            self.next_at.strftime("%H:%M"),
            "" if active else "（到点若不在提醒时段会顺延，原因：%s）" % reason))
        self.refresh_tray()

    def skip_today(self):
        self.skip_date = dt.datetime.now().strftime("%Y-%m-%d")
        self._miss_streak = 0
        self.next_at = None
        core.log("今天不再提醒")
        self.refresh_tray()   # 托盘文字/菜单要立刻显示"今天已设为不再提醒"

    def on_reminder_missed(self, minutes):
        """弹窗自己超时收起了 = 人不在屏幕前，这次提醒等于没发生。"""
        self._miss_streak += 1
        if self._miss_streak > MISSED_LIMIT:
            core.log("提醒连续 %d 次没人响应，回归正常间隔，不再追提醒"
                     % self._miss_streak)
            self._miss_streak = 0
            self.refresh_tray()
            return
        core.log("提醒没人响应（自动收起），追一杯：%d 分钟后再提醒" % minutes)
        self.snooze(minutes)

    def on_reminder_answered(self, _win=None):
        """用户点了按钮或手动 ×，说明这条提醒他看见了。"""
        self._miss_streak = 0

    def on_reminder_closed(self, win=None):
        """
        只在自己就是当前窗时才清引用。
        点"喝了"时旧窗关闭、同一次调用链里可能已经弹出达标窗，
        无条件清掉会把新窗的引用抹了 —— 下一条提醒就会和它叠在一起。
        """
        if win is None or self.reminder_win is win:
            self.reminder_win = None
        self.refresh_tray()

    # ------------------------------------------------------------ 设置窗口
    def open_settings(self):
        """
        用户主动点托盘 / 双击 / 第二实例唤起时走这里，所以要把窗口真正带到前台
        并拿到键盘焦点 —— 无边框窗口不进 Alt-Tab，抢不到焦点就成了"看得见但打不了字"。
        提醒弹窗不这么做，见 ui.ReminderWindow。
        """
        if self.settings_win and self.settings_win.winfo_exists():
            try:
                self.settings_win.deiconify()
                self.settings_win.keep_in_view()   # 可能被拖到屏幕外
                self.settings_win.lift()
                self.settings_win.refresh()
                win32ext.set_topmost(self.settings_win.hwnd, True)
                self._activate_later(self.settings_win, delay=30)
                self.root.after(400, lambda: self._untopmost(self.settings_win))
                return
            except Exception:
                report_exception("打开设置窗口")
                self.settings_win = None
        try:
            self.settings_win = SettingsWindow(self.root, self)
        except Exception:
            report_exception("创建设置窗口")
            self.show_hint("设置窗口打不开，详情看 run.log", seconds=6)
            return
        self._activate_later(self.settings_win)

    def _activate_later(self, win, delay=120):
        """
        带前台要等 Tk 把窗口 map 完。
        刚 new 出来的无边框窗口还没落位，这时直接 ShowWindow 会把它钉在 (0,0)，
        居中请求等于白设（实测：改完主题重建后整扇窗口跑到左上角）。
        """
        def bring_front():
            try:
                if win.winfo_exists():
                    # Tk 层的 focus_force 先给上：无边框窗口不在 Alt-Tab 里，
                    # 系统不肯把前台给一个后台进程时（SetForegroundWindow 会失败），
                    # 至少这个窗口自己能收键盘。
                    win.focus_force()
                    win32ext.activate_window(win.hwnd)
            except Exception:
                pass

        self.root.after(delay, bring_front)

    def reopen_settings(self):
        """
        换外观后重建窗口：卡片颜色是开窗时烤进画布的，不重建看不到新配色。
        """
        old, self.settings_win = self.settings_win, None
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        self.open_settings()

    def _untopmost(self, win):
        """弹出来时置顶一下，半秒后归还普通层，免得常驻挡着别的窗口。"""
        try:
            if win.winfo_exists():
                win32ext.set_topmost(win.hwnd, False)
        except Exception:
            pass

    def on_settings_hidden(self):
        self._hide_hint()

    def on_settings_closing(self):
        """× 关掉的窗口会被销毁，这里把引用清掉，下次点托盘重建。"""
        self.settings_win = None
        self._hide_hint()

    def _hide_hint(self):
        """
        收进托盘时说清楚"程序还在跑"。
        这次"到点没提醒"排查下来根因就是进程没在运行，而用户没有任何地方能看出来。
        每次启动只提示一次，免得变成新的打扰。
        """
        if self._hide_hint_shown:
            return
        self._hide_hint_shown = True
        if bool(self.cfg.get("autoStart")):
            self.show_hint("已收进托盘，程序继续在后台提醒", seconds=5)
        else:
            self.show_hint("已收进托盘继续提醒 · 建议打开「开机自启」，"
                           "否则重启电脑后就不会提醒", seconds=8)

    def show_hint(self, text, seconds=6):
        lift = 0
        try:
            if self.reminder_win and self.reminder_win.winfo_exists():
                lift = self.reminder_win.height + 16   # 别叠在提醒弹窗上面
        except Exception:
            pass
        try:
            if self.hint_win and self.hint_win.winfo_exists():
                self.hint_win.destroy()
            self.hint_win = HintWindow(self.root, text, seconds, lift=lift,
                                       cfg=self.cfg)
        except Exception:
            report_exception("提示窗")

    def on_config_saved(self):
        # 自启以注册表为唯一真相，只在两边不一致时才写，避免每次保存都碰注册表
        if bool(self.cfg.get("autoStart")) != bool(win32ext.get_autostart()):
            self.set_autostart(bool(self.cfg.get("autoStart")))
        self.next_at = None  # 让新的间隔重新计时
        self.refresh_tray()
        core.log("配置已保存并生效：%s" % self.status_line())

    def open_data_dir(self):
        try:
            os.startfile(core.DATA_DIR)
        except Exception:
            try:
                subprocess.Popen(["explorer", core.DATA_DIR])
            except Exception:
                report_exception("打开数据目录")

    # ------------------------------------------------------------ 退出
    def quit_from_ui(self):
        """
        退出要点两次。
        这是个常驻工具，手滑一下就没影了，而且从那一刻起再也不会提醒 ——
        代价远大于"多点一次"。第二次点击要在 QUIT_CONFIRM_SECONDS 秒内。
        """
        now = time.time()
        if self._quit_armed and now - self._quit_armed <= QUIT_CONFIRM_SECONDS:
            self._quit_armed = 0.0
            self.quit()
            return
        self._quit_armed = now
        core.log("收到退出请求：等待二次确认")
        try:
            if self.settings_win and self.settings_win.winfo_exists():
                self.settings_win.flash(
                    "再点一次「退出程序」确认退出：退出后今天不会再有任何提醒", warn=True)
            else:
                self.show_hint("再点一次「退出」确认：退出程序后就不会再提醒了",
                               seconds=QUIT_CONFIRM_SECONDS)
        except Exception:
            report_exception("退出确认提示")

    def quit(self):
        if not self._running:
            return            # 二次确认期间可能被连点，别把退出流程跑两遍
        self._running = False
        self._quit_armed = 0.0
        core.log("退出")
        if self.tray:
            self.tray.stop()
        for win in (self.reminder_win, self.settings_win, self.hint_win):
            if win is not None:
                try:
                    if win.winfo_exists():
                        win.destroy()
                except Exception:
                    pass
        self.reminder_win = None
        self.settings_win = None
        self.hint_win = None
        try:
            self.root.after(150, self._destroy)
        except Exception:
            pass

    def _destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass


def print_version():
    print("WaterReminder %s" % core.VERSION)
    print("数据目录：%s（%s）" % (
        core.DATA_DIR, "exe 同目录" if core.BASE_DIR_KIND == "portable" else "回落到用户目录"))
    print("配置文件：%s" % core.CONFIG_PATH)
    print("日志：%s" % core.LOG_PATH)


def print_usage():
    print("喝水提醒 %s —— 只在设定的工作时段弹右下角提醒的常驻小工具" % core.VERSION)
    print("")
    print("用法：WaterReminder.exe [--minimized] [--version] [--help]")
    print("  --minimized  启动后直接收进托盘，不弹设置窗口（开机自启用这个）")
    print("  --version    打印版本号和几个路径")
    print("  --help       本帮助")
    print("")
    print("程序只跑一个实例：已经在跑时再双击，会把设置窗口叫出来。")
    print_version()


def main():
    win32ext.enable_dpi_awareness()  # 必须在创建任何窗口之前

    if "--help" in sys.argv or "-h" in sys.argv or "/?" in sys.argv:
        print_usage()
        return
    if "--version" in sys.argv or "-v" in sys.argv:
        print_version()
        return

    minimized = "--minimized" in sys.argv
    single = win32ext.SingleInstance(INSTANCE_NAME)

    if single.already_running:
        # 已经在跑了就别弹"程序已运行"这种模态框：用户只会顺手点确定，什么也没得到。
        # 直接把正在跑的那个实例的设置窗叫出来，这才是他双击想要的结果。
        if not win32ext.wake_running_instance():
            core.log("第二实例：没能唤起已运行实例的窗口")
        return

    root = tk.Tk()
    root.withdraw()
    install_exception_hooks(root)

    app = App(root)
    try:
        app.start(minimized=minimized)
    except Exception:
        report_exception("启动")
        try:
            root.after(3000, app.quit)   # 起不来就干净退出，别留个没托盘也没窗口的僵尸进程
        except Exception:
            pass

    try:
        root.mainloop()
    finally:
        single.release()


if __name__ == "__main__":
    main()

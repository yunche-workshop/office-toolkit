# -*- coding: utf-8 -*-
"""
main.py —— 入口与主调度

后台只有一个线程在跑托盘消息循环，提醒判断走 Tk 的定时器（每 15 秒一次），
空闲时 CPU 占用接近 0。
"""

import datetime as dt
import os
import queue
import subprocess
import sys
import time
import tkinter as tk

import core
import icon
import win32ext
from ui import HintWindow, ReminderWindow, SettingsWindow

TICK_MS = 15000        # 调度判断的最长间隔
POLL_MS = 500          # 主循环心跳：托盘点击的响应速度由它决定
MISSED_LIMIT = 2       # 弹窗连续几次没人点就不再追着顺延提醒，回归正常间隔
INSTANCE_NAME = "WaterReminder_SingleInstance_v2"

MENU_SETTINGS = 1001
MENU_REMIND = 1002
MENU_DRINK = 1003
MENU_PAUSE = 1004
MENU_EXIT = 1005
MENU_AUTOSTART = 1006
MENU_RESUME = 1007
TRAY_DOUBLE_CLICK = 90001
TRAY_SINGLE_CLICK = 90002


def notify_already_running():
    try:
        ctypes = win32ext.ctypes
        win32ext.user32.MessageBoxW(
            None,
            "喝水提醒已经在运行了。\n在右下角托盘图标上点一下就能打开设置。",
            "喝水提醒",
            0x00000040,  # MB_ICONINFORMATION
        )
    except Exception:
        pass


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

        core.log(
            "启动：配置=%s 数据=%s 图标=%s 亚克力支持=%s"
            % (core.CONFIG_PATH, core.DATA_DIR,
               "随包" if self.icon_bundled else "自算兜底",
               win32ext.windows_build() >= 16299)
        )
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

    # ------------------------------------------------------------ 托盘
    def tooltip_text(self):
        total = self.store.total()
        goal = self.cfg.get("dailyGoal", 2000)
        if self.pause_until and dt.datetime.now() < self.pause_until:
            return "喝水提醒：已暂停到 %s" % self.pause_until.strftime("%H:%M")
        return "喝水提醒：今日 %d / %d ml" % (total, goal)

    def tray_menu(self):
        now = dt.datetime.now()
        paused = bool(self.pause_until and now < self.pause_until)
        return [
            (MENU_SETTINGS, "打开设置"),
            (MENU_REMIND, "立刻提醒一次"),
            (MENU_DRINK, "记一杯 %d ml" % self.cfg.get("amountPerReminder", 250)),
            None,
            (MENU_PAUSE, "暂停 1 小时", paused),
            (MENU_RESUME, "取消暂停", paused),
            (MENU_AUTOSTART, "开机自启", bool(self.cfg.get("autoStart"))),
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
        except Exception as exc:
            core.log("托盘任务处理异常：%s" % exc)

        now = time.time()
        if now >= self._wake:
            try:
                self.check()
            except Exception as exc:
                core.log("tick 异常：%s" % exc)
            wake = now + TICK_MS / 1000.0
            if self.next_at is not None:
                try:
                    wake = min(wake, self.next_at.timestamp())
                except Exception:
                    pass
            self._wake = wake
        self.root.after(POLL_MS, self.loop)

    def handle_command(self, cmd):
        if cmd in (MENU_SETTINGS, TRAY_DOUBLE_CLICK, TRAY_SINGLE_CLICK):
            self.open_settings()
        elif cmd == MENU_REMIND:
            self.show_reminder_now()
        elif cmd == MENU_DRINK:
            self.record_drink(self.cfg.get("amountPerReminder", 250))
        elif cmd == MENU_PAUSE:
            self.pause_until = dt.datetime.now() + dt.timedelta(hours=1)
            self._active_now = False   # 暂停结束后算"重新进入时段"，第一杯不等满间隔
            core.log("暂停 1 小时")
            self.refresh_tray()
        elif cmd == MENU_RESUME:
            self.pause_until = None
            self.next_at = None
            self._active_now = False
            core.log("已取消暂停")
            self.refresh_tray()
        elif cmd == MENU_AUTOSTART:
            want = not bool(self.cfg.get("autoStart"))
            if win32ext.set_autostart(want):
                self.cfg.data["autoStart"] = want
                self.cfg.save()
            core.log("开机自启：%s" % ("开" if want else "关"))
            self.refresh_tray()
            if self.settings_win and self.settings_win.winfo_exists():
                try:
                    self.settings_win.toggles["autoStart"].value = want
                    self.settings_win.toggles["autoStart"].render()
                    self.settings_win.update_alive_hint()
                except Exception:
                    pass
        elif cmd == MENU_EXIT:
            self.quit()

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
            if not self.store.celebrated():
                self.store.mark_celebrated()
                self.popup(
                    self.cfg.get("goalReachedMessage", "今日目标达成"),
                    total,
                    goal,
                    drink_amount=0,
                )
            return
        self.popup(self.cfg.pick_message(), total, goal,
                   drink_amount=self.cfg.get("amountPerReminder", 250))

    def popup(self, message, total, goal, drink_amount=0, missable=None):
        if self.reminder_win and self.reminder_win.winfo_exists():
            try:
                self.reminder_win.destroy()
            except Exception:
                pass
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
        )
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
                   self.cfg.get("amountPerReminder", 250), missable=False)

    # ------------------------------------------------------------ 动作
    def record_drink(self, amount):
        total = self.store.add(amount)
        self._miss_streak = 0
        core.log("记录 +%d ml，今日累计 %d ml" % (amount, total))
        self.refresh_tray()
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
        core.log("今天不再提醒")

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

    def on_reminder_closed(self, _win=None):
        self.reminder_win = None
        self.refresh_tray()

    # ------------------------------------------------------------ 设置窗口
    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            try:
                self.settings_win.deiconify()
                self.settings_win.keep_in_view()   # 可能被拖到屏幕外
                self.settings_win.lift()
                self.settings_win.refresh()
                win32ext.set_topmost(self.settings_win.hwnd, True)
                self.root.after(400, lambda: self._untopmost(self.settings_win))
                return
            except Exception:
                self.settings_win = None
        self.settings_win = SettingsWindow(self.root, self)

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
            self.hint_win = HintWindow(self.root, text, seconds, lift=lift)
        except Exception as exc:
            core.log("提示窗创建失败：%s" % exc)

    def on_config_saved(self):
        want = bool(self.cfg.get("autoStart"))
        win32ext.set_autostart(want)
        self.next_at = None  # 让新的间隔重新计时
        self.refresh_tray()
        core.log("配置已保存并生效")

    def open_data_dir(self):
        try:
            os.startfile(core.DATA_DIR)
        except Exception:
            try:
                subprocess.Popen(["explorer", core.DATA_DIR])
            except Exception:
                pass

    # ------------------------------------------------------------ 退出
    def quit(self):
        self._running = False
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


def main():
    win32ext.enable_dpi_awareness()  # 必须在创建任何窗口之前
    single = win32ext.SingleInstance(INSTANCE_NAME)
    minimized = "--minimized" in sys.argv

    root = tk.Tk()
    root.withdraw()

    if single.already_running:
        notify_already_running()
        root.destroy()
        return

    app = App(root)
    app.start(minimized=minimized)

    try:
        root.mainloop()
    finally:
        single.release()


if __name__ == "__main__":
    main()

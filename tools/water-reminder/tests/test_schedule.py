# -*- coding: utf-8 -*-
"""
test_schedule.py —— 用假时钟把 App 的调度逻辑走一遍（纯逻辑，不建窗口）

为什么要这份：穆总反馈"8:30 到公司一小时都没弹"，那次只能靠人肉守屏幕复现。
调度是这工具唯一的价值，也是最容易改坏又最难发现的部分，所以钉成断言：

  1) 刚进入活跃时段（早上开工、午休结束、暂停到期）第一次提醒不等满间隔，立刻弹；
  2) 弹窗自己超时收起（没人点）= 没看见，自动顺延 snoozeMinutes 再提醒；
     连续 MISSED_LIMIT 次没人响应就回归正常间隔，不追着吵人；
  3) 在同一个连续时段内重置计时（比如存了配置）不许立刻弹，那是吵人。

做法：把 main/core 里的 dt 换成假时钟，用 App 的子类当桩（不跑 App.__init__，
所以一个 Tk 窗口都不建）。ReminderWindow 换成只记参数的假窗，于是 App.check →
App.popup → 弹窗参数、提示音、排期整条链路都是真的代码在跑，只有窗口是假的。

运行：python -m unittest discover -s tests
"""

import datetime as real_dt
import json
import os
import queue
import shutil
import sys
import tempfile
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import core
import main as mainmod
import win32ext

App = mainmod.App

# ---------------------------------------------------------------- 假时钟

START = real_dt.datetime(2026, 10, 8, 9, 0)      # 周四早上九点
CURRENT = {"now": START}


class _FakeDateTime(real_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return CURRENT["now"]


_dt_module = types.ModuleType("fake_dt")
_dt_module.datetime = _FakeDateTime
_dt_module.date = real_dt.date
_dt_module.timedelta = real_dt.timedelta
_dt_module.timezone = real_dt.timezone

REAL_DT = (core.dt, mainmod.dt)


class FakeReminder(object):
    """参数形状和 ui.ReminderWindow.__init__ 保持一致；漂移了这里会先炸。"""

    def __init__(self, master, app, title, total, goal, amount, snooze,
                 missable=True, tail=None):
        app.popups.append({
            "at": CURRENT["now"].strftime("%H:%M"), "msg": title,
            "total": total, "goal": goal, "amount": amount,
            "snooze": snooze, "missable": missable, "tail": tail,
        })
        self.master, self.app = master, app
        self.alive = True

    def winfo_exists(self):
        return self.alive

    def destroy(self):
        self.alive = False


class Stub(App):
    """
    App 的桩：属性照 App.__init__ 摆齐，但不建窗口、不起托盘、不连主循环。

    root 是假的 —— record_drink 会 self.root.after(220, ...) 排一次达标庆祝，
    桩把回调收进 scheduled，测试自己决定要不要"过 220 毫秒"。
    """

    class _Root(object):
        def __init__(self):
            self.scheduled = []

        def after(self, ms, func=None, *args):
            self.scheduled.append((ms, func))

        def fire(self):
            todo, self.scheduled = self.scheduled, []
            for _ms, func in todo:
                if func is not None:
                    func()

    def __init__(self, cfg, store):
        self.root = self._Root()
        self.cfg, self.store = cfg, store
        self.tray = None
        self.settings_win = None
        self.reminder_win = None
        self.hint_win = None
        self.popups = []
        self.hints = []
        self.skip_date = None
        self.pause_until = None
        self.next_at = None
        self._active_now = False
        self._miss_streak = 0
        # App.__init__ 里剩下的那几个状态，用到哪条方法就得摆哪条，缺一个就是假失败
        self._running = True
        self._quit_armed = 0.0
        self._wake = 0.0
        self._hide_hint_shown = False
        self.tasks = queue.Queue()

    def refresh_tray(self):
        pass

    def show_hint(self, text, seconds=6, **kw):
        self.hints.append(text)

    def run_checks(self):
        """把 root.after 排下去的活儿干完，模拟 Tk 主循环真的走了一轮。"""
        self.root.fire()


class GuiLessCase(unittest.TestCase):
    """不建窗口、不起托盘的一族用例共用的假时钟 + 临时目录。"""

    def setUp(self):
        CURRENT["now"] = START                     # 每条用例都从周四 09:00 开始
        self.tmp = tempfile.mkdtemp(prefix="wr_sched_")
        core.ROOT = self.tmp
        core.DATA_DIR = os.path.join(self.tmp, "data")
        core.CONFIG_PATH = os.path.join(self.tmp, "config.json")
        core.LOG_PATH = os.path.join(self.tmp, "run.log")
        core.dt = _dt_module
        mainmod.dt = _dt_module
        self._real_win = mainmod.ReminderWindow
        mainmod.ReminderWindow = FakeReminder
        # 提示音在无人值守时会走 MessageBeep，桩掉并记下响了几次
        self.cues = []
        self._real_cue = mainmod.win32ext.play_cue
        mainmod.win32ext.play_cue = lambda: self.cues.append(
            CURRENT["now"].strftime("%H:%M"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tearDown(self):
        mainmod.ReminderWindow = self._real_win
        mainmod.win32ext.play_cue = self._real_cue
        core.dt, mainmod.dt = REAL_DT

    # ------------------------------------------------------------ 造场景

    def app(self, **over):
        self._seq = getattr(self, "_seq", 0) + 1
        base = dict(core.DEFAULT_CONFIG, **{
            "workStart": "08:30", "workEnd": "17:30",
            "intervalMinutes": 60, "amountPerReminder": 250,
            "dailyGoal": 2000, "snoozeMinutes": 15,
            "lunchBreak": {"enabled": True, "start": "12:00", "end": "13:00"},
            "workdaysOnly": False,
            "messages": ["测试文案"],
        })
        base.update(over)
        path = os.path.join(self.tmp, "cfg%d.json" % self._seq)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(base, fh, ensure_ascii=False)
        return Stub(core.Config(path),
                    core.Store(os.path.join(self.tmp, "data%d" % self._seq)))

    def at(self, h, m):
        CURRENT["now"] = CURRENT["now"].replace(hour=h, minute=m)

    def on(self, y, mo, d, h, m):
        CURRENT["now"] = real_dt.datetime(y, mo, d, h, m)

    def times(self, s):
        return [p["at"] for p in s.popups]

    # ------------------------------------------------------------ 用例


class SchedCase(GuiLessCase):
    def test_first_pop_is_immediate_then_interval(self):
        """进时段先弹一杯，之后按间隔走 —— 别再退回"8:30 打卡等到 9:30"。"""
        a = self.app()
        a.check()
        self.assertEqual(self.times(a), ["09:00"])
        self.assertEqual(a.next_at.strftime("%H:%M"), "10:00")
        self.at(9, 59)
        a.check()
        self.assertEqual(len(a.popups), 1)
        self.at(10, 0)
        a.check()
        self.at(11, 0)
        a.check()
        self.assertEqual(self.times(a), ["09:00", "10:00", "11:00"])
        self.assertEqual(a.popups[0]["amount"], 250, "提醒的就是设置里那一杯的量")
        self.assertEqual(a.popups[0]["snooze"], 15)

    def test_lunch_break_then_catch_up(self):
        """午休内不弹，出午休立刻补一杯（以前要干等满一个间隔）。"""
        b = self.app()
        self.at(11, 30)
        b.check()
        self.assertEqual(len(b.popups), 1, "11:30 进时段先弹一次")
        self.at(12, 30)
        b.check()
        self.assertEqual(len(b.popups), 1, "午休内不弹")
        self.assertIsNone(b.next_at, "不活跃就重置计时")
        self.at(13, 0)
        b.check()
        self.assertEqual(len(b.popups), 1, "13:00 整仍算午休（两端闭区间）")
        self.at(13, 1)
        b.check()
        self.assertEqual(len(b.popups), 2, "13:01 出午休立刻补弹")
        self.assertEqual(b.next_at.strftime("%H:%M"), "14:01")
        self.at(14, 1)
        b.check()
        self.assertEqual(len(b.popups), 3)

    def test_snooze_pops_exactly_then(self):
        c = self.app()
        self.at(10, 0)
        c.check()
        self.at(11, 0)
        c.check()
        c.snooze(15)                              # 用户在弹窗上点「15 分钟后」
        self.assertEqual(c.next_at.strftime("%H:%M"), "11:15")
        n = len(c.popups)
        self.at(11, 14)
        c.check()
        self.assertEqual(len(c.popups), n, "早一分钟都不弹")
        self.at(11, 15)
        c.check()
        self.assertEqual(len(c.popups), n + 1, "11:15 准点弹出")

    def test_skip_today_quiets_the_day(self):
        d = self.app()
        d.check()
        d.skip_today()
        for hh in (10, 11, 14, 15, 16):
            self.at(hh, 0)
            d.check()
        self.assertEqual(len(d.popups), 1, "「今天不提醒」之后全天安静")

    def test_outside_window_never_pops(self):
        e = self.app()
        for hh, mm in ((18, 30), (7, 0), (12, 10)):
            self.at(hh, mm)
            e.check()
            self.assertEqual(len(e.popups), 0)
            self.assertIsNone(e.next_at, "不活跃时不登记，免得回来时按旧点弹")

    def test_workdays_only(self):
        f = self.app(workdaysOnly=True)
        self.on(2026, 10, 10, 9, 0)               # 周六
        for hh in (9, 10, 11):
            self.at(hh, 0)
            f.check()
        self.assertEqual(len(f.popups), 0, "勾选只在周一至周五 → 周六全天不弹")
        g = self.app(workdaysOnly=False)
        self.on(2026, 10, 10, 9, 0)
        g.check()
        self.assertEqual(len(g.popups), 1)
        self.at(10, 0)
        g.check()
        self.assertEqual(len(g.popups), 2, "没勾选 → 周六照弹")

    def test_celebrate_once(self):
        """达到目标只庆祝一次，之后不再打扰，也不该追提醒。"""
        h = self.app()
        for _ in range(8):
            h.store.add(250, now=real_dt.datetime(2026, 10, 8, 9, 0))
        h.check()
        self.assertEqual(len(h.popups), 1, "9:00 进时段直接庆祝")
        self.assertGreaterEqual(h.popups[0]["total"], 2000)
        self.assertEqual(h.popups[0]["tail"], "达标了，不用再回我")
        self.assertIs(h.popups[0]["missable"], False)
        for hh in (10, 11, 12):
            self.at(hh, 0)
            h.check()
        self.assertEqual(len(h.popups), 1, "庆祝只一次，之后不再弹")

    def test_pause_expires_catches_up(self):
        i = self.app()
        i.check()
        i.pause_until = real_dt.datetime(2026, 10, 8, 10, 0)
        i._active_now = False                     # 与托盘「暂停 1 小时」一致
        self.at(9, 30)
        i.check()
        self.assertEqual(len(i.popups), 1, "暂停期间到点也不弹")
        self.at(10, 0)
        i.check()
        self.assertEqual(len(i.popups), 2, "暂停解除立刻补一杯")
        self.assertEqual(i.next_at.strftime("%H:%M"), "11:00")
        self.at(10, 30)
        i.check()
        self.assertEqual(len(i.popups), 2)
        self.at(11, 0)
        i.check()
        self.assertEqual(len(i.popups), 3)

    def test_retimed_inside_window_does_not_pop(self):
        """同一个连续时段里重排计时（存配置）不许立刻弹。"""
        j = self.app()
        j.check()
        self.at(9, 20)
        j.check()
        j.next_at = None                          # on_config_saved 就是这么重置的
        popped = len(j.popups)
        j.check()
        self.assertEqual(len(j.popups), popped, "不弹，只按新间隔登记")
        self.assertEqual(j.next_at.strftime("%H:%M"), "10:20")

    def test_missed_popup_snoozes_then_gives_up(self):
        """没人点自动收起 → 顺延一次；连续几次没人响应就回归正常间隔。"""
        k = self.app()
        k.check()
        k.on_reminder_missed(15)
        self.assertEqual(k.next_at.strftime("%H:%M"), "09:15")
        self.assertEqual(k._miss_streak, 1)
        self.at(9, 15)
        k.check()
        self.assertEqual(len(k.popups), 2, "9:15 追补一次")
        k.on_reminder_missed(15)
        self.assertEqual(k.next_at.strftime("%H:%M"), "09:30")
        self.assertEqual(k._miss_streak, 2)
        self.at(9, 30)
        k.check()
        k.on_reminder_missed(15)
        self.assertEqual(k.next_at.strftime("%H:%M"), "10:30", "超过上限不再追")
        self.assertEqual(k._miss_streak, 0)
        self.at(10, 0)
        k.check()
        self.assertEqual(k.next_at.strftime("%H:%M"), "10:30")
        self.at(10, 30)
        k.check()
        self.assertEqual(len(k.popups), 4)

    def test_answering_resets_miss_streak(self):
        m = self.app()
        m._miss_streak = 1
        m.on_reminder_answered()
        self.assertEqual(m._miss_streak, 0)
        m._miss_streak = 1
        m.record_drink(250)
        self.assertEqual(m._miss_streak, 0, "记一杯也算响应")

    def test_celebrate_fires_right_after_recording(self):
        """记录刚好满目标时当场庆祝，不等下一次定时提醒（那可能是一小时后）。"""
        n = self.app()
        for _ in range(7):
            n.store.add(250)
        total = n.record_drink(250)
        self.assertEqual(total, 2000)
        self.assertEqual(len(n.popups), 0, "庆祝走的是 after 回调，不是同步弹")
        n.run_checks()
        self.assertEqual(len(n.popups), 1, "过一轮主循环就弹庆祝")
        self.assertEqual(n.popups[0]["tail"], "达标了，不用再回我")
        n.run_checks()
        self.assertEqual(len(n.popups), 1, "庆祝只弹一次")
        self.assertEqual(n.store.total(), 2000)

    def test_sound_flag_controls_cue(self):
        o = self.app()
        o.check()
        self.assertEqual(self.cues, ["09:00"], "默认开着声音")
        p = self.app(sound=False)
        p.check()
        self.assertEqual(len(p.popups), 1, "窗照样弹")
        self.assertEqual(len(self.cues), 1, "关掉提示音就不该再响")

    def test_show_reminder_now_does_not_reschedule(self):
        """手动「测试提醒」：不追提醒，也不许把排期搅了。"""
        q = self.app()
        q.check()                                 # 排期 10:00
        before = q.next_at
        q.show_reminder_now()
        self.assertEqual(len(q.popups), 2)
        self.assertIs(q.popups[1]["missable"], False)
        self.assertEqual(q.popups[1]["tail"], "这条是手动测试，不追提醒")
        self.assertEqual(q.next_at, before, "测试不该改排期")

    def test_new_popup_replaces_the_old_window(self):
        """上一条还开着时又来提醒：先关旧窗，别叠两层。"""
        r = self.app()
        r.popup("第一条", 0, 2000, drink_amount=250)
        first = r.reminder_win
        r.popup("第二条", 250, 2000, drink_amount=250)
        self.assertIs(first.alive, False, "旧窗要真的关掉")
        self.assertIsNot(r.reminder_win, first)
        self.assertEqual([p["msg"] for p in r.popups], ["第一条", "第二条"])

    def test_reminder_win_reference_only_cleared_by_owner(self):
        """点「喝了」时旧窗关闭、同一次调用链已弹新窗：别把新窗引用抹了。"""
        s = self.app()
        sentinel = object()
        s.reminder_win = sentinel
        s.on_reminder_closed(object())
        self.assertIs(s.reminder_win, sentinel, "不是自己就不能清")
        s.on_reminder_closed(sentinel)
        self.assertIsNone(s.reminder_win)
        s.reminder_win = sentinel
        s.on_reminder_closed()
        self.assertIsNone(s.reminder_win, "窗口自己没了（被销毁）时直接清")

    def test_status_line_states(self):
        """状态行给界面和日志用，得说清"现在为什么不弹"。"""
        t = self.app()
        t.check()
        self.assertIn("提醒中", t.status_line())
        self.assertIn("10:00", t.status_line())
        t.pause_until = real_dt.datetime(2026, 10, 8, 10, 0)
        self.assertIn("已暂停", t.status_line())
        t.pause_until = None
        t.skip_today()
        self.assertIn("不再提醒", t.status_line())
        t.skip_date = None                        # 第二天（托盘里点「恢复今天的提醒」）
        self.at(20, 0)
        self.assertIn("不在工作时段", t.status_line())
        t.skip_date = CURRENT["now"].strftime("%Y-%m-%d")
        self.at(9, 0)
        self.assertIn("不再提醒", t.status_line(), "skip 的判断要排在时段之前")

    def test_messages_do_not_repeat_back_to_back(self):
        """文案洗牌袋：一天十来次提醒里不该连着两句一模一样。"""
        u = self.app(messages=["甲", "乙", "丙"])
        seen = []
        for hh in range(9, 17):
            if hh == 12:
                continue
            self.at(hh, 0)
            u.check()
        seen = [p["msg"] for p in u.popups]
        self.assertGreaterEqual(len(seen), 5)
        self.assertEqual([i for i in range(1, len(seen)) if seen[i] == seen[i - 1]],
                         [], "连续两次同文案：%s" % seen)


class ActionCase(GuiLessCase):
    """
    托盘菜单、撤销、二次确认退出这些"用户手点"的通路。
    和调度分开测：调度错了是"不提醒"，这些错了是"点一下把今天的记录搞脏"。
    """

    def setUp(self):
        GuiLessCase.setUp(self)
        # 自启写的是注册表 HKCU\...\Run，单测里绝对不能碰真机器
        self.autostart_calls = []
        self._real_autostart = mainmod.win32ext.set_autostart
        mainmod.win32ext.set_autostart = self._fake_autostart

    def tearDown(self):
        mainmod.win32ext.set_autostart = self._real_autostart
        GuiLessCase.tearDown(self)

    def _fake_autostart(self, want):
        self.autostart_calls.append(want)

    def labels(self, s):
        return [item[1] for item in s.tray_menu() if item]

    def test_menu_labels_say_what_they_do(self):
        s = self.app()
        self.assertIn("暂停 1 小时", self.labels(s))
        self.assertIn("今天不再提醒", self.labels(s))
        s.pause_until = CURRENT["now"] + real_dt.timedelta(minutes=30)
        self.assertIn("取消暂停", self.labels(s), "正在暂停时这一条必须是取消")
        s.pause_until = None
        s.skip_today()
        self.assertIn("恢复今天的提醒", self.labels(s), "已经静音时这一条必须是恢复")

    def test_menu_is_stable_shape(self):
        """分隔线 + 末项固定：末项永远是"退出"，不能因为状态变化换位。"""
        s = self.app()
        menu = s.tray_menu()
        self.assertEqual([i for i, item in enumerate(menu) if item is None], [4, 8])
        self.assertEqual(menu[-1], (mainmod.MENU_EXIT, "退出"))
        self.assertIn("记一杯 250 ml", self.labels(s))
        s.cfg.data["unit"] = "oz"
        self.assertIn("记一杯 8.5 oz", self.labels(s), "菜单里的量按用户单位写")

    def test_undo_last(self):
        s = self.app()
        s.record_drink(250)
        s.record_drink(250)
        self.assertEqual(s.store.total(), 500)
        s.undo_last()
        self.assertEqual(s.store.total(), 250)
        self.assertEqual(len(s.hints), 1)
        self.assertIn("已撤销", s.hints[0])
        s.undo_last()
        self.assertEqual(s.store.total(), 0)
        s.undo_last()
        self.assertIn("还没有可撤销", s.hints[-1], "没东西可撤时要说清楚")

    def test_undo_clears_celebrated_flag(self):
        """撤回到目标以下，达标庆祝要能重新弹，否则今天再也没有庆祝。"""
        s = self.app()
        for _ in range(8):
            s.store.add(250)
        s.store.mark_celebrated()
        s.undo_last()
        self.assertLess(s.store.total(), 2000)
        self.assertFalse(s.store.celebrated())
        s.check()
        self.assertEqual(len(s.popups), 1, "撤销后还能重新提醒")
        self.assertIs(s.popups[0]["missable"], True)

    def test_quit_needs_two_clicks(self):
        s = self.app()
        s.quit_from_ui()
        self.assertTrue(s._running, "第一次点退出只提示，不真退")
        self.assertEqual(len(s.hints), 1)
        s.quit_from_ui()
        self.assertFalse(s._running, "确认窗口内第二下才算数")

    def test_quit_arm_expires(self):
        s = self.app()
        s.quit_from_ui()
        s._quit_armed = time.time() - mainmod.QUIT_CONFIRM_SECONDS - 1
        s.quit_from_ui()
        self.assertTrue(s._running, "隔了 8 秒再点，算新的一次")

    def test_handle_command_routes(self):
        s = self.app()
        s.handle_command(mainmod.MENU_DRINK)
        self.assertEqual(s.store.total(), 250)
        s.handle_command(mainmod.MENU_UNDO)
        self.assertEqual(s.store.total(), 0)
        s.handle_command(mainmod.MENU_PAUSE)
        self.assertTrue(s.pause_until and s.pause_until > CURRENT["now"])
        self.assertFalse(s._active_now, "暂停结束后要算重新进时段")
        s.handle_command(mainmod.MENU_PAUSE)
        self.assertIsNone(s.pause_until)
        s.handle_command(mainmod.MENU_SKIP)
        self.assertEqual(s.skip_date, CURRENT["now"].strftime("%Y-%m-%d"))
        s.handle_command(mainmod.MENU_SKIP)
        self.assertIsNone(s.skip_date)

    def test_activate_message_opens_settings(self):
        """第二实例靠 TRAY_ACTIVATE 把设置窗叫出来，四种点击码都得有通路。"""
        s = self.app()
        opened = []
        s.open_settings = lambda: opened.append(1)
        for code in (mainmod.MENU_SETTINGS, mainmod.TRAY_DOUBLE_CLICK,
                     mainmod.TRAY_SINGLE_CLICK, mainmod.TRAY_ACTIVATE):
            s.handle_command(code)
        self.assertEqual(len(opened), 4)
        self.assertEqual(mainmod.TRAY_ACTIVATE, win32ext.TRAY_ACTIVATE)

    def test_tooltip_reports_why_it_is_quiet(self):
        s = self.app()
        s.check()
        self.assertIn("下一杯", s.tooltip_text())
        s.pause_until = CURRENT["now"] + real_dt.timedelta(minutes=30)
        self.assertIn("已暂停到", s.tooltip_text())
        s.pause_until = None
        s.skip_today()
        self.assertIn("今天已设为不再提醒", s.tooltip_text())
        self.at(20, 0)
        s.skip_date = None
        self.assertIn("现在不打扰", s.tooltip_text())
        self.assertIn("不在工作时段", s.tooltip_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)

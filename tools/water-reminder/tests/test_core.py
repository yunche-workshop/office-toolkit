# -*- coding: utf-8 -*-
"""
test_core.py —— 数据/配置/时间判断的单元测试（纯标准库，不开窗口）

跑法（在项目根目录）：
    python -m unittest discover -s tests -v

覆盖的都是真出过问题的地方：
  * 手改 config.json 把上班时间写成 "25:00" → 以前整天不提醒且不吭声
  * 老配置里 weekendsOnly 键名反了 → 加载时要迁移并保留语义
  * 跨夜班次（22:00 - 06:00）以前会被判成"永远不活跃"
  * 配置文件写坏 → 要备份原件再落回默认，不能静默覆盖
  * 提醒文案连着两次重样
"""

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import core  # noqa: E402


class TempCase(unittest.TestCase):
    """每个用例一个临时目录，测完删掉；日志也别写到真实数据目录里。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wr_test_")
        self._log = core.LOG_PATH
        core.LOG_PATH = os.path.join(self.tmp, "run.log")

    def tearDown(self):
        core.LOG_PATH = self._log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def write_config(self, text):
        p = self.path("config.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p


# ---------------------------------------------------------------- 时间字段


class TestTimeFields(unittest.TestCase):
    def test_valid_hm_pads(self):
        self.assertEqual(core.valid_hm("9:30"), "09:30")
        self.assertEqual(core.valid_hm("9:5"), "09:05")      # 不补零也算合法
        self.assertEqual(core.valid_hm(" 18:00 "), "18:00")
        self.assertEqual(core.valid_hm("00:00"), "00:00")
        self.assertEqual(core.valid_hm("23:59"), "23:59")

    def test_valid_hm_rejects_garbage(self):
        for bad in ("25:00", "18:99", "-1:00", "abc", "9", "", None, "9:60", "0900"):
            self.assertIsNone(core.valid_hm(bad), "应该判非法：%r" % (bad,))

    def test_parse_hm_fallback(self):
        self.assertEqual(core.parse_hm("09:30"), 570)
        self.assertEqual(core.parse_hm("25:00", 999), 999)


class TestInWindow(unittest.TestCase):
    def test_normal_window(self):
        self.assertTrue(core.in_window(600, 540, 1080))
        self.assertFalse(core.in_window(500, 540, 1080))
        self.assertTrue(core.in_window(540, 540, 1080))     # 两端闭区间
        self.assertTrue(core.in_window(1080, 540, 1080))

    def test_overnight_window(self):
        """22:00 - 06:00 这种夜班：跨零点也算在时段内。"""
        self.assertTrue(core.in_window(22 * 60, 22 * 60, 6 * 60))
        self.assertTrue(core.in_window(2 * 60, 22 * 60, 6 * 60))
        self.assertTrue(core.in_window(5 * 60 + 59, 22 * 60, 6 * 60))
        self.assertFalse(core.in_window(12 * 60, 22 * 60, 6 * 60))


# ---------------------------------------------------------------- 配置


class TestConfig(TempCase):
    def config_with(self, **over):
        data = dict(core.DEFAULT_CONFIG)
        data.update(over)
        p = self.write_config(json.dumps(data))
        return core.Config(p)

    def test_bad_time_falls_back_and_reports(self):
        cfg = self.config_with(workStart="25:00", workEnd="18:99")
        self.assertEqual(cfg.get("workStart"), "09:00")
        self.assertEqual(cfg.get("workEnd"), "18:00")
        keys = [k for k, _r, _e in cfg.clamp_report()]
        self.assertIn("workStart", keys)
        self.assertIn("workEnd", keys)

    def test_empty_time_falls_back(self):
        cfg = self.config_with(workStart="")
        self.assertEqual(cfg.get("workStart"), "09:00")
        self.assertTrue(cfg.clamp_report())

    def test_lunch_time_keys_are_labelled(self):
        cfg = self.config_with(lunchBreak={"enabled": True, "start": "26:00",
                                           "end": "13:00"})
        labels = [k for k, _r, _e in cfg.clamp_report()]
        self.assertIn("lunchStart", labels)     # 不能报成含糊的 "start"

    def test_int_rules_clamp_and_report(self):
        cfg = self.config_with(intervalMinutes=99999, dailyGoal="abc",
                               amountPerReminder=1, snoozeMinutes=0)
        self.assertEqual(cfg.get("intervalMinutes"), 480)
        self.assertEqual(cfg.get("dailyGoal"), 2000)      # 非整数回落默认
        self.assertEqual(cfg.get("amountPerReminder"), 50)
        self.assertEqual(cfg.get("snoozeMinutes"), 5)
        self.assertEqual(len(cfg.clamp_report()), 4)

    def test_legacy_weekends_only_migrates(self):
        p = self.write_config('{"weekendsOnly": true, "intervalMinutes": 60}')
        cfg = core.Config(p)
        self.assertIs(cfg.get("workdaysOnly"), True)
        self.assertNotIn("weekendsOnly", cfg.data)

    def test_new_key_wins_over_legacy(self):
        p = self.write_config('{"weekendsOnly": true, "workdaysOnly": false}')
        cfg = core.Config(p)
        self.assertIs(cfg.get("workdaysOnly"), False)

    def test_unknown_theme_and_unit_reset(self):
        cfg = self.config_with(theme="pink", unit="liter")
        self.assertEqual(cfg.get("theme"), "auto")
        self.assertEqual(cfg.get("unit"), "ml")

    def test_empty_messages_use_defaults(self):
        cfg = self.config_with(messages=["", "   ", "有效的文案"])
        self.assertEqual(cfg.get("messages"), ["有效的文案"])
        cfg2 = self.config_with(messages=[])
        self.assertEqual(cfg2.get("messages"), list(core.DEFAULT_MESSAGES))

    def test_corrupt_file_is_backed_up_not_overwritten(self):
        p = self.write_config("{ this is not json ")
        cfg = core.Config(p)
        self.assertTrue(cfg.corrupt_backup)
        self.assertTrue(os.path.exists(cfg.corrupt_backup))
        with open(cfg.corrupt_backup, encoding="utf-8") as fh:
            self.assertIn("not json", fh.read())
        self.assertEqual(cfg.get("intervalMinutes"), 60)

    def test_save_is_atomic_and_complete(self):
        cfg = self.config_with(intervalMinutes=77)
        self.assertTrue(cfg.save())
        self.assertFalse(os.path.exists(cfg.path + ".tmp"))
        with open(cfg.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["intervalMinutes"], 77)

    def test_missing_file_marks_created_new(self):
        cfg = core.Config(self.path("nope.json"))
        self.assertTrue(cfg.created_new)
        self.assertFalse(cfg.corrupt_backup)

    def test_pick_message_no_adjacent_repeat(self):
        cfg = self.config_with(messages=["A", "B", "C"])
        seen = [cfg.pick_message() for _ in range(12)]
        for a, b in zip(seen, seen[1:]):
            self.assertNotEqual(a, b, "文案连着重样：%s" % seen)

    def test_pick_message_single_entry(self):
        cfg = self.config_with(messages=["只有一条"])
        self.assertEqual(cfg.pick_message(), "只有一条")
        self.assertEqual(cfg.pick_message(), "只有一条")


# ---------------------------------------------------------------- 时间窗判断


class TestIsActive(TempCase):
    def app_config(self, **over):
        data = dict(core.DEFAULT_CONFIG)
        data.update({"workStart": "09:00", "workEnd": "18:00",
                     "lunchBreak": {"enabled": True, "start": "12:00",
                                    "end": "13:00"}})
        data.update(over)
        return core.Config(self.write_config(json.dumps(data)))

    @staticmethod
    def at(h, m, day=5):
        # 2026-01-05 是周一
        return dt.datetime(2026, 1, day, h, m)

    def test_boundaries(self):
        cfg = self.app_config()
        self.assertTrue(core.is_active(cfg, self.at(9, 0))[0])
        self.assertFalse(core.is_active(cfg, self.at(8, 59))[0])
        self.assertTrue(core.is_active(cfg, self.at(18, 0))[0])
        self.assertFalse(core.is_active(cfg, self.at(18, 1))[0])

    def test_lunch(self):
        cfg = self.app_config()
        active, reason = core.is_active(cfg, self.at(12, 30))
        self.assertFalse(active)
        self.assertIn("午休", reason)
        self.assertTrue(core.is_active(cfg, self.at(13, 1))[0])

    def test_lunch_disabled(self):
        cfg = self.app_config(lunchBreak={"enabled": False, "start": "12:00",
                                          "end": "13:00"})
        self.assertTrue(core.is_active(cfg, self.at(12, 30))[0])

    def test_workdays_only(self):
        cfg = self.app_config(workdaysOnly=True)
        self.assertFalse(core.is_active(cfg, self.at(10, 0, day=10))[0])   # 周六
        self.assertFalse(core.is_active(cfg, self.at(10, 0, day=11))[0])   # 周日
        cfg2 = self.app_config(workdaysOnly=False)
        self.assertTrue(core.is_active(cfg2, self.at(10, 0, day=10))[0])

    def test_overnight_shift(self):
        cfg = self.app_config(workStart="22:00", workEnd="06:00",
                              lunchBreak={"enabled": False, "start": "12:00",
                                          "end": "13:00"})
        self.assertTrue(core.is_active(cfg, self.at(23, 0))[0])
        self.assertTrue(core.is_active(cfg, self.at(3, 0))[0])
        self.assertFalse(core.is_active(cfg, self.at(12, 0))[0])
        self.assertIn("跨夜", core.fmt_window(cfg))

    def test_reason_explains_itself(self):
        cfg = self.app_config()
        active, reason = core.is_active(cfg, self.at(21, 0))
        self.assertFalse(active)
        self.assertIn("09:00", reason)


# ---------------------------------------------------------------- 记录


class TestStore(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.store = core.Store(self.path("data"))

    def test_add_and_total(self):
        day = self.store.today_key()
        self.assertEqual(self.store.add(250, now=dt.datetime(2026, 10, 9, 9, 0)), 250)
        self.assertEqual(self.store.add(300, now=dt.datetime(2026, 10, 9, 10, 0)), 550)
        self.assertEqual(self.store.total(day), 550)
        self.assertEqual(self.store.count(day), 2)

    def test_empty_day(self):
        self.assertEqual(self.store.total(), 0)
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.store.pop_last(), (0, 0))

    def test_pop_last(self):
        now = dt.datetime(2026, 10, 9, 9, 0)
        self.store.add(200, now=now)
        self.store.add(350, now=now)
        day = self.store.today_key()
        self.assertEqual(self.store.pop_last(day), (350, 1))
        self.assertEqual(self.store.total(day), 200)
        self.assertEqual(self.store.pop_last(day), (200, 0))
        self.assertEqual(self.store.pop_last(day), (0, 0))

    def test_celebrated_flag(self):
        day = self.store.today_key()
        self.assertFalse(self.store.celebrated(day))
        self.store.mark_celebrated(day)
        self.assertTrue(self.store.celebrated(day))
        self.store.clear_celebrated(day)
        self.assertFalse(self.store.celebrated(day))
        self.store.clear_celebrated(day)      # 没标记也不能报错

    def test_history_shape(self):
        hist = self.store.history(7)
        self.assertEqual(len(hist), 7)
        self.assertEqual(hist[-1][0], self.store.today_key())

    def test_streak_counts_yesterday_when_today_not_done(self):
        today = dt.date.today()
        for back, amount in ((1, 2000), (2, 2500), (3, 500)):
            day = (today - dt.timedelta(days=back)).strftime("%Y-%m-%d")
            self.store._write(day, {"date": day, "logs": [{"time": "09:00",
                                                           "amount": amount}],
                                     "celebrated": False})
        self.assertEqual(self.store.streak(2000), 2)     # 昨天+前天，今天没喝够不算断
        self.store.add(2000)
        self.assertEqual(self.store.streak(2000), 3)     # 今天达标就接上

    def test_streak_zero_goal(self):
        self.assertEqual(self.store.streak(0), 0)

    def test_archive_old_moves_not_deletes(self):
        old = (dt.date.today() - dt.timedelta(days=500)).strftime("%Y-%m-%d")
        self.store._write(old, {"date": old, "logs": [{"time": "09:00", "amount": 10}]})
        recent = (dt.date.today() - dt.timedelta(days=10)).strftime("%Y-%m-%d")
        self.store._write(recent, {"date": recent, "logs": []})
        moved = self.store.archive_old(400)
        self.assertEqual(moved, 1)
        self.assertFalse(os.path.exists(self.store._path(old)))
        self.assertTrue(os.path.exists(os.path.join(self.store.dir, "archive",
                                                    old[:4], "%s.json" % old)))
        self.assertTrue(os.path.exists(self.store._path(recent)))
        self.assertEqual(self.store.archive_old(400), 0)   # 二次调用不重复搬

    def test_bad_file_in_data_dir_is_ignored(self):
        with open(os.path.join(self.store.dir, "icon.ico"), "wb") as fh:
            fh.write(b"\x00\x00\x01\x00")
        self.assertEqual(self.store.archive_old(1), 0)


# ---------------------------------------------------------------- 单位 / 文案


class TestDisplay(TempCase):
    def cfg(self, unit="ml"):
        return core.Config(self.write_config(json.dumps(
            dict(core.DEFAULT_CONFIG, unit=unit))))

    def test_ml(self):
        c = self.cfg("ml")
        self.assertEqual(core.unit_label(c), "ml")
        self.assertEqual(core.to_display(c, 250), "250")
        self.assertEqual(core.fmt_vol(c, 250), "250 ml")

    def test_oz_rounds_to_one_decimal(self):
        c = self.cfg("oz")
        self.assertEqual(core.unit_label(c), "oz")
        self.assertEqual(core.to_display(c, 250), "8.5")
        self.assertEqual(core.fmt_vol(c, 250), "8.5 oz")
        self.assertEqual(core.to_display(c, 0), "0")

    def test_unknown_unit_falls_back(self):
        c = core.Config(self.write_config(json.dumps(
            dict(core.DEFAULT_CONFIG, unit="cup"))))
        self.assertEqual(core.unit_label(c), "ml")

    def test_fmt_int_thousands(self):
        self.assertEqual(core.fmt_int(2000), "2,000")
        self.assertEqual(core.fmt_int(900), "900")

    def test_strip_emoji(self):
        self.assertEqual(core.strip_emoji("起来接水 \U0001F41F"), "起来接水")
        self.assertEqual(core.strip_emoji("\U0001F4A7"), "")
        self.assertEqual(core.strip_emoji(""), "")


# ---------------------------------------------------------------- 日志


class TestLog(TempCase):
    def test_rotation(self):
        core.LOG_MAX_BYTES = 64
        self.addCleanup(setattr, core, "LOG_MAX_BYTES", 512 * 1024)
        with open(core.LOG_PATH, "w", encoding="utf-8") as fh:
            fh.write("x" * 200)
        core.log("触发轮转")
        self.assertTrue(os.path.exists(core.LOG_PATH + ".1"))
        self.assertLess(os.path.getsize(core.LOG_PATH), 400)

    def test_log_never_raises(self):
        keep = core.LOG_PATH
        core.LOG_PATH = os.path.join(keep, "nope", "sub", "run.log")   # 不可写
        try:
            core.log("写不进去也不该抛")
        except Exception as exc:                                # noqa: BLE001
            self.fail("log 抛异常了：%s" % exc)


if __name__ == "__main__":
    unittest.main(verbosity=2)

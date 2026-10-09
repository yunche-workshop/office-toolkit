# -*- coding: utf-8 -*-
"""
test_core.py —— 核心逻辑自测（不碰界面，直接 python tests/test_core.py）

覆盖：规则链、非法名清理、扫描过滤、冲突检测、真实改名 + 撤销往返。
"""

import os
import sys
import time
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

import core  # noqa: E402


def rule(kind, **kw):
    r = {"kind": kind, "enabled": True}
    r.update(kw)
    return r


def ctx(index=0, orig="a", parent="p", mtime=None, ctime=None):
    now = mtime if mtime is not None else time.time()
    return {"index": index, "orig": orig, "parent": parent,
            "mtime": now, "ctime": ctime if ctime is not None else now}


class TestRules(unittest.TestCase):
    def test_replace(self):
        self.assertEqual(
            core.apply_rules("IMG_001", [rule("replace", find="IMG", to="照片")],
                             ctx()),
            "照片_001")

    def test_replace_case_insensitive(self):
        r = rule("replace", find="img", to="X", case_sensitive=False)
        self.assertEqual(core.apply_rules("IMG_001", [r], ctx()), "X_001")
        r2 = rule("replace", find="img", to="X", case_sensitive=True)
        self.assertEqual(core.apply_rules("IMG_001", [r2], ctx()), "IMG_001")

    def test_regex(self):
        r = rule("regex", pattern=r"\d+", to="N")
        self.assertEqual(core.apply_rules("a1b22c333", [r], ctx()), "aNbNcN")

    def test_strip(self):
        self.assertEqual(
            core.apply_rules("abcdef", [rule("strip", n=2, side="left")], ctx()),
            "cdef")
        self.assertEqual(
            core.apply_rules("abcdef", [rule("strip", n=2, side="right")], ctx()),
            "abcd")

    def test_strip_more_than_length(self):
        self.assertEqual(
            core.apply_rules("ab", [rule("strip", n=9, side="left")], ctx()), "")

    def test_case(self):
        self.assertEqual(
            core.apply_rules("Hello World", [rule("case", mode="lower")], ctx()),
            "hello world")
        self.assertEqual(
            core.apply_rules("Hello World", [rule("case", mode="upper")], ctx()),
            "HELLO WORLD")
        self.assertEqual(
            core.apply_rules("hello world", [rule("case", mode="title")], ctx()),
            "Hello World")

    def test_insert(self):
        self.assertEqual(
            core.apply_rules("a", [rule("insert", text="P", pos="prefix")], ctx()),
            "Pa")
        self.assertEqual(
            core.apply_rules("a", [rule("insert", text="S", pos="suffix")], ctx()),
            "aS")

    def test_number(self):
        r = rule("number", start=1, width=3, step=1, pos="suffix")
        out = [core.apply_rules("f", [r], ctx(index=i)) for i in range(3)]
        self.assertEqual(out, ["f001", "f002", "f003"])

    def test_number_step_and_no_padding(self):
        r = rule("number", start=5, width=0, step=10, pos="prefix")
        out = [core.apply_rules("f", [r], ctx(index=i)) for i in range(3)]
        self.assertEqual(out, ["5f", "15f", "25f"])

    def test_date(self):
        stamp = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, 0))
        r = rule("date", field="mtime", fmt="%Y%m%d", pos="prefix")
        self.assertEqual(
            core.apply_rules("a", [r], ctx(mtime=stamp)), "20261008a")

    def test_bad_date_format_falls_back(self):
        stamp = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, 0))
        r = rule("date", field="mtime", fmt="%Q", pos="suffix")
        out = core.apply_rules("a", [r], ctx(mtime=stamp))
        self.assertTrue(out.startswith("a"))
        self.assertTrue(len(out) > 1)

    def test_template_placeholders(self):
        stamp = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, 0))
        r = rule("template", text="{date}_{parent}_{name}")
        self.assertEqual(
            core.apply_rules("x", [r], ctx(parent="发票", mtime=stamp)),
            "20261008_发票_x")

    def test_template_can_reference_earlier_rules(self):
        chain = [rule("replace", find="IMG", to="照片"),
                 rule("template", text="{name}_ok")]
        self.assertEqual(core.apply_rules("IMG_1", chain, ctx()), "照片_1_ok")

    def test_disabled_rule_skipped(self):
        r = rule("replace", find="a", to="b")
        r["enabled"] = False
        self.assertEqual(core.apply_rules("aaa", [r], ctx()), "aaa")

    def test_broken_regex_does_not_explode(self):
        r = rule("regex", pattern="(", to="x")
        self.assertEqual(core.apply_rules("abc", [r], ctx()), "abc")


class TestSanitize(unittest.TestCase):
    def test_illegal_chars(self):
        self.assertEqual(core.sanitize('a/b:c*d?e"f<g>h|i'), "abcdefghi")

    def test_trailing_dot_and_space(self):
        self.assertEqual(core.sanitize("abc. "), "abc")

    def test_reserved_name(self):
        self.assertEqual(core.sanitize("CON"), "_CON")

    def test_empty(self):
        self.assertEqual(core.sanitize(""), "")
        self.assertEqual(core.sanitize("..."), "")


class TestScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brtest")
        self.sub = os.path.join(self.tmp, "sub")
        os.makedirs(self.sub)
        for name in ("a.txt", "b.jpg", "c.png"):
            open(os.path.join(self.tmp, name), "w").close()
        open(os.path.join(self.sub, "d.jpg"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_non_recursive(self):
        got = core.scan([self.tmp])
        self.assertEqual(len(got), 3)

    def test_recursive(self):
        got = core.scan([self.tmp], recursive=True)
        self.assertEqual(len(got), 4)

    def test_ext_filter(self):
        got = core.scan([self.tmp], recursive=True, exts=[".jpg"])
        self.assertEqual([os.path.basename(p) for p in got],
                         ["b.jpg", "d.jpg"])

    def test_natural_sort(self):
        tmp2 = tempfile.mkdtemp(prefix="brsort")
        try:
            for n in ("f2.txt", "f10.txt", "f1.txt"):
                open(os.path.join(tmp2, n), "w").close()
            got = [os.path.basename(p) for p in core.scan([tmp2])]
            self.assertEqual(got, ["f1.txt", "f2.txt", "f10.txt"])
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brplan")
        self.files = []
        for name in ("a.txt", "b.txt"):
            p = os.path.join(self.tmp, name)
            open(p, "w").close()
            self.files.append(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_conflict_detected(self):
        rows = core.plan(self.files, [rule("template", text="same")])
        statuses = [r["status"] for r in rows]
        self.assertEqual(statuses[0], "ok")
        self.assertEqual(statuses[1], "dup")

    def test_no_change_detected(self):
        rows = core.plan(self.files, [])
        self.assertTrue(all(r["status"] == "same" for r in rows))

    def test_target_exists_detected(self):
        open(os.path.join(self.tmp, "z.txt"), "w").close()
        rows = core.plan([self.files[0]], [rule("template", text="z")])
        self.assertEqual(rows[0]["status"], "exists")

    def test_empty_result_flagged(self):
        rows = core.plan([self.files[0]], [rule("template", text="")])
        self.assertEqual(rows[0]["status"], "illegal")

    def test_ext_kept(self):
        rows = core.plan([self.files[0]], [rule("template", text="new")])
        self.assertEqual(rows[0]["new_name"], "new.txt")


class TestExecuteUndo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brundo")
        self.files = []
        for name in ("one.txt", "two.txt", "three.txt"):
            p = os.path.join(self.tmp, name)
            open(p, "w").close()
            self.files.append(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self):
        rows = core.plan(self.files, [rule("number", start=1, width=2,
                                           step=1, pos="suffix")])
        log, done, failed = core.execute(rows)
        self.assertEqual(done, 3)
        self.assertEqual(failed, [])
        names = sorted(os.listdir(self.tmp))
        self.assertEqual(names, ["one01.txt", "three03.txt", "two02.txt"])

        restored, bad = core.undo(log)
        self.assertEqual(restored, 3)
        self.assertEqual(bad, [])
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         ["one.txt", "three.txt", "two.txt"])

    def test_case_only_rename(self):
        p = os.path.join(self.tmp, "Case.txt")
        open(p, "w").close()
        rows = core.plan([p], [rule("case", mode="upper")])
        todo = [r for r in rows if r["status"] == "ok"]
        self.assertEqual(len(todo), 1)
        log, done, failed = core.execute(rows)
        self.assertEqual(done, 1)
        self.assertIn("CASE.txt", os.listdir(self.tmp))
        core.undo(log)
        self.assertIn("Case.txt", os.listdir(self.tmp))

    def test_undo_log_persisted(self):
        rows = core.plan(self.files, [rule("insert", text="X", pos="prefix")])
        log, _done, _failed = core.execute(rows)
        core.save_undo(self.tmp, log)
        loaded = core.load_undo(self.tmp)
        self.assertEqual(loaded["count"], 3)
        restored, _bad = core.undo(loaded)
        self.assertEqual(restored, 3)
        # 撤销日志本身也在这个目录里，比对文件名时排除掉
        names = [n for n in os.listdir(self.tmp)
                 if not n.startswith(".batch_renamer")]
        self.assertEqual(sorted(names), ["one.txt", "three.txt", "two.txt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

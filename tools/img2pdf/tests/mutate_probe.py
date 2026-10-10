# -*- coding: utf-8 -*-
"""
mutate_probe.py —— 给 probe() 的断言做"变异测试"

规矩：新写的断言必须能被"把代码改坏"证明它真的会红。

这里把 src 复制到临时目录，每次只改一处，然后跑 TestProbe。
两处要注意：
  1) 有些表达式在 load() 和 probe() 里长得一模一样（比如 colorspace 那行），
     全局替换会同时改到 load()。TestProbe 拿 probe 和 load 互相比，两边一起
     改坏它就发现不了 —— 所以替换只在 probe() 函数体内做。
  2) unittest 的报告走 stderr，别只看 stdout。

    python tests/mutate_probe.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
TESTS = os.path.join(ROOT, "tests")

MUTATIONS = [
    ("灰度被判成彩色",
     'colorspace = b"/DeviceRGB" if chan >= 3 else b"/DeviceGray"',
     'colorspace = b"/DeviceRGB"'),
    ("EXIF 方向被吞掉",
     '"orientation": meta["orientation"], "jpeg": True,',
     '"orientation": 1, "jpeg": True,'),
    ("PNG 的 DPI 不再上报",
     '"dpi": info["dpi"], "orientation": 1, "jpeg": False,',
     '"dpi": None, "orientation": 1, "jpeg": False,'),
    ("JPEG 的 DPI 不再上报",
     '"dpi": meta["dpi"],',
     '"dpi": None,'),
    ("索引色的 tRNS 被无视",
     "has_alpha = trns",
     "has_alpha = False"),
    ("低位深当 8 位",
     'bpc = info["depth"]',
     "bpc = 8"),
    ("CMYK 少了 Decode",
     '"decode": [b"1 0"] * 4 if meta["comps"] == 4 else None}',
     '"decode": None}'),
    ("扩展名不再校验",
     "if ext not in SUPPORTED_EXTS:",
     "if False:"),
    ("空文件不拦了",
     "if not data:",
     "if False:"),
]


def probe_span(text):
    """probe() 函数体的 [起, 止) —— 只在这里动手，别误伤 load()。"""
    start = text.index("def probe(path):")
    nxt = re.search(r"\ndef \w", text[start + 10:])
    end = start + 10 + nxt.start() if nxt else len(text)
    return start, end


def run(work):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(work, "src")
    p = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", "tests.test_core.TestProbe"],
        cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def main():
    missed = []
    for name, old, new in MUTATIONS:
        work = tempfile.mkdtemp(prefix="i2p_mut_")
        try:
            shutil.copytree(SRC, os.path.join(work, "src"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(TESTS, os.path.join(work, "tests"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            core_py = os.path.join(work, "src", "core.py")
            with open(core_py, encoding="utf-8") as fh:
                text = fh.read()
            a, b = probe_span(text)
            body = text[a:b]
            if body.count(old) < 1:
                print("SKIP  %s：probe() 里没有「%s」" % (name, old))
                missed.append(name + "(没找到)")
                continue
            n = body.count(old)
            body = body.replace(old, new)
            with open(core_py, "w", encoding="utf-8") as fh:
                fh.write(text[:a] + body + text[b:])
            rc, out = run(work)
            fails = re.findall(r"^(FAIL|ERROR): (\S+)", out, re.M)
            if rc == 0:
                print("没抓住 %s（TestProbe 全绿）" % name)
                missed.append(name)
            else:
                print("抓住 %s（probe 里改了 %d 处）—— %d 个用例红了：%s"
                      % (name, n, len(fails),
                         ", ".join(sorted({f[1] for f in fails}))[:110]))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print("\n变异 %d 个，漏掉 %d 个" % (len(MUTATIONS), len(missed)))
    if missed:
        print("漏掉：" + "; ".join(missed))
        return 1
    print("probe 的断言全部能被改坏证明")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
mutate_fitz.py —— 证明 verify_fitz.py 那 77 条断言"抓得住真 bug"

verify_fitz 是拿 MuPDF 这个**第三方阅读器**验收，断言写得好不好没人证明过。
这里把 src 复制到临时目录，每次只改坏一处（都是"看起来能过"的错法），
跑一遍 verify_fitz，要求它必须报 FAIL —— 它全绿就说明那条路我们其实没验到。

只有装了 PyMuPDF + Pillow 的开发机能跑（tk38 那个 venv 没装，直接 SKIP）。

    PYTHONUTF8=1 python tests/mutate_fitz.py
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
    ("EXIF 方向 6 不转了（手机竖拍会躺倒）",
     "    6: (0, -1, 1, 0),     # 顺时针转 90（手机竖拍最常见）",
     "    6: (1, 0, 0, 1),      # 顺时针转 90（手机竖拍最常见）"),
    ("alpha 掩膜写反（0 和 255 对调）",
     '            alpha += s[i * 4 + 3:i * 4 + 4]',
     '            alpha += bytes([255 - s[i * 4 + 3]])'),
    ("CMYK 的 /Decode 反相丢了",
     '    decode = [b"1 0"] * 4 if meta["comps"] == 4 else None',
     '    decode = None'),
    ("透明图干脆不当透明（掩膜丢掉）",
     '            "smask": zlib.compress(alpha, 9) if alpha else None,',
     '            "smask": None,'),
    ("页面尺寸算错（DPI 当成磅）",
     "    return (img[\"width\"] * PT_PER_INCH / d, img[\"height\"] * PT_PER_INCH / d)",
     "    return (float(img[\"width\"]), float(img[\"height\"]))"),
    ("低位深灰度被当 8 位（1/2/4 bit 全花）",
     '        bpc = depth\n        for row in rows:',
     '        bpc = 8\n        for row in rows:'),
]


def run(work):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, os.path.join(work, "tests",
                                                     "verify_fitz.py")],
                       cwd=work, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def main():
    try:
        import fitz          # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:
        print("SKIP：这台机器没有 PyMuPDF / Pillow（%s）" % exc)
        return 0

    missed = []
    for name, old, new in MUTATIONS:
        work = tempfile.mkdtemp(prefix="i2p_mf_")
        try:
            for d in ("src", "tests"):
                shutil.copytree(os.path.join(ROOT, d),
                                os.path.join(work, d),
                                ignore=shutil.ignore_patterns("__pycache__"))
            core_py = os.path.join(work, "src", "core.py")
            with open(core_py, encoding="utf-8") as fh:
                text = fh.read()
            if text.count(old) != 1:
                print("SKIP  %s：锚点出现 %d 次，不敢乱改" % (name, text.count(old)))
                missed.append(name + "(锚点不唯一)")
                continue
            with open(core_py, "w", encoding="utf-8") as fh:
                fh.write(text.replace(old, new, 1))
            rc, out = run(work)
            fails = re.findall(r"^  FAIL (.+)$", out, re.M)
            if rc == 0 or not fails:
                print("没抓住 %s（verify_fitz 仍然全绿）" % name)
                missed.append(name)
            else:
                print("抓住 %s —— %d 条断言红了，例如：%s"
                      % (name, len(fails), fails[0][:70]))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print("\n变异 %d 个，漏掉 %d 个" % (len(MUTATIONS), len(missed)))
    if missed:
        print("漏掉：" + "; ".join(missed))
        return 1
    print("verify_fitz 的断言全部能被改坏证明")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
build.py —— 打包成单个 exe

用法（用装了 PyInstaller 的那个 venv）：
    C:/Users/Mu/.workbuddy/binaries/python/envs/tk38/Scripts/python.exe build.py

产物：dist/BatchRenamer.exe

体积控制：只用标准库，排除用不到的大包，不开 UPX
（UPX 明显提高杀软误报率，宁可大两兆）。
"""

import os
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
ASSETS = os.path.join(HERE, "assets")

sys.path.insert(0, SRC)
import icon  # noqa: E402

# 只排「确实没用到、但可能被 hook 顺手拉进来」的大包。
# 标准库不要乱排：json / email / http / xml 等被间接依赖，排掉会直接崩。
EXCLUDES = [
    "numpy",
    "PIL",
    "Pillow",
    "matplotlib",
    "pandas",
    "scipy",
    "PyQt5",
    "PySide2",
    "PySide6",
    "IPython",
    "pytest",
    "lxml",
    "sqlite3",
    "tkinter.test",
    "tkinter.tix",
    "pydoc_data",
]


def main():
    os.makedirs(ASSETS, exist_ok=True)
    ico = icon.ensure_icon(os.path.join(ASSETS, "app.ico"))

    args = [
        "--name=BatchRenamer",
        "--onefile",
        "--windowed",
        "--clean",
        "--noupx",
        "--noconfirm",
        "--icon=%s" % ico,
        "--distpath=%s" % os.path.join(HERE, "dist"),
        "--workpath=%s" % os.path.join(HERE, "build"),
        "--specpath=%s" % HERE,
        "--paths=%s" % SRC,
    ]
    for mod in EXCLUDES:
        args.append("--exclude-module=%s" % mod)
    args.append(os.path.join(SRC, "main.py"))

    print("打包参数：\n  " + "\n  ".join(args))
    PyInstaller.__main__.run(args)

    exe = os.path.join(HERE, "dist", "BatchRenamer.exe")
    if os.path.exists(exe):
        print("\n完成：%s（%.1f MB）" % (
            exe, os.path.getsize(exe) / 1024.0 / 1024.0))
    else:
        print("\n未找到产物，请检查上方输出")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
build.py —— 打包成单个 exe

用法（用装了 PyInstaller 的那个 venv，Python 3.8 + tkinter）：
    python build.py

产物：dist/BatchRenamer.exe

体积控制：只用标准库，排除用不到的大包，不开 UPX
（UPX 明显提高杀软误报率，宁可大两兆）。

版本号只有一个来源：src/core.py 里的 VERSION。它同时写进 exe 的文件属性、
界面署名和关于窗，不会出现"属性里 0.1、界面上 v0.2"。
"""

import os
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
ASSETS = os.path.join(HERE, "assets")
BUILD = os.path.join(HERE, "build")

sys.path.insert(0, SRC)
import core  # noqa: E402
import icon  # noqa: E402

COMPANY = "yunche-workshop"
PRODUCT = "BatchRenamer 批量重命名"
COPYRIGHT = "Copyright (c) 2026 %s · MIT License" % COMPANY

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


def version_tuple(text):
    """'0.1.0' -> (0, 1, 0, 0)；FixedFileInfo 要求四段。"""
    parts = []
    for chunk in str(text).split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3]) + (0,)


def write_version_file(path, version):
    """
    生成 PyInstaller 的 --version-file（和 water-reminder 同一套做法）。

    没有这段元数据的 exe，在资源管理器里公司一栏空白、版本号 0.0，
    用户反馈问题时报不出自己手上是哪一版。

    文本交给 PyInstaller 自己的 __str__ 生成，不手写：那段格式是要 eval()
    回来的，类名写成 VarInfo（正确是 VarFileInfo）就整次打包失败。
    """
    from PyInstaller.utils.win32.versioninfo import (  # noqa: WPS433
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    nums = version_tuple(version)
    strings = [
        ("CompanyName", COMPANY),
        ("FileDescription", PRODUCT),
        ("FileVersion", version),
        ("InternalName", "BatchRenamer"),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", "BatchRenamer.exe"),
        ("ProductName", PRODUCT),
        ("ProductVersion", version),
    ]
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0,
                          OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("080404b0",  # 中文(简体) / Unicode
                                        [StringStruct(k, v) for k, v in strings])]),
            VarFileInfo([VarStruct("Translation", [2052, 1200])]),
        ])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# UTF-8\n"
                 "# 本文件由 build.py 依据 src/core.py 的 VERSION 自动生成，别手改。\n"
                 + str(info) + "\n")
    return path


def main():
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    ico = icon.ensure_icon(os.path.join(ASSETS, "app.ico"))

    version_file = write_version_file(
        os.path.join(BUILD, "version.txt"), core.VERSION)
    print("版本元数据：v%s → %s" % (core.VERSION, version_file))

    args = [
        "--name=BatchRenamer",
        "--onefile",
        "--windowed",
        "--clean",
        "--noupx",
        "--noconfirm",
        "--icon=%s" % ico,
        "--version-file=%s" % version_file,
        "--distpath=%s" % os.path.join(HERE, "dist"),
        "--workpath=%s" % BUILD,
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

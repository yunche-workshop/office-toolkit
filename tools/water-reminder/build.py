# -*- coding: utf-8 -*-
"""
build.py —— 打包成单个 exe

用法：python build.py
产物：dist/WaterReminder.exe

体积控制：只用标准库，排除一切用不到的大包，不启用 UPX
（UPX 会明显提高杀软误报率，宁可大两兆）。

版本号只有一个来源：src/core.py 里的 VERSION。它同时写进 exe 的文件属性、
界面上的"v0.9.0"和 --version 的输出，不会出现"属性里 0.8、界面里 0.9"。
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
PRODUCT = "WaterReminder 喝水提醒"
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
    """'0.9.0' -> (0, 9, 0, 0)；FixedFileInfo 要求四段。"""
    parts = []
    for chunk in str(text).split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3]) + (0,)


def write_version_file(path, version):
    """
    生成 PyInstaller 的 --version-file。

    没有这段元数据的 exe，在资源管理器里叫"WaterReminder"、公司一栏空白、
    版本号 0.0 —— 用户想反馈问题时报不出自己装的是哪一版，
    Windows 的"属性 → 详细信息"也是空的。

    文本用 PyInstaller 自己的 __str__ 生成，不手写：那个格式是要 eval() 回来的，
    少一个逗号、错一个类名（VarInfo / VarFileInfo）就整次打包失败。
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
        ("InternalName", "WaterReminder"),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", "WaterReminder.exe"),
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

    # 优先用 make_icon.py 生成的多尺寸美术图标；没有就退回运行时那套自算水滴
    app_ico = os.path.join(ASSETS, "app.ico")
    if icon.is_ico_file(app_ico):
        ico = app_ico
    else:
        print("警告：assets/app.ico 缺失或损坏，本次用自算图标兜底")
        ico = icon.ensure_icon(os.path.join(ASSETS, "icon.ico"))

    version_file = write_version_file(
        os.path.join(BUILD, "version.txt"), core.VERSION)
    print("版本元数据：v%s → %s" % (core.VERSION, version_file))

    args = [
        "--name=WaterReminder",
        "--onefile",
        "--windowed",
        "--clean",
        "--noupx",
        "--noconfirm",
        "--icon=%s" % ico,
        "--version-file=%s" % version_file,
        # 托盘运行时读这张图，所以除了 exe 图标还得打进包里
        "--add-data=%s;assets" % ico,
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

    exe = os.path.join(HERE, "dist", "WaterReminder.exe")
    if os.path.exists(exe):
        print("\n完成：%s（%.1f MB）" % (exe, os.path.getsize(exe) / 1024.0 / 1024.0))
    else:
        print("\n未找到产物，请检查上方输出")


if __name__ == "__main__":
    main()

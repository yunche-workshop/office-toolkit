# -*- coding: utf-8 -*-
"""
build.py —— 把图片转 PDF 打包成单个 exe

用法（必须用装了 PyInstaller + tkinter 的那个 venv，Python 3.8）：
    C:/Users/Mu/.workbuddy/binaries/python/envs/tk38/Scripts/python.exe build.py

产物：dist/Img2Pdf.exe（双击开界面；带参数就是批处理）

三条从另外两个工具继承来的规矩：
  1. 不开 UPX —— 压过的 exe 杀软误报率明显高，宁可大两兆
  2. --exclude-module 只排"确实没用到但 hook 可能顺手拉进来"的大包，
     **不要排标准库**（json / email / http / xml 被间接依赖，排掉直接崩）
  3. 版本号只有一个来源：src/core.py 的 VERSION。它同时进 exe 文件属性、
     界面署名、关于窗和 --version，不会出现"属性里 0.1、界面上 v0.2"

⚠️ PyMuPDF(fitz) 和 Pillow 只出现在 tests/verify_fitz.py 这种开发期验收脚本里，
   src/ 一个都不 import。这里照样把它们排掉——万一哪天 venv 里装了、
   又被哪个 hook 顺进去，既白白大几十兆，又把 AGPL 依赖带进了 MIT 的发行物。

打包前先杀掉正在跑的旧 exe，否则 dist 里那个文件被占用，
PyInstaller 直接 PermissionError（水提醒那边踩过一次）。
"""

import os
import subprocess
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
ASSETS = os.path.join(HERE, "assets")
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
EXE = os.path.join(DIST, "Img2Pdf.exe")

sys.path.insert(0, SRC)
import core  # noqa: E402
import icon  # noqa: E402

COMPANY = "yunche-workshop"
PRODUCT = "Img2Pdf 图片转 PDF"
COPYRIGHT = "Copyright (c) 2026 %s · MIT License" % COMPANY

# 只排「确实没用到、但可能被 hook 顺手拉进来」的大包。
# 标准库不要乱排：json / email / http / xml 等被间接依赖，排掉会直接崩。
EXCLUDES = [
    "numpy",
    "PIL",
    "Pillow",
    "fitz",
    "pymupdf",
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
    生成 PyInstaller 的 --version-file（和水提醒、批量改名同一套做法）。

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
        ("InternalName", core.APP_NAME),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", core.APP_NAME + ".exe"),
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


def kill_running_exe():
    """
    旧产物还在跑就先收掉：Windows 下占用的文件写不进去。

    只按进程名收自己这个 exe，不做全局清理。
    """
    try:
        subprocess.call(["taskkill", "/IM", "Img2Pdf.exe", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:                       # 没有 taskkill 也要能继续打包
        print("（跳过收尾旧进程：%s）" % exc)


def main():
    kill_running_exe()
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    ico = icon.ensure_icon(os.path.join(ASSETS, "app.ico"))
    print("图标：%s" % ico)

    version_file = write_version_file(
        os.path.join(BUILD, "version.txt"), core.VERSION)
    print("版本元数据：v%s → %s" % (core.VERSION, version_file))

    args = [
        "--name=%s" % core.APP_NAME,
        "--onefile",
        "--windowed",
        "--clean",
        "--noupx",
        "--noconfirm",
        "--icon=%s" % ico,
        "--version-file=%s" % version_file,
        "--distpath=%s" % DIST,
        "--workpath=%s" % BUILD,
        "--specpath=%s" % HERE,
        "--paths=%s" % SRC,
    ]
    for mod in EXCLUDES:
        args.append("--exclude-module=%s" % mod)
    args.append(os.path.join(SRC, "main.py"))

    print("打包参数：\n  " + "\n  ".join(args))
    PyInstaller.__main__.run(args)

    if os.path.exists(EXE):
        print("\n完成：%s（%.1f MB）" % (
            EXE, os.path.getsize(EXE) / 1024.0 / 1024.0))
        print("验收：右键 → 属性 → 详细信息，应看到 v%s / %s"
              % (core.VERSION, COMPANY))
    else:
        print("\n未找到产物，请检查上方输出")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
brand.py —— 署名与「关于」的唯一来源

为什么单独一个文件：界面上、README、打包元数据里都要出现同一串品牌信息，
写三个地方迟早对不上（版本号已经吃过一次亏，见 core.VERSION 的注释）。

这个文件在每个工具目录下各有一份，内容完全一致 —— 不是忘了抽公共库，
是故意的：每个工具都要能独立打包成单文件 exe，`sys.path` 里多一个上层目录
就得在 spec/build.py 里多写一条路径，将来抽 skin.py 时一起处理。
"""

WORKSHOP = "允澈工坊"
HANDLE = "yunche-workshop"
REPO_URL = "https://github.com/yunche-workshop/office-toolkit"
LICENSE = "MIT License"

# 界面上那行小字。署名要短，长了会挤到别的控件。
BYLINE = "制作 by " + WORKSHOP


def credit_line(tool_name, version):
    """界面署名行的完整文案：制作 by 允澈工坊 · 喝水提醒 v0.9.1"""
    return "%s · %s v%s" % (BYLINE, tool_name, version)


def repo_display():
    """
    界面上显示的那一行地址（不带 https://）。
    带上协议头在窄窗口里会硬撑成两行、第一行还顶到卡片右边；
    复制和打开用的仍然是 REPO_URL 那个完整地址。
    """
    return REPO_URL.split("://", 1)[-1]


def about_rows(tool_name, version, summary):
    """
    「关于」小窗里的信息行，两个工具共用同一套顺序。
    返回 [(标签, 显示值, 链接目标)]：目标非空表示这一行可点、可复制。
    """
    return [
        ("工具", "%s v%s" % (tool_name, version), None),
        ("简介", summary, None),
        ("仓库", repo_display(), REPO_URL),
        ("协议", LICENSE + "（可自由使用、修改、分发）", None),
        ("运行", "Windows 10 / 11 · 单文件绿色版 · 只用 Python 标准库", None),
        ("联网", "程序自身不发起任何网络请求；点「打开仓库」才会叫出浏览器", None),
    ]

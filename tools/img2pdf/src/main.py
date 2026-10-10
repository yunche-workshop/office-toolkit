# -*- coding: utf-8 -*-
"""
main.py —— 图片转 PDF 的入口（带界面；命令行给参数就走无界面批处理）

顺序很重要：enable_dpi_awareness() 必须在创建任何窗口之前调用，
否则 200% 缩放的屏上整个窗口会被 Windows 位图拉伸，字是糊的。

命令行模式是给"每次都要点开窗口再拖文件"那种重复劳动准备的：
    python src/main.py 发票/*.jpg -o 报销.pdf --paper A4 --margin 20
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core            # noqa: E402
import brand           # noqa: E402
import win32ext        # noqa: E402

USAGE = """用法：
  Img2Pdf                      打开界面
  Img2Pdf 图片... [-o 输出.pdf] [--paper image|A4|A5|Letter]
                 [--orient auto|portrait|landscape] [--margin 磅]
                 [--dpi 数字] [--title 文字]
  Img2Pdf --version            只打印版本号

不给 -o 就落在第一张图旁边，叫「<首图名>等N张.pdf」。"""

# parse_args 的哨兵：(None, None) = 该打帮助，(None, SHOW_VERSION) = 该打版本。
# 用 None 当"没有图片路径"，用第二个元素区分两种"只看信息就走"的情况。
SHOW_VERSION = "version"


def _say(text, err=False):
    """
    往控制台写一行；没有控制台时安静丢掉，不抛。

    exe 是用 --windowed 打的，那种模式下 sys.stdout / sys.stderr 会是 None：
    print() 自己就不输出，但 sys.stderr.write 直接 AttributeError ——
    在"报错"这条路上崩掉比不报错难查得多，所以统一走这里。
    """
    stream = sys.stderr if err else sys.stdout
    if stream is None:
        return
    stream.write(text + "\n")


def print_version():
    """
    --version / -v：版本号只有一个来源（core.VERSION）。

    顺手把仓库地址打出来：命令行也是引流入口，有人把终端输出截进文章或
    群里时，地址跟着一起出去（和另外两个工具同一套做法）。
    """
    _say("%s v%s —— %s" % (core.TOOL_CN, core.VERSION, core.SUMMARY))
    _say("仓库：%s" % brand.repo_display())
    _say("协议：%s · 只用 Python 标准库，转换全程在本机完成、不联网" % brand.LICENSE)


def center(root, w, h):
    left, top, right, bottom = win32ext.monitor_work_area()
    x = left + (right - left - w) // 2
    y = top + (bottom - top - h) // 2
    x, y = win32ext.clamp_into_view(x, y, w, h)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))


def parse_args(argv):
    """
    自己拆参数，不用 argparse：Python 3.8 的 argparse 在 --help 之外的
    中文提示里会跟着系统代码页走，命令行输出容易乱码；错误信息也要说人话。

    返回 (图片路径列表, 选项字典)；选项字典里没出现的键留给 core 的默认值。
    """
    paths, opts, need = [], {}, None
    for i, a in enumerate(argv):
        if need == "o":
            opts["out_path"] = a
            need = None
        elif need == "paper":
            if a not in core.PAGE_MODES:
                raise ValueError("--paper 只认 %s（现在是 %s）"
                                 % (" / ".join(core.PAGE_MODES), a))
            opts["page"] = a
            need = None
        elif need == "orient":
            if a not in core.ORIENTS:
                raise ValueError("--orient 只认 %s（现在是 %s）"
                                 % (" / ".join(core.ORIENTS), a))
            opts["orient"] = a
            need = None
        elif need in ("margin", "dpi"):
            try:
                v = float(a)
            except ValueError:
                raise ValueError("--%s 得是个数字，现在是 %s" % (need, a))
            opts["margin" if need == "margin" else "dpi"] = v
            need = None
        elif need == "title":
            opts["title"] = a
            need = None
        elif a in ("-h", "--help"):
            return None, None
        elif a in ("-v", "--version"):
            return None, SHOW_VERSION
        elif a in ("-o", "--out"):
            need = "o"
        elif a == "--paper":
            need = "paper"
        elif a == "--orient":
            need = "orient"
        elif a == "--margin":
            need = "margin"
        elif a == "--dpi":
            need = "dpi"
        elif a == "--title":
            need = "title"
        elif a.startswith("-"):
            raise ValueError("不认识的参数：%s\n\n%s" % (a, USAGE))
        else:
            paths.append(a)
    if need:
        raise ValueError("%s 后面缺值" % ("--" + need if need != "o" else "-o"))
    return paths, opts


def expand(paths):
    """
    把目录摊开成里面的图片，顺手挡掉不存在和 unsupported 的。

    排序按文件名：Windows 的资源管理器按名字排，相机也是按名字编号的，
    跟着它走最不容易"页序乱了得重排"。
    """
    out = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                q = os.path.join(p, name)
                if os.path.isfile(q) and core.ext_of(q) in core.SUPPORTED_EXTS:
                    out.append(q)
        elif os.path.isfile(p):
            out.append(p)
        else:
            raise ValueError("找不到：%s" % p)
    return out


def cli(argv):
    paths, opts = parse_args(argv)
    if paths is None:
        # 帮助和版本都是"看完信息就走"，退出码 0；不打印用法当错误
        if opts == SHOW_VERSION:
            print_version()
        else:
            _say(USAGE)
        return 0
    if not paths:
        _say(USAGE)
        return 2
    inputs = expand(paths)
    if not inputs:
        _say("挑出来的东西里没有 .jpg / .jpeg / .png")
        return 2
    out = opts.pop("out_path", None) or core.suggested_output(inputs)
    res = core.convert(inputs, out, **opts)
    _say("写了 %s：%d 页 · %.1f KB" % (res["out"], res["pages"],
                                       res["bytes"] / 1024.0))
    for name, why in res["failed"]:
        _say("跳过 %s —— %s" % (name, why))
    return 0 if res["pages"] else 2


def gui():
    win32ext.enable_dpi_awareness()
    root = tk.Tk()
    root.configure(bg="#16161a")
    center(root, win32ext.px(860), win32ext.px(640))
    import ui            # noqa: 放这儿：只有走界面这条路才需要 Tk 的控件层
    app = ui.run(root)
    root.mainloop()
    return app


def main():
    argv = sys.argv[1:]
    if argv:
        # 只要带参数就按批处理走；双击 exe（无参数）才是界面。
        # 这样 `Img2Pdf.exe a.jpg -o x.pdf` 在脚本、计划任务里都能直接用
        try:
            return cli(argv)
        except core.ImageError as exc:
            _say("%s" % exc, err=True)
            return 1
        except ValueError as exc:
            _say("%s" % exc, err=True)
            return 2
    gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())

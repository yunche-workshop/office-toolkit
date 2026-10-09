# -*- coding: utf-8 -*-
"""
core.py —— 批量重命名的核心逻辑

这一层不碰界面，全是纯函数，方便直接跑单测。
界面（ui.py）只负责收集规则和展示结果。

设计要点：
- 规则按顺序链式执行，每条可单独开关
- 真正改名之前先 plan()，把「新名冲突 / 非法字符 / 目标已存在」全查出来给用户看
- execute() 每改一个就记一条，返回 undo 日志；undo() 逆序还原
"""

import os
import re
import json
import time
import tempfile

APP_NAME = "BatchRenamer"

# 版本号的唯一来源：exe 文件属性（build.py）、界面署名、关于窗都读这里，
# 免得出现"属性里 0.1、界面上 v0.2"。
VERSION = "0.1.0"

# 一句话简介：关于窗和 --help 用同一句，别在界面里另写一版。
SUMMARY = "批量改文件名，改错了能一键撤销"

# Windows 文件名非法字符
ILLEGAL_CHARS = '<>:"/\\|?*'
# Windows 保留设备名
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

SORT_MODES = ("name", "mtime", "size")


# ---------------------------------------------------------------- 规则

def default_rules():
    """面板上从上到下的默认规则。改顺序就是改这里的顺序。"""
    return [
        {"kind": "replace", "enabled": False,
         "find": "", "to": "", "case_sensitive": True},
        {"kind": "regex", "enabled": False,
         "pattern": "", "to": ""},
        {"kind": "strip", "enabled": False,
         "n": 1, "side": "left"},
        {"kind": "case", "enabled": False,
         "mode": "keep"},
        {"kind": "insert", "enabled": False,
         "text": "", "pos": "prefix"},
        {"kind": "number", "enabled": False,
         "start": 1, "width": 3, "step": 1, "pos": "suffix"},
        {"kind": "date", "enabled": False,
         "field": "mtime", "fmt": "%Y%m%d", "pos": "prefix"},
        {"kind": "template", "enabled": False,
         "text": "{name}"},
    ]


def rule_label(kind):
    return {
        "replace": "查找替换",
        "regex": "正则替换",
        "strip": "删除字符",
        "case": "大小写",
        "insert": "加前缀/后缀",
        "number": "编号",
        "date": "加日期",
        "template": "重设文件名",
    }.get(kind, kind)


def _fmt_date(stamp, fmt):
    try:
        return time.strftime(fmt, time.localtime(stamp))
    except Exception:
        # 用户自己填的格式可能不合法，退回一个安全值
        return time.strftime("%Y%m%d", time.localtime(stamp))


def _safe_int(value, lo, hi, default):
    try:
        n = int(str(value).strip())
    except Exception:
        return default
    return max(lo, min(hi, n))


def apply_rules(name, rules, ctx):
    """
    对「不含扩展名」的文件名按顺序应用规则链，返回新名字。

    ctx 里带：index / orig（原始名）/ parent（父文件夹名）/ mtime / ctime
    """
    out = name
    for rule in rules or []:
        if not rule.get("enabled"):
            continue
        kind = rule.get("kind")
        try:
            if kind == "replace":
                find = rule.get("find", "")
                if not find:
                    continue
                to = rule.get("to", "")
                if rule.get("case_sensitive", True):
                    out = out.replace(find, to)
                else:
                    out = re.sub(re.escape(find), lambda _m: to, out,
                                 flags=re.IGNORECASE)

            elif kind == "regex":
                pattern = rule.get("pattern", "")
                if not pattern:
                    continue
                out = re.sub(pattern, rule.get("to", ""), out)

            elif kind == "strip":
                n = _safe_int(rule.get("n", 0), 0, 999, 0)
                if n <= 0:
                    continue
                if rule.get("side") == "right":
                    out = out[:-n] if n < len(out) else ""
                else:
                    out = out[n:]

            elif kind == "case":
                mode = rule.get("mode", "keep")
                if mode == "lower":
                    out = out.lower()
                elif mode == "upper":
                    out = out.upper()
                elif mode == "title":
                    out = out.title()

            elif kind == "insert":
                text = rule.get("text", "")
                if not text:
                    continue
                if rule.get("pos") == "suffix":
                    out = out + text
                else:
                    out = text + out

            elif kind == "number":
                width = _safe_int(rule.get("width", 0), 0, 12, 0)
                start = _safe_int(rule.get("start", 1), 0, 999999999, 1)
                step = _safe_int(rule.get("step", 1), 1, 9999, 1)
                num = start + ctx["index"] * step
                text = ("%%0%dd" % width % num) if width > 0 else str(num)
                if rule.get("pos") == "prefix":
                    out = text + out
                else:
                    out = out + text

            elif kind == "date":
                stamp = ctx.get(rule.get("field", "mtime"), time.time())
                text = _fmt_date(stamp, rule.get("fmt", "%Y%m%d"))
                if rule.get("pos") == "suffix":
                    out = out + text
                else:
                    out = text + out

            elif kind == "template":
                text = rule.get("text", "{name}")
                out = text.format(
                    name=out,
                    origin=ctx.get("orig", out),
                    parent=ctx.get("parent", ""),
                    index=ctx["index"] + 1,
                    date=_fmt_date(ctx.get("mtime", time.time()), "%Y%m%d"),
                )
        except Exception:
            # 单条规则出错不能把整个批次搞崩，跳过继续
            continue

    return out


def sanitize(name):
    """去掉非法字符、收尾空格和点，处理 Windows 保留名。"""
    out = "".join(ch for ch in name if ch not in ILLEGAL_CHARS)
    out = out.replace("\x00", "").strip()
    out = out.rstrip(". ")          # Windows 文件名不能以点或空格结尾
    if not out:
        return ""
    if out.split(".")[0].upper() in RESERVED_NAMES:
        out = "_" + out
    return out


def split_name(path):
    """把路径拆成 (目录, 主名, 扩展名带点)。.tar.gz 这种只认最后一段。"""
    folder = os.path.dirname(path)
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    return folder, stem, ext


# ---------------------------------------------------------------- 扫描

def scan(paths, recursive=False, exts=None, sort_by="name"):
    """
    把用户丢进来的文件/文件夹摊平成文件列表。

    exts: 允许的后缀集合，元素形如 '.jpg'；None 表示不限
    """
    found = []
    for p in paths or []:
        if os.path.isfile(p):
            found.append(p)
        elif os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        found.append(os.path.join(root, f))
            else:
                try:
                    for f in os.listdir(p):
                        full = os.path.join(p, f)
                        if os.path.isfile(full):
                            found.append(full)
                except OSError:
                    continue

    if exts:
        wanted = set(e.lower() for e in exts)
        found = [p for p in found
                 if os.path.splitext(p)[1].lower() in wanted]

    # 去重，保持先来后到
    seen = set()
    uniq = []
    for p in found:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)

    if sort_by == "mtime":
        uniq.sort(key=lambda p: (os.path.getmtime(p), os.path.basename(p)))
    elif sort_by == "size":
        uniq.sort(key=lambda p: (os.path.getsize(p), os.path.basename(p)))
    else:
        uniq.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return uniq


def _natural_key(text):
    """自然排序：file2 排在 file10 前面。"""
    return [int(s) if s.isdigit() else s.lower()
            for s in re.split(r"(\d+)", text)]


# ---------------------------------------------------------------- 计划

def plan(files, rules, keep_ext=True):
    """
    只算不改。返回列表，每项：
    {"old": 原绝对路径, "new": 新绝对路径, "old_name"/"new_name": 文件名,
     "status": ok/dup/illegal/exists/same, "note": 说明}
    """
    rows = []
    used = {}
    for i, path in enumerate(files):
        folder, stem, ext = split_name(path)
        try:
            st = os.stat(path)
            mtime, ctime = st.st_mtime, st.st_ctime
        except OSError:
            mtime = ctime = time.time()

        ctx = {
            "index": i,
            "orig": stem,
            "parent": os.path.basename(folder) or folder,
            "mtime": mtime,
            "ctime": ctime,
        }

        new_stem = sanitize(apply_rules(stem, rules, ctx))
        new_ext = ext if keep_ext else ""
        new_name = new_stem + new_ext
        old_name = os.path.basename(path)

        status, note = "ok", ""
        if not new_stem:
            status, note = "illegal", "新名字是空的"
        elif new_name == old_name:
            status, note = "same", "没变化"
        else:
            # 同一批里撞名
            key = os.path.normcase(os.path.join(folder, new_name))
            if key in used:
                status, note = "dup", "和上面第 %d 个撞名" % (used[key] + 1)
            else:
                used[key] = i
                target = os.path.join(folder, new_name)
                if os.path.exists(target) and \
                        os.path.normcase(target) != os.path.normcase(path):
                    status, note = "exists", "目标文件已存在"

        rows.append({
            "old": path,
            "new": os.path.join(folder, new_name),
            "old_name": old_name,
            "new_name": new_name,
            "status": status,
            "note": note,
        })
    return rows


# ---------------------------------------------------------------- 执行

def execute(rows):
    """
    按计划真正改名。只动 status == ok 的行。
    返回 (undo_log, done, failed)；undo_log 可直接交给 undo()。
    """
    log = []
    done = 0
    failed = []

    for row in rows:
        if row.get("status") != "ok":
            continue
        src = row["old"]
        dst = row["new"]
        if not os.path.exists(src):
            failed.append((src, "源文件不见了"))
            continue
        try:
            # 只改大小写时 Windows 的 os.rename 不会报错但不生效，走两步
            if os.path.normcase(src) == os.path.normcase(dst) and src != dst:
                tmp = src + ".__renametmp__"
                os.rename(src, tmp)
                os.rename(tmp, dst)
            else:
                os.rename(src, dst)
            log.append({"from": src, "to": dst})
            done += 1
        except OSError as e:
            failed.append((src, "改名失败：%s" % e))

    if not log:
        return None, done, failed

    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(log),
        "items": log,
    }, done, failed


def undo(log):
    """
    撤销。逆序还原，尽量把能还原的都还原回去。
    返回 (restored, failed)。
    """
    if not log or not log.get("items"):
        return 0, []

    restored = 0
    failed = []
    for item in reversed(log["items"]):
        src = item.get("to")
        dst = item.get("from")
        if not src or not dst or not os.path.exists(src):
            failed.append((src or "?", "文件不在了，跳过"))
            continue
        try:
            if os.path.exists(dst):
                # Windows 大小写不敏感：「只改大小写」时 exists 会误判成被占，
                # 实际可以还原，只是得走两步
                if os.path.normcase(src) == os.path.normcase(dst) and \
                        src != dst:
                    tmp = src + ".__renametmp__"
                    os.rename(src, tmp)
                    os.rename(tmp, dst)
                    restored += 1
                    continue
                failed.append((dst, "目标位置被占了，跳过"))
                continue
            os.rename(src, dst)
            restored += 1
        except OSError as e:
            failed.append((src, "还原失败：%s" % e))
    return restored, failed


# ---------------------------------------------------------------- 撤销日志存取

def undo_path(folder):
    return os.path.join(folder, ".batch_renamer_undo.json")


def save_undo(folder, log):
    """原子写：tmp + fsync + replace，写到被改名文件所在的目录。"""
    path = undo_path(folder)
    data = json.dumps(log, ensure_ascii=False, indent=2)
    tmp_fd, tmp = tempfile.mkstemp(dir=folder, prefix=".brtmp", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_undo(folder):
    path = undo_path(folder)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_undo(folder):
    path = undo_path(folder)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

# -*- coding: utf-8 -*-
"""
core.py —— 配置 / 数据存储 / 时间判断

纯标准库实现，零第三方依赖。
数据全部落在本地，不联网、不上传。
"""

import datetime as dt
import json
import os
import random
import re
import sys

APP_NAME = "WaterReminder"

# 版本号的唯一来源：exe 元数据（build.py）、启动日志、设置窗口标题、关于窗、CHANGELOG 都读这里。
# 每次改动打包前记得同步 CHANGELOG.md。
VERSION = "0.9.1"

# 一句话简介：关于窗、启动日志、README 首段都用这一句，别在界面里另写一版。
SUMMARY = "只在设定的工作时段弹右下角提醒的常驻小工具"

# 一天一个 JSON 文件，超过这个天数的挪进 data/archive/（只挪不删，历史永远在）
ARCHIVE_AFTER_DAYS = 400

DEFAULT_MESSAGES = [
    "起来接水，顺便摸个鱼",
    "水杯空了，人也快空了",
    "接杯水，顺便看看窗外",
    "久坐伤身，起来动动",
    "老板不会给你发水，自己来",
    "喝完这杯，再战两小时",
    "你的肾在等你",
    "摸鱼借口已生成：接水",
    "离下班还有几杯水？先喝这杯",
    "空调房里最容易脱水，喝一口",
    "脑子转不动了？可能只是缺水",
    "站起来，这不算离岗",
]

DEFAULT_CONFIG = {
    "workStart": "09:00",
    "workEnd": "18:00",
    "lunchBreak": {"enabled": True, "start": "12:00", "end": "13:00"},
    "intervalMinutes": 60,
    "amountPerReminder": 250,
    "dailyGoal": 2000,
    "snoozeMinutes": 15,
    "workdaysOnly": False,
    "autoStart": False,
    # 弹提醒时顺带一声轻提示。人不在屏幕前只有视觉提醒必然漏，
    # 声音是标准库 winsound 发的，不占体积、不联网、可在设置里关掉。
    "sound": True,
    # auto = 跟随系统"应用深色/浅色"设置；也可手动钉死 dark / light
    "theme": "auto",
    # 显示单位，只影响界面上怎么写，存储永远是毫升
    "unit": "ml",
    "messages": list(DEFAULT_MESSAGES),
    "goalReachedMessage": "今日目标达成，你今天喝够了，剩下的随意",
}

INT_RULES = (
    ("intervalMinutes", 60, 5, 480),
    ("amountPerReminder", 250, 50, 2000),
    ("dailyGoal", 2000, 200, 10000),
    ("snoozeMinutes", 15, 5, 120),
)

INT_LABELS = {
    "intervalMinutes": "提醒间隔",
    "amountPerReminder": "每次喝水",
    "dailyGoal": "每日目标",
    "snoozeMinutes": "稍后提醒",
}

TIME_LABELS = {
    "workStart": "上班时间",
    "workEnd": "下班时间",
    "lunchStart": "午休开始",
    "lunchEnd": "午休结束",
}


def fix_label(key):
    """纠正项显示用的中文名；日志和设置窗口共用一张表，别两边各写一遍。"""
    return INT_LABELS.get(key) or TIME_LABELS.get(key) or key

_UNITS = {"ml": "毫升", "oz": "盎司"}
ML_PER_OZ = 29.5735


# emoji 在 Tk 里渲染成方块，界面显示前过滤掉（配置文件里仍然保留）
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"
    "\U00002190-\U000021FF"
    "\U00002400-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F]"
)


def strip_emoji(text):
    """去掉 emoji 和变异选择符，让 Tk 能正常显示。"""
    if not text:
        return ""
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", text)).strip()


def is_frozen():
    return getattr(sys, "frozen", False)


BASE_DIR_KIND = ""    # portable = exe 同目录；appdata = 回落到 %APPDATA%，界面要说清楚


def base_dir():
    """
    数据目录：exe 同目录（便携）；不可写时回落到 %APPDATA%。
    回落在以前是完全静默的，结果就是"我明明改了 config.json 怎么没生效"——
    用户改的是 exe 旁边那份，程序读的是 APPDATA 里那份。现在记下来源并写日志。
    """
    global BASE_DIR_KIND
    if is_frozen():
        candidate = os.path.dirname(os.path.abspath(sys.executable))
    else:
        candidate = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        probe = os.path.join(candidate, ".write_test")
        with open(probe, "w") as fh:
            fh.write("1")
        os.remove(probe)
        BASE_DIR_KIND = "portable"
        return candidate
    except Exception as exc:
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        fallback = os.path.join(root, APP_NAME)
        try:
            os.makedirs(fallback, exist_ok=True)
        except Exception:
            fallback = os.path.join(os.path.expanduser("~"), APP_NAME)
            os.makedirs(fallback, exist_ok=True)
        BASE_DIR_KIND = "appdata"
        # 这里不能用 log()：LOG_PATH 还指向能写进去的那个目录
        print("数据目录回落到 %s（原目录不可写：%s）" % (fallback, exc))
        return fallback


ROOT = base_dir()
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_PATH = os.path.join(ROOT, "run.log")

# 源码运行时的项目根（core.py 在 src/ 下，往上两层）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_MAX_BYTES = 512 * 1024   # 超过就转成 run.log.1，只留一份


def resource_path(name, sub="assets"):
    """
    取随 exe 一起打包的只读资源，返回绝对路径；取不到返回 ""。

    onefile 会把资源解到临时目录 sys._MEIPASS，源码运行时就是项目 assets/。
    资源缺失不算致命错误（图标有运行时自算的兜底），所以这里静默返回空串，
    降级策略交给调用方决定。
    """
    if is_frozen():
        tmp = getattr(sys, "_MEIPASS", "")
        cand = os.path.join(tmp, sub, name) if tmp else ""
    else:
        cand = os.path.join(PROJECT_ROOT, sub, name)
    try:
        if cand and os.path.isfile(cand) and os.path.getsize(cand) > 0:
            return cand
    except OSError:
        return ""
    return ""


def _to_int(value, default, low, high):
    """把用户填的值收成整数并夹到 [low, high]；坏值一律回落到默认。"""
    try:
        v = int(str(value).strip())
    except Exception:
        return default
    return max(low, min(high, v))


_HM_RE = re.compile(r"^\s*(\d{1,2}):(\d{1,2})\s*$")


def valid_hm(value):
    """'09:30' / '9:5' 这种写法算合法并补零；越界或写错算不合法。"""
    m = _HM_RE.match(str(value or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def _norm_hm(data, key, default, fixes, fix_key=None):
    """
    把一个时间字段收成 'HH:MM'。写坏/越界的值落回默认并记进 fixes。
    这一步以前没有：手改 config.json 把 workStart 写成 "25:00"，
    软件会一整天不提醒、日志一个字都不写，而 README 明明白白写着"配置随便改"。
    fix_key 是给界面/日志看的名字：午休那两个字段在 data 里就叫 start/end，
    直接拿它报出来，用户看不出是哪一项被改了。
    """
    raw = data.get(key)
    if raw is None:                      # 老配置根本没写这个键，用默认值，不算"纠正"
        data[key] = default
        return default
    fixed = valid_hm(raw)
    if fixed is None:
        fixed = default
    if fixed != str(raw):
        fixes.append((fix_key or key, raw, fixed))
    data[key] = fixed
    return fixed


def _write_text(path, text):
    """
    原子写：先写同目录的 .tmp，再 os.replace 覆盖目标。
    直接 open(path,'w') 会在写一半时被断电/杀进程留下半截文件，
    而 config.json 一旦坏了下次启动会静默退回默认值 —— 用户设置全丢。
    """
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, os.path.basename(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _rotate_log():
    try:
        if os.path.getsize(LOG_PATH) <= LOG_MAX_BYTES:
            return
    except Exception:
        return
    try:
        bak = LOG_PATH + ".1"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(LOG_PATH, bak)
    except Exception:
        pass


def log(message):
    """写一行日志，失败不影响主流程。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _rotate_log()
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (stamp, message))
    except Exception:
        pass


# ---------------------------------------------------------------- 配置


class Config(object):
    def __init__(self, path=None):
        self.path = path or CONFIG_PATH
        self.data = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
        self.raw = {}          # 文件里用户原样填的值，用来回显"被修正过"
        self.fixes = []        # [(键名, 原值, 生效值)]，本次加载被纠正过的项
        self.created_new = False
        self.corrupt_backup = ""   # 坏配置另存到的路径（如果有）
        self._bag = []         # 文案洗牌袋
        self._bag_last = None
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                merged = json.loads(json.dumps(DEFAULT_CONFIG))
                merged.update(raw)
                self.data = merged
                self.raw = dict(raw)
        except FileNotFoundError:
            self.created_new = True
            self.save()
        except Exception as exc:
            # 文件在但读不出来（半截 JSON / 手改漏了逗号 / 编码坏了）。
            # 以前只记一行日志就静默退回默认值，用户的设置当场蒸发。
            # 现在先把原件另存一份，界面上也要说明"这次用的是默认值 + 备份在哪"。
            self.corrupt_backup = self._backup_corrupt()
            log("配置读取失败，使用默认值：%s%s" % (
                exc,
                "（原文件已另存 %s）" % self.corrupt_backup if self.corrupt_backup else ""))
        self._normalize()
        return self.data

    def _backup_corrupt(self):
        try:
            if not os.path.isfile(self.path):
                return ""
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            folder = os.path.dirname(self.path) or "."
            target = os.path.join(
                folder, "%s.bad-%s.json" % (os.path.splitext(os.path.basename(self.path))[0], stamp))
            with open(self.path, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
            return target
        except Exception as exc:
            log("坏配置备份失败：%s" % exc)
            return ""

    def _normalize(self):
        d = self.data
        fixes = []
        for key, default, low, high in INT_RULES:
            before = d.get(key, default)
            d[key] = _to_int(before, default, low, high)
            if str(before).strip() != str(d[key]):
                fixes.append((key, before, d[key]))
        lb = d.get("lunchBreak") or {}
        if not isinstance(lb, dict):
            lb = {}
        d["lunchBreak"] = {
            "enabled": bool(lb.get("enabled", True)),
            "start": lb.get("start", "12:00"),
            "end": lb.get("end", "13:00"),
        }
        # 时间字段统一在这里补零/校验，越界一律落回默认（见 _norm_hm 的说明）
        _norm_hm(d, "workStart", "09:00", fixes)
        _norm_hm(d, "workEnd", "18:00", fixes)
        _norm_hm(d["lunchBreak"], "start", "12:00", fixes, "lunchStart")
        _norm_hm(d["lunchBreak"], "end", "13:00", fixes, "lunchEnd")
        # 老配置里那个键叫 weekendsOnly，语义其实是"只在周一至周五提醒"，
        # 名字反着容易误导后来读代码的人。这里兼容旧键，新写入一律用新键。
        # 注意：默认值已经把 workdaysOnly 塞进 data 了，判断"用户到底写了哪个键"
        # 必须看 self.raw，只看 data 会永远认为新键存在、旧键被丢掉。
        src = self.raw if isinstance(self.raw, dict) else {}
        legacy = src.get("weekendsOnly", d.get("weekendsOnly"))
        if legacy is not None and "workdaysOnly" not in src:
            d["workdaysOnly"] = bool(legacy)
        d["workdaysOnly"] = bool(d.get("workdaysOnly", False))
        d.pop("weekendsOnly", None)
        d["sound"] = bool(d.get("sound", True))
        theme = str(d.get("theme", "auto")).lower()
        if theme not in ("auto", "dark", "light"):
            fixes.append(("theme", d.get("theme"), "auto"))
            theme = "auto"
        d["theme"] = theme
        unit = str(d.get("unit", "ml")).lower()
        if unit not in _UNITS:
            fixes.append(("unit", d.get("unit"), "ml"))
            unit = "ml"
        d["unit"] = unit
        msgs = d.get("messages") or []
        msgs = [str(m).strip() for m in msgs if str(m).strip()]
        d["messages"] = msgs or list(DEFAULT_MESSAGES)
        self.fixes = fixes
        if fixes:
            log("配置已纠正：%s" % "，".join(
                "%s %s→%s" % (fix_label(k), r, v) for k, r, v in fixes))

    def clamp_report(self):
        """
        返回被静默修正过的项：[(键名, 用户填的值, 实际生效的值), ...]
        保存后要在界面上回显，否则用户填了 99 分钟却悄悄变成 480，
        只会以为"软件不干活"。
        修正是在 _normalize 里做的（那里才知道"原始值"长什么样），这里只负责吐出去。
        """
        return list(self.fixes)

    def save(self):
        try:
            _write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2))
            return True
        except Exception as exc:
            log("配置保存失败：%s" % exc)
            return False

    def get(self, key, default=None):
        return self.data.get(key, default)

    def pick_message(self):
        """
        从"洗牌袋"里取文案。
        random.choice 会有大约 1/N 的概率连着两次同一条，一天十来次提醒里
        一天能看到两遍同一句话，用户会觉得这软件在敷衍。
        袋子里取完再重洗；重洗时把上一条摁到袋底，跨袋也不重样。
        """
        bag = self.data["messages"]
        if not self._bag:
            fresh = list(bag)
            try:
                random.shuffle(fresh)
            except Exception:
                pass
            if self._bag_last and len(fresh) > 1 and fresh[-1] == self._bag_last:
                fresh.insert(0, fresh.pop())
            self._bag = fresh
        msg = self._bag.pop()
        self._bag_last = msg
        return msg


# ---------------------------------------------------------------- 记录


class Store(object):
    """每天一个 JSON 文件，放在 data/ 下。"""

    def __init__(self, directory=None):
        self.dir = directory or DATA_DIR
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, day):
        return os.path.join(self.dir, "%s.json" % day)

    def _read(self, day):
        try:
            with open(self._path(day), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {"date": day, "logs": [], "celebrated": False}

    def _write(self, day, obj):
        try:
            _write_text(self._path(day), json.dumps(obj, ensure_ascii=False, indent=2))
        except Exception as exc:
            log("记录写入失败：%s" % exc)

    def today_key(self, now=None):
        return (now or dt.datetime.now()).strftime("%Y-%m-%d")

    def total(self, day=None):
        day = day or self.today_key()
        obj = self._read(day)
        return sum(int(x.get("amount", 0)) for x in obj.get("logs", []))

    def add(self, amount, now=None):
        now = now or dt.datetime.now()
        day = self.today_key(now)
        obj = self._read(day)
        obj["date"] = day
        obj.setdefault("logs", []).append(
            {"time": now.strftime("%H:%M"), "amount": int(amount)}
        )
        self._write(day, obj)
        return self.total(day)

    def pop_last(self, day=None):
        """
        撤掉最后一条记录，返回 (毫升数, 剩余条数)；没东西可撤返回 (0, 0)。
        托盘手滑点一次"记一杯"就多 250 ml，以前只能自己翻 JSON 删，
        对一个记账型工具来说这是硬伤。
        """
        day = day or self.today_key()
        obj = self._read(day)
        logs = obj.get("logs") or []
        if not logs:
            return 0, 0
        last = logs.pop()
        obj["logs"] = logs
        self._write(day, obj)
        return int(last.get("amount", 0)), len(logs)

    def count(self, day=None):
        """今天已记几条，用来决定托盘菜单里"撤销上一次记录"能不能点。"""
        day = day or self.today_key()
        return len(self._read(day).get("logs") or [])

    def celebrated(self, day=None):
        day = day or self.today_key()
        return bool(self._read(day).get("celebrated", False))

    def mark_celebrated(self, day=None):
        day = day or self.today_key()
        obj = self._read(day)
        obj["celebrated"] = True
        self._write(day, obj)

    def clear_celebrated(self, day=None):
        """撤销记录把总量撤回到目标以下时，庆祝标记要一起收回，不然再喝回去也不会重弹达标。"""
        day = day or self.today_key()
        obj = self._read(day)
        if not obj.get("celebrated"):
            return
        obj["celebrated"] = False
        self._write(day, obj)

    def history(self, days=7):
        out = []
        today = dt.date.today()
        for i in range(days - 1, -1, -1):
            d = today - dt.timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            out.append((key, self.total(key)))
        return out

    def streak(self, goal, max_scan=120):
        """
        连续达标天数（截止到"今天或昨天"）。
        今天还没喝够不算断：早上的时候昨天是终点，昨天喝够了就还算连着。
        只往上数 max_scan 天，数据都被归档了也不至于把盘扫穿。
        """
        if goal <= 0:
            return 0
        today = dt.date.today()
        n = 0
        d = today
        if self.total(d.strftime("%Y-%m-%d")) >= goal:
            n += 1
        d -= dt.timedelta(days=1)
        for _ in range(max_scan):
            if self.total(d.strftime("%Y-%m-%d")) >= goal:
                n += 1
                d -= dt.timedelta(days=1)
                continue
            break
        return n

    def archive_old(self, keep_days=None, today=None):
        """
        把 keep_days 天以前的日文件挪进 data/archive/（只挪不删）。
        一天一个文件，跑几年就是上千个小文件，翻目录和备份都难受；
        但历史直接删掉更难受，所以是挪走 —— 需要的时候自己还能拿回来。
        返回挪走的文件数。
        """
        keep = int(keep_days or ARCHIVE_AFTER_DAYS)
        stamp = today or dt.date.today()
        count = 0
        try:
            names = [n for n in os.listdir(self.dir) if n.endswith(".json")]
        except Exception:
            return 0
        for name in names:
            stem = name[:-5]
            try:
                d = dt.datetime.strptime(stem, "%Y-%m-%d").date()
            except Exception:
                continue          # 不是日期文件就别动（icon.ico 之类不在这个列表里）
            if (stamp - d).days <= keep:
                continue
            try:
                src = os.path.join(self.dir, name)
                dst_dir = os.path.join(self.dir, "archive", stem[:4])
                os.makedirs(dst_dir, exist_ok=True)
                dst = os.path.join(dst_dir, name)
                if os.path.exists(dst):
                    os.remove(src)
                else:
                    os.replace(src, dst)
                count += 1
            except Exception as exc:
                log("归档 %s 失败：%s" % (name, exc))
        if count:
            log("历史数据归档：%d 个超过 %d 天的文件已挪到 data/archive/" % (count, keep))
        return count


# ---------------------------------------------------------------- 时间


def parse_hm(text, fallback=0):
    """
    '09:30' -> 570 分钟；写错或越界（"25:00"、"18:99"）一律回落到 fallback。
    以前这里只 catch 异常：'25:00' 语法上能拆开，于是变成 1500 分钟，
    "当前时间永远小于开始时间"→ 一整天一条提醒都没有，而且日志里毫无痕迹。
    """
    v = valid_hm(text)
    if v is None:
        return fallback
    h, m = v.split(":")
    return int(h) * 60 + int(m)


def now_minutes(moment=None):
    moment = moment or dt.datetime.now()
    return moment.hour * 60 + moment.minute


def in_window(cur, start, end):
    """
    分钟数落在 [start, end] 里吗？start > end 视为跨夜窗口（20:00-06:00 上夜班）。
    以前跨夜窗口直接判"永远不在时段"，夜班同学根本没有可用配置。
    """
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end


def is_active(cfg, moment=None):
    """返回 (是否该提醒, 原因)。"""
    moment = moment or dt.datetime.now()
    data = cfg.data

    if data.get("workdaysOnly") and moment.weekday() >= 5:
        return False, "周末（只在周一到周五提醒）"

    start = parse_hm(data.get("workStart", "09:00"), 9 * 60)
    end = parse_hm(data.get("workEnd", "18:00"), 18 * 60)
    cur = now_minutes(moment)

    if not in_window(cur, start, end):
        return False, "不在工作时段 %s" % fmt_window(cfg)

    lb = data.get("lunchBreak") or {}
    if lb.get("enabled"):
        ls = parse_hm(lb.get("start", "12:00"), 12 * 60)
        le = parse_hm(lb.get("end", "13:00"), 13 * 60)
        if in_window(cur, ls, le):
            return False, "午休免打扰 %s-%s" % (lb.get("start"), lb.get("end"))

    return True, "工作时段"


def fmt_window(cfg):
    """时段文案；跨夜时段标出来，否则界面上看着像填反了。"""
    d = cfg.data
    text = "%s - %s" % (d.get("workStart"), d.get("workEnd"))
    if parse_hm(d.get("workStart"), 0) > parse_hm(d.get("workEnd"), 1439):
        text += "（跨夜）"
    return text


# ---------------------------------------------------------------- 单位
#
# 存储永远是毫升（数据文件、配置里的 amountPerReminder / dailyGoal 都是 ml），
# 只有显示这一层换算。这样换单位不会动历史数据，也不会出现两套数字混在一个文件里。


def unit_label(cfg):
    return str(cfg.get("unit", "ml")).lower() if cfg else "ml"


def to_display(cfg, ml):
    """把毫升数换算成界面要显示的数值字符串（不带货单位名称）。"""
    try:
        ml = float(ml)
    except Exception:
        return "0"
    if unit_label(cfg) == "oz":
        return ("%.1f" % (ml / ML_PER_OZ)).rstrip("0").rstrip(".")
    return fmt_int(ml)


def fmt_vol(cfg, ml):
    """'250 ml' / '8.5 oz'：界面上一切带单位的水量都走这里。"""
    return "%s %s" % (to_display(cfg, ml), unit_label(cfg))


def fmt_int(n):
    """12345 -> '12,345'（千分位）。毫升数上千后不带分隔符很难一眼读出量级。"""
    try:
        return "{:,}".format(int(round(n)))
    except Exception:
        return str(n)

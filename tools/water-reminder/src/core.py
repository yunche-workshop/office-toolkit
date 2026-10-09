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
    "messages": list(DEFAULT_MESSAGES),
    "goalReachedMessage": "今日目标达成，你今天喝够了，剩下的随意",
}

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


def base_dir():
    """数据目录：exe 同目录（便携）；不可写时回落到 %APPDATA%。"""
    if is_frozen():
        candidate = os.path.dirname(os.path.abspath(sys.executable))
    else:
        candidate = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        probe = os.path.join(candidate, ".write_test")
        with open(probe, "w") as fh:
            fh.write("1")
        os.remove(probe)
        return candidate
    except Exception:
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        fallback = os.path.join(root, APP_NAME)
        os.makedirs(fallback, exist_ok=True)
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
        self.created_new = False
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
            log("配置读取失败，使用默认值：%s" % exc)
        self._normalize()
        return self.data

    def _normalize(self):
        d = self.data
        d["intervalMinutes"] = _to_int(d.get("intervalMinutes"), 60, 5, 480)
        d["amountPerReminder"] = _to_int(d.get("amountPerReminder"), 250, 50, 2000)
        d["dailyGoal"] = _to_int(d.get("dailyGoal"), 2000, 200, 10000)
        d["snoozeMinutes"] = _to_int(d.get("snoozeMinutes"), 15, 5, 120)
        lb = d.get("lunchBreak") or {}
        d["lunchBreak"] = {
            "enabled": bool(lb.get("enabled", True)),
            "start": lb.get("start", "12:00"),
            "end": lb.get("end", "13:00"),
        }
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
        msgs = d.get("messages") or []
        msgs = [str(m).strip() for m in msgs if str(m).strip()]
        d["messages"] = msgs or list(DEFAULT_MESSAGES)

    def clamp_report(self):
        """
        返回被静默修正过的项：[(键名, 用户填的值, 实际生效的值), ...]
        保存后要在界面上回显，否则用户填了 99 分钟却悄悄变成 480，
        只会以为"软件不干活"。
        """
        out = []
        rules = {
            "intervalMinutes": (5, 480),
            "amountPerReminder": (50, 2000),
            "dailyGoal": (200, 10000),
            "snoozeMinutes": (5, 120),
        }
        for key, (low, high) in rules.items():
            raw = self.raw.get(key)
            if raw is None:
                continue
            try:
                v = int(str(raw).strip())
            except Exception:
                out.append((key, raw, self.data[key]))
                continue
            if v < low or v > high:
                out.append((key, v, self.data[key]))
        return out

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
        return random.choice(self.data["messages"])


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

    def celebrated(self, day=None):
        day = day or self.today_key()
        return bool(self._read(day).get("celebrated", False))

    def mark_celebrated(self, day=None):
        day = day or self.today_key()
        obj = self._read(day)
        obj["celebrated"] = True
        self._write(day, obj)

    def history(self, days=7):
        out = []
        today = dt.date.today()
        for i in range(days - 1, -1, -1):
            d = today - dt.timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            out.append((key, self.total(key)))
        return out


# ---------------------------------------------------------------- 时间


def parse_hm(text, fallback=0):
    """'09:30' -> 570 分钟"""
    try:
        h, m = str(text).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return fallback


def now_minutes(moment=None):
    moment = moment or dt.datetime.now()
    return moment.hour * 60 + moment.minute


def is_active(cfg, moment=None):
    """返回 (是否该提醒, 原因)。"""
    moment = moment or dt.datetime.now()
    data = cfg.data

    if data.get("workdaysOnly") and moment.weekday() >= 5:
        return False, "周末（只在周一到周五提醒）"

    start = parse_hm(data.get("workStart", "09:00"), 9 * 60)
    end = parse_hm(data.get("workEnd", "18:00"), 18 * 60)
    cur = now_minutes(moment)

    if cur < start or cur > end:
        return False, "不在工作时段 %s-%s" % (data.get("workStart"), data.get("workEnd"))

    lb = data.get("lunchBreak") or {}
    if lb.get("enabled"):
        ls = parse_hm(lb.get("start", "12:00"), 12 * 60)
        le = parse_hm(lb.get("end", "13:00"), 13 * 60)
        if le < ls:  # 跨零点保护
            le = 24 * 60
        if ls <= cur <= le:
            return False, "午休免打扰 %s-%s" % (lb.get("start"), lb.get("end"))

    return True, "工作时段"


def fmt_window(cfg):
    d = cfg.data
    return "%s - %s" % (d.get("workStart"), d.get("workEnd"))

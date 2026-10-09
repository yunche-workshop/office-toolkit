# -*- coding: utf-8 -*-
"""
smoke.py —— 开发自检（不弹窗）

只验证纯逻辑部分：配置、存储、时间判断、图标生成、Win32 只读接口。
图形界面请用 --minimized 实际启动后肉眼确认。
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import core  # noqa: E402
import icon  # noqa: E402
import win32ext  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    flag = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print("[%s] %s %s" % (flag, name, extra))


# ---------------- 时间判断 ----------------
tmp_dir = tempfile.mkdtemp(prefix="wr_test_")
cfg_path = os.path.join(tmp_dir, "config.json")
cfg = core.Config(cfg_path)


def at(h, m, weekday=0):
    # 2026-01-05 是周一
    return dt.datetime(2026, 1, 5 + weekday, h, m)


check("工作时段内提醒", core.is_active(cfg, at(10, 0))[0])
check("上班前不提醒", not core.is_active(cfg, at(8, 0))[0])
check("下班后不提醒", not core.is_active(cfg, at(19, 0))[0])
check("午休不提醒", not core.is_active(cfg, at(12, 30))[0])
check("午休结束后恢复", core.is_active(cfg, at(13, 30))[0])

cfg.data["workdaysOnly"] = True
check("周末不提醒（只在周一至周五）", not core.is_active(cfg, at(10, 0, weekday=5))[0])
cfg.data["workdaysOnly"] = False
check("周末提醒（开关关）", core.is_active(cfg, at(10, 0, weekday=5))[0])

# 老配置里的 weekendsOnly 键名是反的，加载时要迁移成新键且保留语义
legacy = os.path.join(tmp_dir, "legacy.json")
with open(legacy, "w", encoding="utf-8") as fh:
    fh.write('{"weekendsOnly": true, "intervalMinutes": 99999, "dailyGoal": "abc"}')
lcfg = core.Config(legacy)
check("旧键 weekendsOnly 迁移", lcfg.get("workdaysOnly") is True)
check("旧键已从数据里移除", "weekendsOnly" not in lcfg.data)
check("越界间隔被夹到上限", lcfg.get("intervalMinutes") == 480)
check("非整数目标回落默认", lcfg.get("dailyGoal") == 2000)
check("越界项被记录", any(k == "intervalMinutes" for k, _r, _e in lcfg.clamp_report()),
      str(lcfg.clamp_report()))

check("时间解析 09:30", core.parse_hm("09:30") == 570)
check("非法时间回落", core.parse_hm("abc", 999) == 999)

# ---------------- 存储 ----------------
store = core.Store(os.path.join(tmp_dir, "data"))
key = store.today_key()
store.add(250)
store.add(250)
check("累计水量", store.total(key) == 500, "got %s" % store.total(key))
check("达标标记默认关闭", store.celebrated(key) is False)
store.mark_celebrated(key)
check("达标标记写入", store.celebrated(key) is True)
check("历史 7 天", len(store.history(7)) == 7)

# 历史日期写入也要能被读到
past = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
store._write(past, {"date": past, "logs": [{"time": "09:00", "amount": 300}]})
check("昨日数据读取", core.Store(os.path.join(tmp_dir, "data")).total(past) == 300)

# ---------------- 配置 ----------------
cfg.data["intervalMinutes"] = -5
cfg._normalize()
check("非法间隔被纠正", cfg.data["intervalMinutes"] == 5)
cfg.data["messages"] = ["", "  ", "有效的文案"]
cfg._normalize()
check("空文案被清理", cfg.data["messages"] == ["有效的文案"])
check("配置可保存读取", cfg.save() and core.Config(cfg_path).get("intervalMinutes") == 5)
check("保存是原子写（不留 .tmp）", not os.path.exists(cfg_path + ".tmp"))
check("配置文件是完整 JSON",
      isinstance(__import__("json").load(open(cfg_path, encoding="utf-8")), dict))

# 日志轮转：超过上限就换成 .1，常驻跑几个月不会把盘写满
core.LOG_PATH = os.path.join(tmp_dir, "run.log")
core.LOG_MAX_BYTES = 64
with open(core.LOG_PATH, "w", encoding="utf-8") as fh:
    fh.write("x" * 200)
core.log("触发轮转")
check("日志超上限会轮转", os.path.exists(core.LOG_PATH + ".1")
      and os.path.getsize(core.LOG_PATH) < 64 + 200,
      "%s / %s" % (os.path.getsize(core.LOG_PATH), os.path.getsize(core.LOG_PATH + ".1")))
core.LOG_MAX_BYTES = 512 * 1024

# ---------------- emoji 过滤 ----------------
check("emoji 过滤", core.strip_emoji("起来接水 \U0001F41F") == "起来接水")
check("纯 emoji 变空", core.strip_emoji("\U0001F4A7") == "")

# ---------------- 图标 ----------------
ico_bytes = icon.build_ico_bytes()
check("兜底图标大小合理", 20 * 1024 < len(ico_bytes) < 80 * 1024, "%d bytes" % len(ico_bytes))
check("兜底图标头正确", ico_bytes[:4] == b"\x00\x00\x01\x00")
ico_path = os.path.join(tmp_dir, "icon.ico")
icon.ensure_icon(ico_path)
check("图标文件可写", os.path.getsize(ico_path) == len(ico_bytes))
check("图标二次调用复用", icon.ensure_icon(ico_path) == ico_path)


def ico_sizes(raw):
    """从 ICO 字节里读出各帧边长，用来确认多尺寸真的都在。"""
    import struct

    n = struct.unpack("<H", raw[4:6])[0]
    out = []
    for i in range(n):
        e = struct.unpack("<BBBBHHII", raw[6 + 16 * i : 22 + 16 * i])
        out.append(e[0] or 256)
    return sorted(out)


check("兜底图标含常用尺寸", set(ico_sizes(ico_bytes)) >= {16, 32, 48}, str(ico_sizes(ico_bytes)))

bundled = icon.bundled_icon()
check("美术图标资源存在", bool(bundled), bundled or core.resource_path("app.ico") or "(未找到)")
if bundled:
    with open(bundled, "rb") as fh:
        raw = fh.read()
    check("美术图标是多尺寸 ICO", set(ico_sizes(raw)) >= {16, 24, 32, 48, 256},
          "%s / %.0f KB" % (ico_sizes(raw), len(raw) / 1024.0))
    check("托盘优先用美术图标", icon.icon_path(tmp_dir) == bundled)
else:
    check("资源缺失时退回自算图标",
          icon.icon_path(tmp_dir) == os.path.join(tmp_dir, "icon.ico"))

bad = os.path.join(tmp_dir, "bad.ico")
with open(bad, "wb") as fh:
    fh.write(b"GIF89a" + b"\x00" * 100)
check("坏文件不当图标", icon.is_ico_file(bad) is False)
check("空文件不当图标", icon.is_ico_file(ico_path + ".none") is False)

# ---------------- Win32 只读接口 ----------------
check("系统版本号可读", win32ext.windows_build() > 0, "build %s" % win32ext.windows_build())
area = win32ext.work_area()
check("工作区有效", area[2] > area[0] and area[3] > area[1], str(area))
check("按坐标取所在显示器",
      win32ext.monitor_work_area(area[0] + 5, area[1] + 5)[2] > area[0])
vd = win32ext.virtual_desktop()
check("虚拟桌面边界有效", vd[2] > vd[0] and vd[3] > vd[1], str(vd))
cx, cy = win32ext.clamp_into_view(vd[2] + 5000, vd[3] + 5000, 400, 200)
check("屏幕外坐标被拉回", vd[0] <= cx <= vd[2] and vd[1] <= cy <= vd[3],
      "%d,%d" % (cx, cy))
cx2, cy2 = win32ext.clamp_into_view(-99999, -99999, 400, 200)
check("左上越界同样拉回", cx2 >= vd[0] - 400 and cy2 >= vd[1], "%d,%d" % (cx2, cy2))
check("自启动状态可读", isinstance(win32ext.get_autostart(), bool))
cmd = win32ext.autostart_command()
check("自启命令行引号成对", cmd.count('"') % 2 == 0 and cmd.startswith('"'), cmd)
check("自启命令行能取回程序路径",
      os.path.exists(win32ext.autostart_target(cmd)), win32ext.autostart_target(cmd))
check("指向不存在程序的自启算没开",
      win32ext.autostart_target('"C:\\不存在\\a.exe" --minimized') == "C:\\不存在\\a.exe")

inst_a = win32ext.SingleInstance("WaterReminder_SmokeTest_2026")
inst_b = win32ext.SingleInstance("WaterReminder_SmokeTest_2026")
check("单实例互斥体生效", inst_b.already_running is True)
inst_a.release()
inst_b.release()

print("")
print("数据目录：%s" % tmp_dir)
print("结果：%s" % ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)

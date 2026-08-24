#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
value_locate.py — 在 ECU/TCU bin 里定位一个"已知物理量+单位"的标定值。

idle-speed-calibration 与 speed-limiter-removal 两个技能共享的执行引擎。
核心问题:标定值(怠速目标 RPM、最高限速 km/h、断油转速…)以 raw 整数按某个 factor 存,
而 factor/字节序未知。本引擎【多缩放 × 双字节序】穷举解码,把落在目标物理带内的位置挑出来,
再按可信度排序。

两种真实形态,分开处理:
  * 曲线型(怠速目标随水温递减):有结构信号 → --curve 探测单调 run,较可靠。
  * 标量型(最高限速常是一个数):0xFA(250) 在 bin 里满地都是 → 单值搜索天生高误报。
    本引擎只给【候选】不给答案;最可靠是 --diff(原厂 vs 已改文件对比)和 ghidra 看比较逻辑。

用法:
    # 标量搜索:在 bin 里找"250 km/h"的候选(所有速度缩放×字节序)
    python value_locate.py --bin ECU.bin --preset vehicle_speed --value 250 --tol 2
    # 怠速曲线探测:找随温递减、落在怠速 RPM 带的短单调 run
    python value_locate.py --bin ECU.bin --preset idle_rpm --curve
    # 断油转速标量
    python value_locate.py --bin ECU.bin --preset rev_limit --value 6500 --tol 50
    # 通用:自定义单位缩放
    python value_locate.py --bin ECU.bin --generic --value 250 --tol 2 --factor 0.0625 --dtype u16
    # 金标准:原厂 vs 已解限文件 diff,变动处按速度缩放解码
    python value_locate.py --bin STOCK.bin --diff DELIMITED.bin --preset vehicle_speed
    python value_locate.py --selftest

纪律(doctrine):
  * 输出是【候选 + 可信度】,不是结论。标量命中必须再经 ghidra 比较逻辑 / A2L 标签 / diff / 改后实测确认。
  * 缩放/字节序穷举命中不代表语义正确——同一 raw 在别的功能表里也可能凑巧落带。
  * 写盘后 checksum 交 checksum-crc-engine;单表语义交 ecu-map-analysis;字节读写交 bin 引擎。
"""
import sys, struct, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# dtype -> (struct 字符, 字节宽, 是否有符号)
DT = {"u8": ("B", 1), "u16": ("H", 2), "u32": ("I", 4), "s16": ("h", 2)}

# 预设缩放候选:phys = raw*factor (+offset)。覆盖各家常见存法。
# 每项: (标签, dtype, factor, offset, 是否"干净规范缩放"用于加分)
PRESETS = {
    "vehicle_speed": {   # 单位 km/h
        "band": (60, 320), "unit": "km/h",
        "scalings": [
            ("kmh u8 直存",    "u8",  1.0,     0.0, True),
            ("kmh u16 直存",   "u16", 1.0,     0.0, True),
            ("kmh u16 /100",   "u16", 0.01,    0.0, False),
            ("kmh u16 /128",   "u16", 1/128.0, 0.0, False),
            ("kmh u16 /256",   "u16", 1/256.0, 0.0, False),
            ("kmh u16 x0.0625","u16", 0.0625,  0.0, False),
            ("kmh u16 x0.1",   "u16", 0.1,     0.0, False),
            ("mph u8 直存",    "u8",  1.60934, 0.0, False),
            ("mph u16 直存",   "u16", 1.60934, 0.0, False),
        ],
    },
    "idle_rpm": {        # 单位 RPM,怠速带
        "band": (500, 1300), "unit": "RPM",
        "scalings": [
            ("rpm u16 直存", "u16", 1.0,   0.0, True),
            ("rpm u16 /4",   "u16", 0.25,  0.0, True),
            ("rpm u16 /8",   "u16", 0.125, 0.0, False),
            ("rpm u8 x40",   "u8",  40.0,  0.0, False),
            ("rpm u8 x32",   "u8",  32.0,  0.0, False),
            ("rpm u8 x50",   "u8",  50.0,  0.0, False),
            ("rpm u16 x10",  "u16", 10.0,  0.0, False),
        ],
    },
    "rev_limit": {       # 单位 RPM,断油/限转带
        "band": (4500, 9500), "unit": "RPM",
        "scalings": [
            ("rpm u16 直存", "u16", 1.0,   0.0, True),
            ("rpm u16 /4",   "u16", 0.25,  0.0, True),
            ("rpm u16 /8",   "u16", 0.125, 0.0, False),
            ("rpm u16 x10",  "u16", 10.0,  0.0, False),
        ],
    },
}


def _iter_raw(buf, dtype, endian):
    """产出 (offset, raw) —— 按 dtype/字节序遍历。u8 无字节序。"""
    ch, w = DT[dtype]
    fmt = ("<" if endian == "le" else ">") + ch
    n = len(buf)
    for off in range(0, n - w + 1):
        yield off, struct.unpack_from(fmt, buf, off)[0]


def _endians_for(dtype):
    return ["le"] if DT[dtype][1] == 1 else ["le", "be"]


def find_value(buf, preset, target, tol):
    """标量搜索:所有预设缩放×字节序,找解码落 [target-tol, target+tol] 的位置。"""
    hits = []
    for label, dtype, factor, offset, clean in preset["scalings"]:
        lo, hi = target - tol, target + tol
        for endian in _endians_for(dtype):
            ch, w = DT[dtype]
            fmt = ("<" if endian == "le" else ">") + ch
            for off in range(0, len(buf) - w + 1):
                raw = struct.unpack_from(fmt, buf, off)[0]
                phys = raw * factor + offset
                if lo <= phys <= hi:
                    hits.append({"offset": off, "dtype": dtype, "endian": endian,
                                 "raw": raw, "phys": phys, "scaling": label,
                                 "clean": clean, "width": w})
    return hits


def _cluster_score(hits):
    """聚类:同(缩放,值)在 64 字节窗口内出现多次 → 可能是"警告+切断"多阈值,加分。
    给每个 hit 打 score 并标注 context。"""
    by = {}
    for h in hits:
        by.setdefault((h["scaling"], round(h["phys"], 1)), []).append(h)
    for h in hits:
        group = by[(h["scaling"], round(h["phys"], 1))]
        near = sum(1 for g in group if abs(g["offset"] - h["offset"]) <= 64 and g is not h)
        score = 0.0
        score += 3 if h["clean"] else 0          # 规范缩放(直存整数)更可信
        score += 2 if h["raw"] == round(h["phys"]) and h["clean"] else 0  # 整数直存
        score += min(near, 3) * 1.5              # 邻近聚类(多阈值)
        score += (h["width"] - 1) * 2            # 位宽稀有度:多字节巧合远罕于单字节(u16>u8)
        h["cluster"] = near + 1
        h["score"] = score
    return hits


def detect_curve(buf, preset, min_run=6, max_run=40):
    """曲线探测(怠速):找解码落物理带、且【多为单调】的短 run(随水温递减/递增)。
    返回候选曲线 [{start,stride,dtype,endian,scaling,count,values,trend,score}]。"""
    lo, hi = preset["band"]
    out = []
    for label, dtype, factor, offset, clean in preset["scalings"]:
        ch, w = DT[dtype]
        for endian in _endians_for(dtype):
            fmt = ("<" if endian == "le" else ">") + ch
            stride = w
            n = len(buf)
            off = 0
            while off + stride <= n:
                vals, offs, k = [], [], off
                while k + w <= n:
                    raw = struct.unpack_from(fmt, buf, k)[0]
                    phys = raw * factor + offset
                    if not (lo <= phys <= hi):
                        break
                    vals.append(phys)
                    offs.append(k)
                    k += stride
                run = len(vals)
                if min_run <= run <= max_run:
                    dec = sum(1 for a, b in zip(vals, vals[1:]) if b <= a)
                    inc = sum(1 for a, b in zip(vals, vals[1:]) if b >= a)
                    mono = max(dec, inc)
                    if mono >= run - 2 and len(set(round(v) for v in vals)) >= 3:
                        trend = "递减(冷→热典型)" if dec >= inc else "递增"
                        score = run + (run if clean else 0) + (run if dec >= inc else 0)
                        out.append({"start": off, "stride": stride, "dtype": dtype,
                                    "endian": endian, "scaling": label, "count": run,
                                    "values": [round(v, 1) for v in vals], "trend": trend,
                                    "score": score})
                        off = k
                        continue
                off += 1
    out.sort(key=lambda t: -t["score"])
    # 去重:同起点不同缩放,保留最高分
    seen, uniq = set(), []
    for t in out:
        if t["start"] in seen:
            continue
        seen.add(t["start"])
        uniq.append(t)
    return uniq


def diff_decode(buf_a, buf_b, preset):
    """双 bin diff:变动字节区间,按预设速度缩放解码"改前→改后",定位限速类改动。"""
    if len(buf_a) != len(buf_b):
        print("[!] 两文件长度不同(%d vs %d),仅比较公共前缀。" % (len(buf_a), len(buf_b)))
    n = min(len(buf_a), len(buf_b))
    # 收集变动区间
    regions, i = [], 0
    while i < n:
        if buf_a[i] != buf_b[i]:
            j = i
            while j < n and buf_a[j] != buf_b[j]:
                j += 1
            regions.append((i, j))
            i = j
        else:
            i += 1
    out = []
    for (s, e) in regions:
        # 在变动区间起点两侧对齐尝试 u16 解码(限速多为 u16)
        for label, dtype, factor, offset, clean in preset["scalings"]:
            ch, w = DT[dtype]
            if s + w > n:
                continue
            for endian in _endians_for(dtype):
                fmt = ("<" if endian == "le" else ">") + ch
                ra = struct.unpack_from(fmt, buf_a, s)[0]
                rb = struct.unpack_from(fmt, buf_b, s)[0]
                pa, pb = ra * factor + offset, rb * factor + offset
                lo, hi = preset["band"]
                if lo <= pa <= hi or lo <= pb <= hi:
                    out.append({"offset": s, "len": e - s, "scaling": label,
                                "dtype": dtype, "endian": endian,
                                "phys_a": round(pa, 1), "phys_b": round(pb, 1)})
    return regions, out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def report_value(hits, preset, target, tol):
    hits = _cluster_score(hits)
    hits.sort(key=lambda h: (-h["score"], h["offset"]))
    print("=" * 80)
    print("标量定位: 目标 %g±%g %s" % (target, tol, preset["unit"]))
    print("命中 %d 处(多缩放×双字节序穷举)。按可信度排序,前 20:" % len(hits))
    print("=" * 80)
    if not hits:
        print("未命中。可能:该值用了未列缩放 / 不是标量而在曲线里 / 单位不同。")
        print("最可靠做法:--diff 原厂vs已改文件,或用 ghidra 找车速比较逻辑。")
        return
    print("%-10s %-6s %-4s %-8s %-16s %-5s %s" %
          ("偏移", "类型", "序", "raw", "解码", "聚类", "缩放"))
    print("-" * 80)
    for h in hits[:20]:
        print("0x%08X %-6s %-4s %-8d %-16s x%-4d %s" %
              (h["offset"], h["dtype"], h["endian"], h["raw"],
               "%.2f %s" % (h["phys"], preset["unit"]), h["cluster"], h["scaling"]))
    print("-" * 80)
    print("★ 标量搜索天生高误报(如 0xFA=250 到处都是)。候选须再确认:")
    print("  1) ghidra 找'车速>阈值'的比较指令(最可靠) 2) A2L 标签 3) --diff 对比法 4) 改后实测。")


def report_curve(cands, preset):
    print("=" * 80)
    print("曲线探测: 在 %s 带 [%d,%d] 找单调 run(怠速目标典型随水温递减)" %
          (preset["unit"], preset["band"][0], preset["band"][1]))
    print("候选 %d 条,前 12:" % len(cands))
    print("=" * 80)
    if not cands:
        print("未找到。可能:怠速目标缩放未列 / 是标量 / 与其它 RPM 表混叠。")
        return
    for c in cands[:12]:
        print("@0x%08X %s %s [%s] %d点 %s" %
              (c["start"], c["dtype"], c["endian"], c["scaling"], c["count"], c["trend"]))
        print("   值: %s" % (" ".join("%.0f" % v for v in c["values"][:16])))
    print("-" * 80)
    print("提醒: 落带+单调只是形态证据。确认该曲线是怠速目标(而非其它 RPM 表):")
    print("  对照 X 轴是否为水温、用 ecu-map-analysis 拆解、或 A2L 标签。")


def report_diff(regions, decoded, preset):
    print("=" * 80)
    print("双 bin diff: %d 个变动区间;其中按 %s 缩放落带的:" % (len(regions), preset["unit"]))
    print("=" * 80)
    if not decoded:
        print("变动区间无一按速度缩放落带。可能改的不是限速,或缩放未列。")
        print("变动区间偏移:", ", ".join("0x%X(%dB)" % (s, e - s) for s, e in regions[:20]))
        return
    for d in decoded[:30]:
        print("0x%08X %-6s %-3s %-16s  改前 %s → 改后 %s  [%s]" %
              (d["offset"], d["dtype"], d["endian"], d["scaling"],
               "%.1f" % d["phys_a"], "%.1f" % d["phys_b"], preset["unit"]))
    print("-" * 80)
    print("★ diff 是限速定位金标准:两文件仅限速不同,变动处按速度缩放'改前→改后'一目了然。")


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("ok " if c else "FAIL ") + m)
        ok = ok and c

    import random
    random.seed(3)
    n = 0x800
    # 噪声用 0..99 字节:避开幻数(250=0xFA、idle/rev 的高字节均 >99),使埋入值可确定性验证。
    # 真实 bin 噪声是全域的,故技能强调标量搜索高误报——那是语义现实,不影响引擎正确性测试。
    buf = bytearray(random.randrange(100) for _ in range(n))

    # 埋一个限速标量:250 km/h u16-LE 直存,放两处(警告+切断)相邻
    struct.pack_into("<H", buf, 0x100, 250)
    struct.pack_into("<H", buf, 0x108, 250)
    # 埋一条怠速目标曲线:u16-LE,随温递减 950..640(8 点),/1 直存
    idle = [950, 900, 850, 800, 760, 720, 680, 640]
    for i, v in enumerate(idle):
        struct.pack_into("<H", buf, 0x300 + i * 2, v)

    # 标量搜索 250
    hits = _cluster_score(find_value(bytes(buf), PRESETS["vehicle_speed"], 250, 2))
    hits.sort(key=lambda h: (-h["score"], h["offset"]))
    top = hits[0]
    chk(top["offset"] in (0x100, 0x108) and top["scaling"].startswith("kmh u16 直存"),
        "限速 250 u16直存 排在最前(聚类+规范缩放加分)")
    chk(any(h["offset"] == 0x100 and h["cluster"] >= 2 for h in hits),
        "0x100 识别到邻近聚类(多阈值)")

    # 怠速曲线探测
    cands = detect_curve(bytes(buf), PRESETS["idle_rpm"])
    hit = [c for c in cands if c["start"] == 0x300]
    chk(len(hit) == 1, "怠速曲线 @0x300 被探到")
    if hit:
        chk(hit[0]["count"] == 8 and "递减" in hit[0]["trend"], "8 点递减曲线识别正确")
        chk(hit[0]["values"][0] == 950 and hit[0]["values"][-1] == 640, "曲线值解码正确 950→640")

    # diff:造一个"已解限"副本,把 250 改成 300
    buf2 = bytearray(buf)
    struct.pack_into("<H", buf2, 0x100, 300)
    struct.pack_into("<H", buf2, 0x108, 300)
    regions, decoded = diff_decode(bytes(buf), bytes(buf2), PRESETS["vehicle_speed"])
    chk(any(d["offset"] == 0x100 and d["phys_a"] == 250 and d["phys_b"] == 300 for d in decoded),
        "diff 定位到 0x100 限速 250→300")

    # rev_limit 复用同引擎
    struct.pack_into("<H", buf, 0x200, 6500)
    hits_r = find_value(bytes(buf), PRESETS["rev_limit"], 6500, 20)
    chk(any(h["offset"] == 0x200 and h["scaling"].startswith("rpm u16 直存") for h in hits_r),
        "断油转速 6500 u16直存 命中")

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Locate a known-magnitude calibration value in an ECU bin")
    ap.add_argument("--bin", dest="binpath", default=None)
    ap.add_argument("--preset", choices=sorted(PRESETS), default=None)
    ap.add_argument("--generic", action="store_true", help="自定义缩放(需 --factor --dtype)")
    ap.add_argument("--value", type=float, default=None, help="目标物理值(标量搜索)")
    ap.add_argument("--tol", type=float, default=2.0, help="容差(同单位)")
    ap.add_argument("--curve", action="store_true", help="曲线探测(怠速目标随温单调)")
    ap.add_argument("--diff", dest="diffpath", default=None, help="对比另一个 bin(限速金标准)")
    ap.add_argument("--factor", type=float, default=1.0)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--dtype", choices=sorted(DT), default="u16")
    ap.add_argument("--unit", default="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not a.binpath:
        ap.error("需要 --bin(或 --selftest)")
    buf = open(a.binpath, "rb").read()

    if a.generic:
        preset = {"band": (a.value - a.tol, a.value + a.tol) if a.value else (0, 1e9),
                  "unit": a.unit,
                  "scalings": [("generic", a.dtype, a.factor, a.offset, True)]}
    elif a.preset:
        preset = PRESETS[a.preset]
    else:
        ap.error("需要 --preset 或 --generic")

    if a.diffpath:
        b2 = open(a.diffpath, "rb").read()
        regions, decoded = diff_decode(buf, b2, preset)
        report_diff(regions, decoded, preset)
    elif a.curve:
        report_curve(detect_curve(buf, preset), preset)
    elif a.value is not None:
        report_value(find_value(buf, preset, a.value, a.tol), preset, a.value, a.tol)
    else:
        ap.error("需要 --value(标量)或 --curve(曲线)或 --diff(对比)之一")


if __name__ == "__main__":
    main()

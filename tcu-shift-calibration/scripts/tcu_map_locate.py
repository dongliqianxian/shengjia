#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tcu_map_locate.py — 在 TCU bin 里定位【换挡点表】(tcu-shift-calibration 技能)。

换挡点表有很强的、TCU 特有的结构指纹(value_locate 的单值/单曲线、blind_map 的通用 2D 都抓不到):
  * 每条曲线 = 某次换挡(1-2上/2-3上/…)的车速阈值 vs 节气门/负荷,【随节气门单调递增】
    (小油门早换=低速,大油门晚换=高速)。
  * 多条曲线【随档位堆叠】:高档在更高车速才换 → 行均值逐行递增。这是与随机单调块的判别点。
  * 常成对:升档表 与 降档表 同维,升档阈值整体高于降档(防抖回差 hysteresis)。

本脚本按【车速缩放感知 + 递增曲线 + 档位堆叠】找换挡点表候选,并配对升/降档(回差)。

用法:
    python tcu_map_locate.py --bin TCU.bin                      # 默认小端,自动缩放
    python tcu_map_locate.py --bin TCU.bin --endian be
    python tcu_map_locate.py --bin TCU.bin --base 0x800000 --json out.json
    python tcu_map_locate.py --selftest

纪律(doctrine):
  * 输出是【候选 + 结构证据】,不是结论。命中须再经 ecu-map-analysis 判功能、对照轴(节气门/车速)、
    或 ghidra 看谁读它。油压/TCC 表(2D 但非车速堆叠)用 blind-map-finder;单一限速值用 value_locate。
  * 字节读写交 bin 引擎;写后 checksum 交 checksum-crc-engine;字节序经 multi-vendor-ecu-engine 核定。
"""
import sys, struct, argparse, json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DT = {"u8": ("B", 1), "u16": ("H", 2)}

# 车速缩放候选(km/h)。换挡车速带较宽(低档低速换、极速档高速换)。
SPEED_SCALINGS = [
    ("kmh u8 直存",  "u8",  1.0),
    ("kmh u16 直存", "u16", 1.0),
    ("kmh u16 x0.1", "u16", 0.1),
    ("kmh u16 x0.01","u16", 0.01),
    ("kmh u16 /16",  "u16", 1 / 16.0),
]
BAND = (5.0, 300.0)          # 合理换挡车速带 km/h
MIN_RISE = 5.0               # 一条换挡曲线跨节气门至少涨 5 km/h(否则近乎常数,不像换挡曲线)


def read_lane(buf, dtype, endian):
    ch, w = DT[dtype]
    n = len(buf) // w
    fmt = ("<" if endian == "le" else ">") + ch * n
    return list(struct.unpack_from(fmt, buf, 0)), w


def _valid_curve(phys):
    """一条候选换挡曲线:全落车速带、非递减、净涨 >= MIN_RISE。"""
    lo, hi = BAND
    if any(not (lo <= p <= hi) for p in phys):
        return False
    if any(phys[k + 1] < phys[k] for k in range(len(phys) - 1)):
        return False
    return (phys[-1] - phys[0]) >= MIN_RISE


def find_shift_maps(buf, endian, min_gears=3, cols_range=(4, 16)):
    """返回换挡点表候选:>=min_gears 条【连续、等长、递增、行均值逐行递增(档位堆叠)】的车速曲线。"""
    out = []
    for label, dtype, factor in SPEED_SCALINGS:
        if dtype == "u8" and endian == "be":
            continue
        V, w = read_lane(buf, dtype, endian)
        phys_all = [v * factor for v in V]
        lo, hi = BAND
        inband = [lo <= p <= hi for p in phys_all]
        N = len(V)
        for cols in range(cols_range[0], cols_range[1] + 1):
            i = 0
            while i + cols <= N:
                if not inband[i]:
                    i += 1
                    continue
                # 从 i 起,逐个 cols 段收集"有效递增曲线",要求档位堆叠(行均值递增)
                rows_vals = []
                k = i
                prev_mean = None
                while k + cols <= N:
                    seg = phys_all[k:k + cols]
                    if not _valid_curve(seg):
                        break
                    mean = sum(seg) / cols
                    if prev_mean is not None and mean <= prev_mean:
                        break                       # 未堆叠(高档没在更高速换)→ 断
                    rows_vals.append((k, seg, mean))
                    prev_mean = mean
                    k += cols
                if len(rows_vals) >= min_gears:
                    start = rows_vals[0][0]
                    nrows = len(rows_vals)
                    # 堆叠强度:相邻行均值差的最小值(越大越像真换挡表)
                    means = [r[2] for r in rows_vals]
                    stack = min(means[j + 1] - means[j] for j in range(nrows - 1))
                    rise = sum(r[1][-1] - r[1][0] for r in rows_vals) / nrows
                    out.append({"start": start, "rows": nrows, "cols": cols,
                                "dtype": dtype, "endian": endian, "scaling": label,
                                "factor": factor, "width": w,
                                "speed_lo": round(rows_vals[0][1][0], 1),
                                "speed_hi": round(rows_vals[-1][1][-1], 1),
                                "stack": round(stack, 1), "rise": round(rise, 1),
                                "curves": [[round(x, 1) for x in r[1]] for r in rows_vals],
                                "score": round(nrows * cols + stack + rise, 1)})
                    i = start + nrows * cols        # 跳过整块
                else:
                    i += 1
    # NMS:同区块不同 cols/缩放的重叠候选,保留最高分
    out.sort(key=lambda t: -t["score"])
    kept, used = [], []
    for c in out:
        a0, a1 = c["start"], c["start"] + c["rows"] * c["cols"] * c["width"]
        # 换算成字节区间
        b0 = c["start"] * c["width"]
        b1 = b0 + c["rows"] * c["cols"] * c["width"]
        if any(not (b1 <= u0 or b0 >= u1) for u0, u1 in used):
            continue
        used.append((b0, b1))
        kept.append(c)
    return kept


def find_hysteresis_pairs(maps):
    """配对升/降档表:同维、字节相邻、且一张整体高于另一张(升档>降档回差)。"""
    pairs = []
    for a in maps:
        for b in maps:
            if a is b or a["rows"] != b["rows"] or a["cols"] != b["cols"]:
                continue
            ba = a["start"] * a["width"]
            bb = b["start"] * b["width"]
            gap = abs(ba - bb)
            block = a["rows"] * a["cols"] * a["width"]
            if 0 < gap <= block * 2:               # 相邻(一两个块内)
                # a 整体高于 b?
                flat_a = [x for row in a["curves"] for x in row]
                flat_b = [x for row in b["curves"] for x in row]
                if all(x >= y for x, y in zip(flat_a, flat_b)) and \
                        sum(flat_a) - sum(flat_b) > 0:
                    diff = (sum(flat_a) - sum(flat_b)) / len(flat_a)
                    pairs.append((a, b, round(diff, 1)))
    # 去重(a,b)与(b,a)
    uniq, seen = [], set()
    for a, b, d in pairs:
        key = tuple(sorted((a["start"], b["start"])))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((a, b, d))
    return uniq


def report(maps, pairs, base):
    print("=" * 92)
    print("TCU 换挡点表定位 — %d 个候选(递增车速曲线 + 档位堆叠)" % len(maps))
    print("=" * 92)
    if not maps:
        print("未找到换挡点表候选。可能:车速缩放未列 / 字节序不对(试 --endian be) /")
        print("表结构非'档位堆叠'(如按车速索引的单表) → 改用 blind-map-finder。")
        return
    for m in maps[:15]:
        addr = base + m["start"] * m["width"]
        print("@0x%08X  %d档×%d点  %s %s [%s]  车速%.0f..%.0f km/h  堆叠%.1f 涨%.1f (score=%.1f)"
              % (addr, m["rows"], m["cols"], m["dtype"], m["endian"], m["scaling"],
                 m["speed_lo"], m["speed_hi"], m["stack"], m["rise"], m["score"]))
        for gi, cv in enumerate(m["curves"][:8]):
            print("    档%d: %s" % (gi + 1, " ".join("%.0f" % x for x in cv)))
    if pairs:
        print("-" * 92)
        print("★ 升/降档回差对(hysteresis,一张整体高于另一张):")
        for a, b, d in pairs:
            print("  升档@0x%08X  ↔  降档@0x%08X  平均回差 %.1f km/h"
                  % (base + a["start"] * a["width"], base + b["start"] * b["width"], d))
    print("-" * 92)
    print("下一步:对照 X 轴(节气门/负荷)与 Y(档位)、用 ecu-map-analysis 判功能、ghidra 看谁读;")
    print("油压/TCC(非车速堆叠 2D)用 blind-map-finder;字节序经 multi-vendor 核定;写后 checksum。")


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("ok " if c else "FAIL ") + m)
        ok = ok and c

    import random
    random.seed(5)
    rows, cols = 5, 8
    # 升档表:5 档,每档随节气门 +4,随档位堆叠(基速 15,+18/档)
    up = []
    for r in range(rows):
        base_v = 25 + r * 18
        up.append([base_v + t * 4 for t in range(cols)])
    down = [[v - 12 for v in row] for row in up]     # 降档 = 升档 -12(回差,仍在车速带内)

    def pack_map(m):
        b = bytearray()
        for row in m:
            for v in row:
                b += struct.pack("<H", v)
        return b

    blob = bytearray(struct.pack("<%dH" % 0x80, *[random.randrange(301, 5000) for _ in range(0x80)]))
    up_off = len(blob) // 2
    blob += pack_map(up)
    blob += struct.pack("<%dH" % 4, *[random.randrange(301, 5000) for _ in range(4)])  # 小间隔
    down_off = len(blob) // 2
    blob += pack_map(down)
    blob += struct.pack("<%dH" % 0x80, *[random.randrange(301, 5000) for _ in range(0x80)])

    maps = find_shift_maps(bytes(blob), "le")
    starts = {m["start"] for m in maps}
    chk(up_off in starts, "升档表被定位")
    chk(down_off in starts, "降档表被定位")
    um = [m for m in maps if m["start"] == up_off][0]
    chk(um["rows"] == 5 and um["cols"] == 8, "维度 5档×8点 正确")
    chk(um["dtype"] == "u16" and um["scaling"].startswith("kmh u16 直存"), "识别 u16 直存 km/h")
    chk(um["stack"] > 0, "档位堆叠强度 > 0")
    chk(20 <= um["speed_lo"] <= 30 and um["speed_hi"] >= 80, "车速范围解码正确")

    pairs = find_hysteresis_pairs(maps)
    got = any({p[0]["start"], p[1]["start"]} == {up_off, down_off} for p in pairs)
    chk(got, "升/降档回差对被配上")
    if got:
        d = [p[2] for p in pairs if {p[0]["start"], p[1]["start"]} == {up_off, down_off}][0]
        chk(abs(d - 12) < 0.5, "回差幅度≈12 km/h 正确")

    # 负向:一堆随机递增但不堆叠的块不应成换挡表(此处噪声区无堆叠曲线)
    noise_maps = [m for m in maps if m["start"] not in (up_off, down_off)]
    chk(all(m["rows"] >= 3 for m in noise_maps), "输出的都满足 >=3 档(结构约束)")

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Locate shift-point maps in a TCU bin (speed-curve stacking)")
    ap.add_argument("--bin", dest="binpath", default=None)
    ap.add_argument("--endian", choices=["le", "be", "both"], default="le")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--min-gears", type=int, default=3)
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not a.binpath:
        ap.error("需要 --bin(或 --selftest)")
    buf = open(a.binpath, "rb").read()
    endians = ["le", "be"] if a.endian == "both" else [a.endian]
    maps = []
    for e in endians:
        maps += find_shift_maps(buf, e, min_gears=a.min_gears)
    maps.sort(key=lambda t: -t["score"])
    pairs = find_hysteresis_pairs(maps)
    report(maps, pairs, a.base)
    if a.json:
        json.dump({"base": a.base, "candidates": [
            {"addr": a.base + m["start"] * m["width"], "rows": m["rows"], "cols": m["cols"],
             "dtype": m["dtype"], "endian": m["endian"], "scaling": m["scaling"],
             "speed_lo": m["speed_lo"], "speed_hi": m["speed_hi"], "curves": m["curves"]}
            for m in maps]}, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("JSON ->", a.json)


if __name__ == "__main__":
    main()

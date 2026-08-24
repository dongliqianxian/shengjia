#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
blind_map_scan.py — 无标定(无 A2L/DAMOS)时,在 ECU bin 上【数据驱动】盲扫 map 候选。

blind-map-finder 技能的执行引擎。这是 WinOLS auto-detect 那类"统计找图"的蒸馏版:
不反汇编,直接在字节上找 map 的形态指纹,输出【排序候选】——不是事实。

诚实边界(doctrine):
  * 没有任何工具能全自动给出"所有 map 地址 + 正确 scaling + 功能名"当事实用。本脚本给的是
    候选 + 可信度 + 理由,把搜索空间从整片 flash 收敛到几十处。
  * 每个候选必须再确认:①按 dtype/scaling 解码后落物理带自检 ②ghidra 看读该地址的例程(xref)
    ③交 ecu-map-analysis 判功能。三者印证才算数。
  * 本脚本不解码语义、不猜 factor/offset、不重算 checksum——那些交下游技能。

找什么(map 的形态指纹):
  1. 轴数组 axis   : 严格单调序列(RPM/负荷/水温断点必单调)——最强信号。
  2. Z 数据块 zmap : 等宽、局部平滑(map 是光滑曲面)、非填充、非高熵代码的区域。
  3. map(配对)   : [X轴][Y轴][大小=lenX*lenY 的 Z块] 连续 = 经典布局。
  4. 指针表 ptrtab : 一串对齐 u32 落在镜像地址窗内(Bosch/Conti 描述符表常见)。
  熵粗分:高熵=代码/压缩(降权),恒定=填充(排除),中间=数据/map。

用法:
    python blind_map_scan.py --bin ECU.bin                     # 全扫,输出候选清单
    python blind_map_scan.py --bin ECU.bin --endian le         # 只小端(默认双序)
    python blind_map_scan.py --bin ECU.bin --kinds axis,map    # 只要某几类
    python blind_map_scan.py --bin ECU.bin --min-axis 6 --top 60
    python blind_map_scan.py --bin ECU.bin --base 0x80000000   # 给镜像基址(指针表更准)
    python blind_map_scan.py --bin ECU.bin --json out.json
    python blind_map_scan.py --selftest

字节序跟 MCU 核走不跟车标(multi-vendor 原则#1):TriCore/AURIX=小端,SH/PowerPC=大端。
不确定就用默认(双序),按候选落带自检哪个序对。
"""
import sys, struct, argparse, json, math

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# dtype -> (struct 字符, 字节宽, 有符号)
DT = {"u8": ("B", 1, False), "u16": ("H", 2, False), "s16": ("h", 2, True),
      "u32": ("I", 4, False)}


def _fmt(dtype, endian):
    ch = DT[dtype][0]
    return ("<" if endian == "le" else ">") + ch


def _read_lane(buf, dtype, endian, phase):
    """读一条"对齐车道":从 phase 起,每 width 取一个值。返回 (offsets, values)。
    按 width 分 phase 扫描,天然避免 u16/u32 逐字节滑动的相位别名。"""
    ch, w, _ = DT[dtype]
    fmt = _fmt(dtype, endian)
    n = len(buf)
    offs, vals = [], []
    off = phase
    while off + w <= n:
        offs.append(off)
        vals.append(struct.unpack_from(fmt, buf, off)[0])
        off += w
    return offs, vals


# ---------------------------------------------------------------------------
# 1) 轴数组:严格单调 run
# ---------------------------------------------------------------------------

def _is_fill(v, dtype):
    """全 0xFF 填充哨兵(未编程 flash),不是轴断点,应从 run 中排除。"""
    ch, w, signed = DT[dtype]
    return v == -1 if signed else v == (1 << (8 * w)) - 1


def find_axes(buf, endians, dtypes, min_len=6):
    """找严格单调(增或减)的对齐 run = 候选轴。步长规整度加分(轴断点多为人选圆整值)。
    排除全 0xFF 填充哨兵——否则 map 前的填充会让轴带相位提前误检。"""
    out = []
    for dtype in dtypes:
        w = DT[dtype][1]
        for endian in endians:
            if w == 1 and endian == "be":
                continue                      # u8 无字节序,避免重复
            for phase in range(w):
                offs, vals = _read_lane(buf, dtype, endian, phase)
                i, m = 0, len(vals)
                while i < m - 1:
                    # 尝试从 i 起的最长严格单调 run(方向由前两个值定)
                    if vals[i + 1] == vals[i] or _is_fill(vals[i], dtype):
                        i += 1
                        continue
                    up = vals[i + 1] > vals[i]
                    j = i + 1
                    while j < m and not _is_fill(vals[j], dtype) \
                            and ((vals[j] > vals[j - 1]) if up else (vals[j] < vals[j - 1])):
                        j += 1
                    run = j - i
                    if run >= min_len:
                        seg = vals[i:j]
                        steps = [abs(seg[k + 1] - seg[k]) for k in range(len(seg) - 1)]
                        rng = seg[-1] - seg[0] if up else seg[0] - seg[-1]
                        # 步长规整度:步长变异系数越小越像轴(等距/缓变断点)
                        mean_s = sum(steps) / len(steps)
                        var_s = sum((s - mean_s) ** 2 for s in steps) / len(steps)
                        cov = (math.sqrt(var_s) / mean_s) if mean_s else 9
                        step_reg = max(0.0, 1.0 - min(cov, 1.0))
                        # 排除退化(全 0/1 步、range 过小)
                        # 真轴步长规整(等距/缓变断点)。长且步长杂乱(step_reg 低)= 平滑数据
                        # 斜坡被误读成轴,gate 掉;打分让规整度主导、长度封顶,避免长噪声 run 霸榜。
                        if rng >= max(3, min_len) and not (run > 48 and step_reg < 0.15):
                            eff = min(run, 64)
                            score = eff * (1.0 + 3.0 * step_reg)
                            out.append({"kind": "axis", "address": offs[i],
                                        "dtype": dtype, "endian": endian,
                                        "length": run, "dir": "up" if up else "down",
                                        "vmin": min(seg), "vmax": max(seg),
                                        "step_reg": round(step_reg, 2),
                                        "end": offs[i] + run * w, "width": w,
                                        "score": round(score, 1)})
                        i = j - 1               # 允许相邻 run 共享端点
                    else:
                        i += 1
    # 去重:u16 与 s16 在全为正(<0x8000)时读出完全相同的轴 → 只留无符号 u16。
    dedup = {}
    for ax in out:
        k = (ax["address"], ax["length"], ax["dir"], ax["vmin"], ax["vmax"])
        prev = dedup.get(k)
        if prev is None:
            dedup[k] = ax
        elif {ax["dtype"], prev["dtype"]} == {"u16", "s16"}:
            dedup[k] = ax if ax["dtype"] == "u16" else prev      # 保留无符号
        elif ax["score"] > prev["score"]:
            dedup[k] = ax
    return list(dedup.values())


# ---------------------------------------------------------------------------
# 2) Z 数据块:平滑度
# ---------------------------------------------------------------------------

def _smoothness(vals):
    """map 是光滑曲面:多数相邻差 <= 10% 值域。返回 (平滑比例, range)。"""
    if len(vals) < 4:
        return 0.0, 0
    rng = max(vals) - min(vals)
    if rng <= 0:
        return 0.0, 0                          # 恒定 = 填充/无信息
    thr = max(1.0, rng * 0.10)
    small = sum(1 for a, b in zip(vals, vals[1:]) if abs(b - a) <= thr)
    return small / (len(vals) - 1), rng


def block_is_maplike(buf, start, count, dtype, endian, min_smooth=0.70):
    ch, w, _ = DT[dtype]
    if start + count * w > len(buf):
        return None
    fmt = _fmt(dtype, endian)
    vals = [struct.unpack_from(fmt, buf, start + k * w)[0] for k in range(count)]
    smooth, rng = _smoothness(vals)
    if smooth < min_smooth or rng < 4:
        return None
    return {"smooth": round(smooth, 2), "vmin": min(vals), "vmax": max(vals), "range": rng}


# ---------------------------------------------------------------------------
# 3) map 配对:[X轴][Y轴][Z块]
# ---------------------------------------------------------------------------

def _is_monotonic(vals):
    if len(vals) < 2:
        return False
    up = all(vals[k + 1] > vals[k] for k in range(len(vals) - 1))
    dn = all(vals[k + 1] < vals[k] for k in range(len(vals) - 1))
    return up or dn


def pair_maps(buf, axes, endians, max_ny=64):
    """经典布局:[X轴][Y轴][大小=nx*ny 的 Z块] 连续;也支持 1D [X轴][nx 的 Z曲线]。

    关键:不信任贪婪 Y 轴的长度(平滑 Z 当 u16 读常是 X/Y 轴单调 run 的延续,会把 Y 撑长)。
    改为锚定 X 轴(其后继若数值更低则 X 干净收尾),**显式搜索 ny 与 Z 参数**——对每个 ny 单独
    验证"Y 段严格单调 且 其后 nx*ny 的块平滑",找到就是一张 map。"""
    maps = []
    seen = set()
    for a in sorted(axes, key=lambda a: a["address"]):
        nx = a["length"]
        if not (2 <= nx <= 64):
            continue
        xend = a["end"]
        # 2D
        for ytype in ("u16", "s16", "u8"):
            yw = DT[ytype][1]
            for ye in endians:
                if yw == 1 and ye == "be":
                    continue
                yfmt = _fmt(ytype, ye)
                for ny in range(3, max_ny + 1):
                    yend = xend + ny * yw
                    if yend + nx * ny > len(buf):
                        break
                    yv = [struct.unpack_from(yfmt, buf, xend + k * yw) for k in range(ny)]
                    yv = [v[0] if isinstance(v, tuple) else v for v in yv]
                    if not _is_monotonic(yv) or (max(yv) - min(yv)) < 3:
                        continue
                    for ztype in ("u8", "u16", "s16"):
                        zw = DT[ztype][1]
                        for ze in endians:
                            if zw == 1 and ze == "be":
                                continue
                            info = block_is_maplike(buf, yend, nx * ny, ztype, ze)
                            if not info:
                                continue
                            key = (a["address"], yend, ztype, ze, nx, ny)
                            if key in seen:
                                continue
                            seen.add(key)
                            maps.append({"kind": "map", "address": a["address"],
                                         "x_axis": a["address"], "y_axis": xend,
                                         "z_addr": yend, "dims": [nx, ny],
                                         "z_dtype": ztype, "z_endian": ze,
                                         "z_range": info["range"], "smooth": info["smooth"],
                                         "score": round(nx + ny + info["smooth"] * (nx + ny)
                                                        + a["step_reg"] * 4, 1)})
        # 1D 曲线:轴后紧跟 nx 的平滑 Z
        for ztype in ("u8", "u16", "s16"):
            zw = DT[ztype][1]
            for ze in endians:
                if zw == 1 and ze == "be":
                    continue
                info = block_is_maplike(buf, xend, nx, ztype, ze, min_smooth=0.75)
                if info and nx >= 6:
                    maps.append({"kind": "curve", "address": a["address"],
                                 "x_axis": a["address"], "z_addr": xend,
                                 "dims": [nx], "z_dtype": ztype, "z_endian": ze,
                                 "z_range": info["range"], "smooth": info["smooth"],
                                 "score": round(nx + info["smooth"] * nx
                                                + a["step_reg"] * 3, 1)})
    # 去重:同 Z 起点保留最高分
    best = {}
    for mp in maps:
        k = (mp["z_addr"], tuple(mp["dims"]))
        if k not in best or mp["score"] > best[k]["score"]:
            best[k] = mp
    return list(best.values())


# ---------------------------------------------------------------------------
# 4) 指针表:对齐 u32 落镜像窗
# ---------------------------------------------------------------------------

def find_pointer_tables(buf, base, endians, min_len=6):
    n = len(buf)
    out = []
    for endian in endians:
        fmt = _fmt("u32", endian)
        for phase in (0,):                     # 指针表通常 4 字节对齐
            offs, vals = [], []
            off = phase
            while off + 4 <= n:
                offs.append(off)
                vals.append(struct.unpack_from(fmt, buf, off)[0])
                off += 4
            # 推断合理地址窗:有 base 用 [base,base+n];否则用文件内偏移 [0,n]
            lo = base if base is not None else 0
            hi = (base + n) if base is not None else n
            # 关键:真描述符/指针表的目标几乎全【偶对齐】(指向 u16/u32 数据)。代码区 u32 的
            # 目标约 50% 为奇 → 要求 run 内目标 v%2==0 会在第一个奇目标处断开,长代码假 run 消失;
            # 真对齐表仍完整成 run。这是消除"5021 项代码假指针表"的核心过滤。
            def ok_ptr(v):
                return lo <= v < hi and v != 0 and v % 2 == 0
            i, m = 0, len(vals)
            while i < m:
                if not ok_ptr(vals[i]):
                    i += 1
                    continue
                j = i
                while j < m and ok_ptr(vals[j]):
                    j += 1
                run = j - i
                if run >= min_len:
                    seg = vals[i:j]
                    distinct = len(set(seg)) / run
                    aligned4 = sum(1 for v in seg if v % 4 == 0) / run
                    if distinct >= 0.5:          # 排除同一偶值重复填充
                        out.append({"kind": "ptrtab", "address": offs[i], "endian": endian,
                                    "count": run, "width": 4,
                                    "target_lo": min(seg), "target_hi": max(seg),
                                    "aligned4": round(aligned4, 2),
                                    "score": round(run * 1.5, 1)})
                i = max(j, i + 1)
    return out


# ---------------------------------------------------------------------------
# 熵(代码/数据/填充粗分,用于注释与降权)
# ---------------------------------------------------------------------------

def region_entropy(buf, start, size=256):
    seg = buf[start:start + size]
    if not seg:
        return 0.0
    freq = [0] * 256
    for b in seg:
        freq[b] += 1
    ln = len(seg)
    h = 0.0
    for f in freq:
        if f:
            p = f / ln
            h -= p * math.log2(p)
    return h                                    # 0..8;>7≈代码/压缩,<1≈填充


def entropy_tag(buf, addr):
    h = region_entropy(buf, (addr // 256) * 256)
    if h > 7.0:
        return "高熵(疑代码/压缩)"
    if h < 1.0:
        return "低熵(疑填充)"
    return "中熵(疑数据/表)"


# ---------------------------------------------------------------------------
# 顶层
# ---------------------------------------------------------------------------

def _zspan(mp):
    zw = DT[mp["z_dtype"]][1]
    n = 1
    for d in mp["dims"]:
        n *= d
    return mp["z_addr"], mp["z_addr"] + n * zw


def merge_overlapping(maps):
    """同一张真 map 常因 ny 猜测/±1 相位冒出多个近重复候选。按分数贪婪保留:
    若某候选的 Z 区间与已保留候选显著重叠(>50%),视为同一张 map 的次优猜测,丢弃。"""
    kept = []
    for mp in sorted(maps, key=lambda m: -m["score"]):
        s, e = _zspan(mp)
        dup = False
        for k in kept:
            ks, ke = _zspan(k)
            ov = max(0, min(e, ke) - max(s, ks))
            if ov > 0.5 * min(e - s, ke - ks):
                dup = True
                break
        if not dup:
            kept.append(mp)
    return kept


def scan(buf, endians, kinds, min_axis=6, base=None):
    axes = find_axes(buf, endians, ["u8", "u16", "s16"], min_len=min_axis) \
        if ("axis" in kinds or "map" in kinds or "curve" in kinds) else []
    maps = pair_maps(buf, axes, endians) if ("map" in kinds or "curve" in kinds) else []
    maps = merge_overlapping(maps)
    ptrs = find_pointer_tables(buf, base, endians) if "ptrtab" in kinds else []

    # map/curve 用掉的轴不再单列(降噪)
    used = set()
    for mp in maps:
        used.add(mp.get("x_axis"))
        if "y_axis" in mp:
            used.add(mp["y_axis"])
    axes_solo = [a for a in axes if a["address"] not in used] if "axis" in kinds else []

    results = maps + axes_solo + ptrs
    for r in results:
        r["entropy"] = entropy_tag(buf, r["address"])
    # 降权:落在高熵区的候选可疑
    for r in results:
        if r["entropy"].startswith("高熵"):
            r["score"] = round(r["score"] * 0.5, 1)
    results.sort(key=lambda r: -r["score"])
    return results


def report(results, top, base):
    print("=" * 90)
    print("无标定盲扫 map 候选(数据驱动统计) — 共 %d 个候选,显示前 %d" %
          (len(results), min(top, len(results))))
    print("★ 这是【排序候选】不是事实。每个都要:解码落带自检 + ghidra 看 xref + ecu-map-analysis 判功能。")
    print("=" * 90)
    for r in results[:top]:
        a = ("0x%08X" % (r["address"] + (base or 0)))
        if r["kind"] == "map":
            print("[MAP  ] %s  %dx%d  Z=%s/%s @0x%X  平滑%.2f 值域%d  %s (score=%.1f)"
                  % (a, r["dims"][0], r["dims"][1], r["z_dtype"], r["z_endian"],
                     r["z_addr"] + (base or 0), r["smooth"], r["z_range"],
                     r["entropy"], r["score"]))
            print("         X轴@0x%X  Y轴@0x%X" % (r["x_axis"] + (base or 0),
                                                  r["y_axis"] + (base or 0)))
        elif r["kind"] == "curve":
            print("[CURVE] %s  %d点  Z=%s/%s @0x%X  平滑%.2f  %s (score=%.1f)"
                  % (a, r["dims"][0], r["z_dtype"], r["z_endian"], r["z_addr"] + (base or 0),
                     r["smooth"], r["entropy"], r["score"]))
        elif r["kind"] == "axis":
            print("[AXIS ] %s  %d点 %s/%s %s  值域%d..%d 规整%.2f  %s (score=%.1f)"
                  % (a, r["length"], r["dtype"], r["endian"], r["dir"],
                     r["vmin"], r["vmax"], r["step_reg"], r["entropy"], r["score"]))
        elif r["kind"] == "ptrtab":
            print("[PTRTB] %s  %d项 u32/%s  指向0x%X..0x%X  %s (score=%.1f)"
                  % (a, r["count"], r["endian"], r["target_lo"], r["target_hi"],
                     r["entropy"], r["score"]))
    print("-" * 90)
    print("下一步:1) 轴要单调且落物理带(RPM/负荷/温度) 2) 用 bosch-conti/denso-bin-engine 按")
    print("dtype 解码 Z 块看落不落带 3) ghidra-tricore-disasm 找读该地址的插值例程坐实 4)")
    print("ecu-map-analysis 判功能。地址栏已加 --base(未给则为文件偏移)。")


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
    random.seed(11)
    parts = bytearray()
    parts += bytes([0xFF]) * 0x200                          # 填充区
    parts += bytes(random.randrange(256) for _ in range(0x400))   # 高熵"代码"区
    parts += bytes([0xFF]) * 0x10                           # 隔离填充,断开与代码区的伪 run
    map_off = len(parts)
    # X 轴:16 点 u16-le 递增(圆整断点)
    xax = [500 + 300 * i for i in range(16)]               # 500..5000
    for v in xax:
        parts += struct.pack("<H", v)
    xa_off = map_off
    ya_off = len(parts)
    # Y 轴:12 点 u16-le 递增
    for v in [200 * i for i in range(12)]:                 # 0..2200
        parts += struct.pack("<H", v)
    z_off = len(parts)
    # Z 块:16x12 u8 光滑【非全局单调】曲面(中间隆起的丘,更接近真实 map)
    for i in range(16):
        for j in range(12):
            parts += bytes([(60 + min(i, 15 - i) * 2 + min(j, 11 - j) * 2) & 0xFF])
    parts += bytes(random.randrange(256) for _ in range(0x180))   # 尾部噪声
    blob = bytes(parts)

    res = scan(blob, ["le", "be"], {"axis", "map", "curve", "ptrtab"}, min_axis=6)

    # X 轴应干净收尾于 16 点(其后 Y 从 0 开始,数值回落使 X run 停在 16)
    axes_all = find_axes(blob, ["le", "be"], ["u8", "u16", "s16"], 6)
    chk(any(a["address"] == xa_off and a["dtype"] == "u16" and a["dir"] == "up"
            and a["length"] == 16 for a in axes_all), "X 轴 @map 起点 干净识别(16点 u16 递增)")

    # 配对出一张 16x12 的 map,Z 指向 z_off(即便贪婪 Y 会吞入 Z,显式 ny 搜索仍锁定 12)
    m = [r for r in res if r["kind"] == "map" and r["x_axis"] == xa_off
         and r["dims"] == [16, 12]]
    chk(len(m) >= 1, "配对出 16x12 的 map 候选(显式 ny 搜索抗贪婪 Y)")
    if m:
        mm = m[0]
        chk(mm["z_addr"] == z_off, "Z 地址正确")
        chk(mm["y_axis"] == ya_off, "Y 轴地址正确")
        chk(mm["z_dtype"] == "u8", "Z 块识别为 u8")
        chk(mm["smooth"] >= 0.7, "Z 块平滑度达标")

    # 纯高熵"代码"区(0x210..0x600,不含 map 前填充边缘)不应产出 map(平滑度不达标)
    code_maps = [r for r in res if r["kind"] in ("map", "curve")
                 and 0x210 <= r["address"] < 0x600]
    chk(len(code_maps) == 0, "高熵代码区不产出 map/curve")

    # 排序有效:真 LE 16x12 map 的分数应 >= 任何跨端序 BE 幽灵(靠解码自检可排除幽灵)
    if m:
        be_ghosts = [r for r in res if r["kind"] == "map" and r.get("z_endian") == "be"]
        chk(all(m[0]["score"] >= g["score"] for g in be_ghosts) or not be_ghosts,
            "真 LE map 排名 >= 跨端序 BE 幽灵")

    # 熵判别
    chk(region_entropy(blob, 0) < 1.0, "填充区低熵")
    chk(region_entropy(blob, 0x200) > 7.0, "代码区高熵")

    # 指针表:造一段对齐 u32,目标落在镜像窗 [base, base+len) 内
    base = 0x80000000
    ptr_blob = bytearray(random.randrange(256) for _ in range(0x80))
    pt_off = len(ptr_blob)
    for k in range(10):
        ptr_blob += struct.pack("<I", base + 0x20 + k * 0x08)   # 目标在 buf 内
    ptr_blob += bytes(random.randrange(256) for _ in range(0x40))
    pts = find_pointer_tables(bytes(ptr_blob), base, ["le"], min_len=6)
    chk(any(p["address"] == pt_off and p["count"] >= 10 for p in pts),
        "指针表(10项对齐u32落镜像窗)被识别")

    # 回归1(核心 bug):代码型 u32——目标落窗内但奇偶随机——不应成大假指针表。
    # 偶对齐 gate 在第一个奇目标处断开 run,长代码假表(曾达 5021 项)消失。
    base2 = 0x80000000
    code_blob = bytearray()
    for _ in range(300):
        code_blob += struct.pack("<I", base2 + random.randrange(0, 300 * 4))  # 窗内,奇偶随机
    pts2 = find_pointer_tables(bytes(code_blob), base2, ["le"], min_len=6)
    chk(not any(p["count"] >= 20 for p in pts2),
        "回归:半数奇目标的代码型 u32 不成大假指针表(偶对齐 gate)")

    # 回归2:长且步长杂乱(step_reg<0.15, run>48)的平滑数据斜坡不应被当轴(gate 掉)。
    ramp = bytearray(b"\xff\xff" * 4)                        # 前置填充隔离
    ramp_off = len(ramp)
    steps = [1] * 50 + [500] * 6                             # 重尾步长 → CoV 大 → step_reg≈0
    cur = 1000
    ramp += struct.pack("<H", cur)
    for s in steps:
        cur += s
        ramp += struct.pack("<H", cur)                       # 严格递增,峰值 <0xFFFF
    ramp += b"\xff\xff" * 4
    ramp_axes = find_axes(bytes(ramp), ["le"], ["u16"], min_len=6)
    chk(not any(a["address"] == ramp_off and a["length"] > 48 for a in ramp_axes),
        "回归:长且步长杂乱的数据斜坡被 gate,不当轴")

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Blind (no-A2L) statistical map-candidate scanner for ECU bins")
    ap.add_argument("--bin", dest="binpath", default=None)
    ap.add_argument("--endian", choices=["le", "be", "both"], default="both")
    ap.add_argument("--kinds", default="axis,map,curve,ptrtab",
                    help="逗号分隔: axis,map,curve,ptrtab")
    ap.add_argument("--min-axis", type=int, default=6, help="轴最短点数(默认6)")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=None, help="镜像基址,如 0x80000000")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not a.binpath:
        ap.error("需要 --bin(或 --selftest)")
    buf = open(a.binpath, "rb").read()
    endians = ["le", "be"] if a.endian == "both" else [a.endian]
    kinds = set(k.strip() for k in a.kinds.split(","))
    results = scan(buf, endians, kinds, min_axis=a.min_axis, base=a.base)
    report(results, a.top, a.base)
    if a.json:
        json.dump({"file": a.binpath, "base": a.base, "count": len(results),
                   "candidates": results}, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("JSON ->", a.json)


if __name__ == "__main__":
    main()

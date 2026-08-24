#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diff_cluster.py — 【学】阶段主脚本 (reference-tune-porting skill)

把一对 ORI/MOD 变成一份【结构化的改动配方】:
    整文件字节 diff  ->  按间隙聚簇  ->  归属到命名 MAP  ->  逐表解码 ->  产出 recipe JSON

与 bosch-conti-bin-engine 的 diff_maps 的分工:
    diff_maps      : 你【已经知道】是哪张表, 对比这一张
    diff_cluster.py: 你【还不知道】改了哪些表, 从整文件反推出改动清单 + 未归属残留

纯标准库, 无第三方依赖.

用法
----
  # 只看聚簇 (没有定义文件时也能用, 先拿到改动地图)
  python diff_cluster.py --ori A_ORI.bin --mod A_MOD.bin

  # 带定义文件, 归属到具体 MAP 并产出配方
  python diff_cluster.py --ori A_ORI.bin --mod A_MOD.bin \
         --defs ../assets/defs_mevd1729_b20_8709.csv --json recipe.json

  python diff_cluster.py --selftest

定义文件 (CSV) 列
----------------
  name,addr,rows,cols,dtype,copies,unit,factor,offset,stride
  addr  : 0x 十六进制或十进制, 指向【数据块首字节】
  dtype : u8 s8 u16 s16 u32 s32   (默认小端, 用 --endian big 切换)
  copies: 副本份数, 默认 1
  stride: 副本间距(字节). 留空则自动推导 —— 优先用数据块后的自描述符,
          推不出来就按 packed (rows*cols*size) 处理
  unit/factor/offset: 仅用于显示, phys = raw*factor + offset
"""

import argparse
import csv
import json
import os
import struct
import sys

# ----------------------------------------------------------------- 基础

DTYPES = {
    "u8":  (1, "B"), "s8":  (1, "b"),
    "u16": (2, "H"), "s16": (2, "h"),
    "u32": (4, "I"), "s32": (4, "i"),
}


def load(path):
    with open(path, "rb") as fh:
        return fh.read()


def parse_addr(s):
    s = str(s).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s, 0)


def decode(buf, addr, count, dtype, endian="little"):
    """从 buf 解出 count 个 dtype 值 (list[int])."""
    size, code = DTYPES[dtype]
    pre = "<" if endian == "little" else ">"
    end = addr + count * size
    if end > len(buf):
        raise ValueError("read past end of file at 0x%X" % addr)
    return list(struct.unpack_from(pre + str(count) + code, buf, addr))


# ----------------------------------------------------------------- diff + 聚簇

def diff_offsets(a, b, block=4096):
    """返回所有不同字节的偏移. 先按块比较, 只下钻到不同的块 —— 4MB 文件亚秒级."""
    n = min(len(a), len(b))
    out = []
    for base in range(0, n, block):
        stop = min(base + block, n)
        if a[base:stop] != b[base:stop]:
            for i in range(base, stop):
                if a[i] != b[i]:
                    out.append(i)
    return out


def cluster(offsets, gap=32):
    """把偏移列表按间隙合并成 [(start, end, changed_count), ...]."""
    if not offsets:
        return []
    out = []
    start = prev = offsets[0]
    cnt = 1
    for x in offsets[1:]:
        if x - prev > gap:
            out.append((start, prev, cnt))
            start, cnt = x, 0
        prev = x
        cnt += 1
    out.append((start, prev, cnt))
    return out


# ----------------------------------------------------------------- 自描述符探针

#: 轴元素大小的尝试顺序 (行轴, 列轴). u16 双轴最常见, 但实测存在 u8 轴 ——
#: 例如 Bosch MEVD17.2.9 的档位相关限扭表, 列轴就是 u8 的档位序号 0..9,
#: 按 u16 去算 stride 会差 10 字节, 第二份副本就整个错位.
AXIS_SIZES = ((2, 2), (2, 1), (1, 2), (1, 1))


def _mono(v):
    return all(v[i] <= v[i + 1] for i in range(len(v) - 1))


def probe_descriptors(buf, data_end, rows, cols, win=160, endian="little"):
    """
    在数据块之后探测自描述符, 返回【全部】通过校验的候选:
        [(pos, rowaxis, colaxis, row_axis_size, col_axis_size), ...]

    已验证的容器形态 (Bosch MEVD17.2.x 等自描述平台):
        2D:  [rows:u8][cols:u8][rowaxis: rows*(u16|u8)][colaxis: cols*(u16|u8)]
        1D:  [n:u8][0:u8][axis: n*(u16|u8)]

    单条候选的命中判据: 维度字节吻合 + 两轴均单调不减 + 轴不是全相同值.
    这三条【不足以】唯一确定轴宽 —— 窄轴后面紧跟的下一份副本数据可能恰好也单调,
    于是宽轴解释同样"通过". 消歧交给 map_spans 用副本冗余做交叉验证.
    """
    dims = [(rows, cols)]
    if min(rows, cols) == 1:
        dims.append((rows * cols, 0))

    out = []
    for pos in range(data_end, min(data_end + win, len(buf) - 2)):
        for r, c in dims:
            if r > 255 or c > 255 or buf[pos] != r or buf[pos + 1] != c:
                continue
            for rs, cs in AXIS_SIZES:
                if pos + 2 + rs * r + cs * c > len(buf):
                    continue
                try:
                    rax = decode(buf, pos + 2, r, "u16" if rs == 2 else "u8", endian)
                    cax = decode(buf, pos + 2 + rs * r, c, "u16" if cs == 2 else "u8",
                                 endian) if c else []
                except ValueError:
                    continue
                if not (_mono(rax) and _mono(cax)):
                    continue
                if (r > 1 and len(set(rax)) == 1) or (c > 1 and len(set(cax)) == 1):
                    continue
                out.append((pos, rax, cax, rs, cs))
    return out


def probe_descriptor(buf, data_end, rows, cols, win=160, endian="little"):
    """探测自描述符, 只取第一个候选. 无副本可交叉验证时用它."""
    c = probe_descriptors(buf, data_end, rows, cols, win, endian)
    return c[0] if c else None


def _desc_stride(addr, cd):
    pos, rax, cax, rs, cs = cd
    return (pos + 2 + rs * len(rax) + cs * len(cax)) - addr


def map_spans(buf, d, endian="little"):
    """
    算出一张定义的所有副本数据区 [(copy_idx, start, end_exclusive), ...],
    同时返回自动推导出的 stride 与描述符信息.
    """
    size = DTYPES[d["dtype"]][0]
    blk = d["rows"] * d["cols"] * size
    addr = d["addr"]

    cands = probe_descriptors(buf, addr + blk, d["rows"], d["cols"], endian=endian)
    desc, stride, how = (cands[0] if cands else None), blk, "packed"

    if d.get("stride"):
        stride, how = d["stride"], "given"
    elif cands and d["copies"] > 1:
        # 副本冗余交叉验证: 正确的 stride 必须让【下一份副本】的描述符也恰好落位.
        # 这是唯一能可靠区分 u16 轴与 u8 轴的判据 —— 单份副本时无从消歧.
        for cd in cands:
            st = _desc_stride(addr, cd)
            if st > 0 and probe_descriptors(buf, addr + st + blk, d["rows"], d["cols"],
                                            win=4, endian=endian):
                desc, stride, how = cd, st, "descriptor+copy"
                break
        else:
            desc, stride, how = cands[0], _desc_stride(addr, cands[0]), "descriptor?"
    elif cands:
        stride, how = _desc_stride(addr, cands[0]), "descriptor"

    spans = [(i, addr + i * stride, addr + i * stride + blk) for i in range(d["copies"])]
    return spans, stride, how, desc


# ----------------------------------------------------------------- 归属

def attribute(clusters, defs, ori, mod, endian="little", gap=32):
    """把聚簇归属到定义. 返回 (hits, unattributed)."""
    hits = []
    covered = []          # 已被某张表覆盖的字节区间

    for d in defs:
        try:
            spans, stride, how, desc = map_spans(ori, d, endian)
        except ValueError:
            continue
        for ci, s, e in spans:
            if e > len(ori):
                continue
            if ori[s:e] == mod[s:e]:
                continue
            n = d["rows"] * d["cols"]
            o = decode(ori, s, n, d["dtype"], endian)
            m = decode(mod, s, n, d["dtype"], endian)
            ch = sum(1 for i in range(n) if o[i] != m[i])
            ratios = [m[i] / o[i] for i in range(n) if o[i] != 0]
            f, off = d["factor"], d["offset"]
            hits.append({
                "name": d["name"] + ("" if d["copies"] == 1 else " c%d" % ci),
                "addr": s, "rows": d["rows"], "cols": d["cols"], "dtype": d["dtype"],
                "copy": ci, "copies": d["copies"], "stride": stride, "stride_from": how,
                "descriptor": ("0x%06X" % desc[0]) if desc else None,
                "row_axis": desc[1] if desc else None,
                "col_axis": desc[2] if desc else None,
                "cells": n, "changed": ch,
                "ori_raw": [min(o), max(o)], "mod_raw": [min(m), max(m)],
                "ori_phys": [min(o) * f + off, max(o) * f + off],
                "mod_phys": [min(m) * f + off, max(m) * f + off],
                "ratio": [round(min(ratios), 4), round(max(ratios), 4)] if ratios else None,
                "delta_raw": [min(m[i] - o[i] for i in range(n)),
                              max(m[i] - o[i] for i in range(n))],
                "unit": d["unit"], "factor": f, "offset": off,
                "_ori": o, "_mod": m,
            })
            covered.append((s, e))

    # 未归属 = 落在所有已覆盖区间之外的改动字节, 再按同一 gap 聚成精确子区间.
    # 按子区间而非整簇报告 —— 一个簇里常常同时装着"已识别的表"和"没识别出的表"
    # (实测: 0x1AAE0C..0x1AAF79 一个簇里就有两张不同的表).
    covered.sort()
    leftover = []
    for s, e, _ in clusters:
        for i in range(s, e + 1):
            if ori[i] == mod[i]:
                continue
            if not any(cs <= i < ce for cs, ce in covered):
                leftover.append(i)
    unattributed = [{"start": s, "end": e, "unexplained_bytes": c}
                    for s, e, c in cluster(leftover, gap)]

    hits.sort(key=lambda h: h["addr"])
    return hits, unattributed


def _fit_scale(o, m):
    """
    最小二乘拟合统一倍率 k, 并检验"整表同乘一个数再取整"这个假设是否成立.

    不能用 min/max 比值来判 —— 原始值小的时候取整误差会把比值散开
    (例: 110*1.25=137.5 -> 138, 比值 1.2545; 而 100*1.25=125, 比值 1.25),
    与量级相关的阈值不可靠. 残差判据 |m - round(o*k)| <= 1 与量级无关.
    """
    den = sum(a * a for a in o)
    if den == 0:
        return None
    k = sum(a * b for a, b in zip(o, m)) / den
    if all(abs(m[i] - round(o[i] * k)) <= 1 for i in range(len(o))):
        return k
    return None


def propose_op(h):
    """给一个改动提一个候选算子, 供人复核 —— 不是结论."""
    o, m = h["_ori"], h["_mod"]
    # flatten 优先判: "原来有形状、改完变常数"是安全相关的信号(监控/保护被拍平),
    # 必须先于 scale 报出来, 否则会被当成普通缩放放过去.
    if len(set(m)) == 1 and len(set(o)) > 1:
        return {"op": "flatten", "value": m[0]}
    k = _fit_scale(o, m)
    if k is not None:
        return {"op": "scale", "k": round(k, 4)}
    if h["delta_raw"][0] == h["delta_raw"][1]:
        return {"op": "offset_raw", "d": h["delta_raw"][0]}
    return {"op": "table", "k": None}


# ----------------------------------------------------------------- 定义文件

def read_defs(path):
    defs = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if not row.get("name") or str(row["name"]).startswith("#"):
                continue
            defs.append({
                "name":   row["name"].strip(),
                "addr":   parse_addr(row["addr"]),
                "rows":   int(row["rows"]), "cols": int(row["cols"]),
                "dtype":  row["dtype"].strip(),
                "copies": int(row.get("copies") or 1),
                "unit":   (row.get("unit") or "").strip(),
                "factor": float(row.get("factor") or 1.0),
                "offset": float(row.get("offset") or 0.0),
                "stride": int(row["stride"]) if row.get("stride") else None,
            })
    return defs


# ----------------------------------------------------------------- 报告

def report(clusters, hits, unattributed, ori, mod, gap):
    total = sum(c[2] for c in clusters)
    print("=" * 108)
    print("改动总量 %d 字节 (%.4f%%)   聚簇 %d 个 (gap<=%d)   范围 0x%06X..0x%06X"
          % (total, 100.0 * total / max(len(ori), 1), len(clusters), gap,
             clusters[0][0], clusters[-1][1]) if clusters else "无改动")
    print("=" * 108)

    if hits:
        print("\n%-34s %-9s %-8s %-6s %-22s %-22s %s"
              % ("MAP", "ADDR", "GEOM", "CELLS", "ORI", "MOD", "候选算子"))
        print("-" * 130)
        for h in hits:
            op = propose_op(h)
            ops = ("x%.4f" % op["k"]) if op["op"] == "scale" else \
                  ("raw%+d" % op["d"]) if op["op"] == "offset_raw" else \
                  ("flatten->%d" % op["value"]) if op["op"] == "flatten" else "table"
            u = (" " + h["unit"]) if h["unit"] else ""
            print("%-34s 0x%06X %-8s %-6s %-22s %-22s %s"
                  % (h["name"][:34], h["addr"], "%dx%d" % (h["rows"], h["cols"]),
                     "%d/%d" % (h["changed"], h["cells"]),
                     "%.6g..%.6g%s" % (h["ori_phys"][0], h["ori_phys"][1], u),
                     "%.6g..%.6g%s" % (h["mod_phys"][0], h["mod_phys"][1], u),
                     ops))

    if unattributed:
        print("\n!! 未归属改动 %d 处 —— 这些是定义文件没覆盖的表, 必须人工处理, 不得忽略:"
              % len(unattributed))
        for u in unattributed:
            print("   0x%06X..0x%06X   %d 字节无法解释" % (u["start"], u["end"], u["unexplained_bytes"]))
    elif hits:
        print("\n所有改动均已归属到命名 MAP.")


# ----------------------------------------------------------------- selftest

def _synth():
    """造一对合成 bin: 两张自描述表 + 一处定义外改动."""
    buf = bytearray(b"\x00" * 0x2000)

    def put16(a, vals):
        struct.pack_into("<%dH" % len(vals), buf, a, *vals)

    # A: 3x4 u16 @0x100, 描述符紧随其后, 2 份副本
    rows, cols = 3, 4
    for cpy in range(2):
        base = 0x100 + cpy * (rows * cols * 2 + 2 + 2 * rows + 2 * cols)
        put16(base, [100 + 10 * i + 100 * cpy for i in range(rows * cols)])
        buf[base + rows * cols * 2] = rows
        buf[base + rows * cols * 2 + 1] = cols
        put16(base + rows * cols * 2 + 2, [1000, 2000, 3000])
        put16(base + rows * cols * 2 + 2 + 2 * rows, [10, 20, 30, 40])
    # B: 1D 6 点 u16 @0x400, 描述符 [6][0]
    put16(0x400, [500, 600, 700, 800, 900, 1000])
    buf[0x40C], buf[0x40D] = 6, 0
    put16(0x40E, [800, 1600, 2400, 3200, 4000, 4800])

    # C: 2x5 u16 @0x600, 行轴 u16 / 列轴 u8 —— 复刻实测的档位轴形态, 2 份副本
    put16(0x600, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    buf[0x614], buf[0x615] = 2, 5
    put16(0x616, [2000, 4000])
    buf[0x61A:0x61F] = bytes([0, 1, 2, 3, 4])          # 列轴 u8
    C_STRIDE = 10 * 2 + 2 + 2 * 2 + 1 * 5              # = 31
    put16(0x600 + C_STRIDE, [11, 21, 31, 41, 51, 61, 71, 81, 91, 101])
    buf[0x600 + C_STRIDE + 20], buf[0x600 + C_STRIDE + 21] = 2, 5
    put16(0x600 + C_STRIDE + 22, [2000, 4000])
    buf[0x600 + C_STRIDE + 26:0x600 + C_STRIDE + 31] = bytes([0, 1, 2, 3, 4])

    ori = bytes(buf)
    mod = bytearray(buf)
    # A 两份副本都 x1.25
    for cpy in range(2):
        base = 0x100 + cpy * (rows * cols * 2 + 2 + 2 * rows + 2 * cols)
        v = list(struct.unpack_from("<12H", mod, base))
        struct.pack_into("<12H", mod, base, *[int(round(x * 1.25)) for x in v])
    # B 全部 +7 raw
    v = list(struct.unpack_from("<6H", mod, 0x400))
    struct.pack_into("<6H", mod, 0x400, *[x + 7 for x in v])
    # C 两份副本都 x2 —— 只有 stride 算对了, 第二份才会被正确归属
    for cpy in range(2):
        base = 0x600 + cpy * C_STRIDE
        v = list(struct.unpack_from("<10H", mod, base))
        struct.pack_into("<10H", mod, base, *[x * 2 for x in v])
    # 定义外的一处改动
    mod[0x1800:0x1804] = b"\xDE\xAD\xBE\xEF"
    return ori, bytes(mod)


def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        ok = ok and bool(cond)

    print("diff_cluster.py selftest")
    ori, mod = _synth()

    offs = diff_offsets(ori, mod)
    chk(len(offs) > 0, "diff 找到改动字节 (%d)" % len(offs))
    # MapA 两份副本间只隔 16 字节(描述符), gap=32 会把它们并成一簇 —— 这是预期行为,
    # 也是"簇 != 表"的原因: 归属必须靠定义, 不能靠簇.
    cl = cluster(offs, gap=32)
    chk(len(cl) == 4, "gap=32 聚簇数 == 4 (A0+A1 合并/B/C0+C1 合并/定义外), 实际 %d" % len(cl))
    chk(len(cluster(offs, gap=8)) == 6, "gap=8 能把各副本拆开 == 6, 实际 %d"
        % len(cluster(offs, gap=8)))

    stride = 3 * 4 * 2 + 2 + 2 * 3 + 2 * 4
    defs = [
        {"name": "MapA", "addr": 0x100, "rows": 3, "cols": 4, "dtype": "u16", "copies": 2,
         "unit": "Nm", "factor": 0.1, "offset": 0.0, "stride": None},
        {"name": "CurveB", "addr": 0x400, "rows": 1, "cols": 6, "dtype": "u16", "copies": 1,
         "unit": "%", "factor": 1.0, "offset": 0.0, "stride": None},
        {"name": "MapC", "addr": 0x600, "rows": 2, "cols": 5, "dtype": "u16", "copies": 2,
         "unit": "", "factor": 1.0, "offset": 0.0, "stride": None},
    ]

    spans, st, how, desc = map_spans(ori, defs[0])
    chk(how == "descriptor+copy", "MapA stride 经副本交叉验证, 得到 '%s'" % how)
    chk(st == stride, "MapA stride == %d, 实际 %d" % (stride, st))
    chk(desc is not None and desc[1] == [1000, 2000, 3000], "MapA 行轴解出 [1000,2000,3000]")

    spans_b, st_b, how_b, desc_b = map_spans(ori, defs[1])
    chk(desc_b is not None and desc_b[1] == [800, 1600, 2400, 3200, 4000, 4800],
        "CurveB 1D 描述符 [6][0] 命中, 轴解出正确")

    # 混合轴宽: 行轴 u16 + 列轴 u8. stride 必须是 31 而不是按双 u16 算的 36
    spans_c, st_c, how_c, desc_c = map_spans(ori, defs[2])
    chk(desc_c is not None and (desc_c[3], desc_c[4]) == (2, 1),
        "MapC 探到 行轴u16/列轴u8, 实际 %s" % (str((desc_c[3], desc_c[4])) if desc_c else "None"))
    chk(st_c == 31, "MapC stride == 31 (混合轴宽), 实际 %d" % st_c)
    chk(desc_c is not None and desc_c[2] == [0, 1, 2, 3, 4], "MapC 列轴解出 [0..4]")

    hits, un = attribute(cl, defs, ori, mod)
    chk(len(hits) == 5, "归属命中 5 条 (MapA x2 + CurveB + MapC x2), 实际 %d" % len(hits))
    c1 = [h for h in hits if h["name"] == "MapC c1"]
    chk(len(c1) == 1 and propose_op(c1[0])["k"] == 2.0,
        "MapC 第二份副本靠正确 stride 命中且倍率 x2")
    a0 = [h for h in hits if h["name"] == "MapA c0"][0]
    op = propose_op(a0)
    chk(op["op"] == "scale" and abs(op["k"] - 1.25) < 0.005,
        "MapA c0 候选算子 == scale k≈1.25, 实际 %s" % op)
    chk(a0["ratio"][0] < 1.25 < a0["ratio"][1] or a0["ratio"][0] == 1.25,
        "min/max 比值确实是散开的 %s —— 证明必须用残差判据而不是比值阈值" % a0["ratio"])
    b = [h for h in hits if h["name"] == "CurveB"][0]
    chk(propose_op(b) == {"op": "offset_raw", "d": 7}, "CurveB 候选算子 == raw+7")
    chk(a0["ori_phys"] == [10.0, 21.0], "factor 0.1 应用正确: %s" % a0["ori_phys"])

    # flatten 必须优先于 scale 被识别出来 (监控/保护被拍平的安全信号)
    flat = dict(a0, _ori=[10, 20, 30, 40], _mod=[251, 251, 251, 251],
                delta_raw=[211, 241], mod_raw=[251, 251])
    chk(propose_op(flat) == {"op": "flatten", "value": 251},
        "有形状 -> 常数 被判为 flatten 而不是 scale")

    chk(len(un) == 1 and un[0]["start"] == 0x1800,
        "定义外改动被报为未归属 @0x1800, 实际 %s" % un)

    # 副本齐全性哨兵: 只定义 1 份副本时, 第二份必须以【精确子区间】报出来
    defs2 = [dict(defs[0], copies=1), defs[1], defs[2]]
    hits2, un2 = attribute(cl, defs2, ori, mod)
    ca1 = 0x100 + stride
    chk(any(ca1 <= u["start"] < ca1 + 24 for u in un2),
        "漏定义的副本被精确报到 0x%X 附近, 实际 %s" % (ca1, un2))
    chk(len(hits2) == 4, "copies=1 时少归属一条 == 4, 实际 %d" % len(hits2))

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="ORI/MOD 全文件 diff -> 聚簇 -> 归属到命名 MAP")
    ap.add_argument("--ori"); ap.add_argument("--mod")
    ap.add_argument("--defs", help="定义 CSV")
    ap.add_argument("--gap", type=int, default=32, help="聚簇间隙容差, 默认 32")
    ap.add_argument("--endian", choices=["little", "big"], default="little")
    ap.add_argument("--json", help="产出 recipe JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not (a.ori and a.mod):
        ap.error("需要 --ori 与 --mod (或 --selftest)")

    ori, mod = load(a.ori), load(a.mod)
    if len(ori) != len(mod):
        print("!! 两文件大小不同: 0x%X vs 0x%X —— 先用 port_verify.py 对齐, 否则 diff 无意义"
              % (len(ori), len(mod)))
    cl = cluster(diff_offsets(ori, mod), a.gap)
    if not cl:
        print("两文件完全相同."); return 0

    defs = read_defs(a.defs) if a.defs else []
    hits, un = attribute(cl, defs, ori, mod, a.endian, a.gap) if defs else ([], [
        {"start": s, "end": e, "unexplained_bytes": c} for s, e, c in cl])
    report(cl, hits, un, ori, mod, a.gap)

    if not defs:
        print("\n(未提供 --defs: 只输出聚簇. 定义来源 -> damos-a2l-parser / winols-ols-parser /")
        print(" winols-screenshot-analyzer 抄定义树 / bin-to-a2l 穷举自描述容器)")

    if a.json:
        recipe = {
            "learned_from": {"ori": os.path.abspath(a.ori), "mod": os.path.abspath(a.mod),
                             "ori_size": len(ori), "mod_size": len(mod)},
            "gap": a.gap, "endian": a.endian,
            "entries": [dict({k: v for k, v in h.items() if not k.startswith("_")},
                             proposed=propose_op(h), verdict="TODO", note="") for h in hits],
            "unattributed": un,
        }
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(recipe, fh, indent=2, ensure_ascii=False)
        print("\nrecipe -> %s   (每条 verdict 字段必须人工改成 keep / fix / drop)" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

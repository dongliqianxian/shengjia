#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
port_verify.py — 【证】阶段硬闸门 (reference-tune-porting skill)

回答一个问题, 而且只回答这一个:
    在 ORI-A 上学到的配方, 每一张表在 ORI-B 上【能不能被证明还在原地】?

为什么必须有这一步
------------------
拿 A 车的地址去改 B 车的文件, 如果某张表因软件版本挪了位:
    diff 看不出来 / 解码看不出来 / 写盘不报错 / checksum 也能算对
    —— 装车才知道.
这是【静默灾难】: 不做这一步的时候, 一切看起来都是对的.
所以本脚本的默认姿态是【有罪推定】: 证不出来 = UNPROVEN = 必须从配方里剔除.

判定等级
--------
  PROVEN_IDENTICAL   两文件该地址字节完全相同           -> 直接照搬, 任何算子都安全
  PROVEN_BY_DESC     字节不同, 但同址自描述符(维度+轴)一致 -> 同一张表的不同标定值.
                     【相对算子(scale/offset)可以照搬; 绝对算子(flatten/设定值)必须重判】
  MOVED              原数据块在 B 里唯一出现于别处        -> 改地址后可用, 脚本给出新地址
  AMBIGUOUS          原数据块在 B 里出现多次              -> 不可用, 需人工消歧
  UNPROVEN           以上都不成立                        -> 必须剔除, 不得"大概是这个地址"

用法
----
  python port_verify.py --src A_ORI.bin --dst B_ORI.bin \
         --defs ../assets/defs_mevd1729_b20_8709.csv \
         --region 0x180000-0x1E0000 --json proof.json
  python port_verify.py --selftest

退出码: 0 = 全部 PROVEN_*; 1 = 存在 MOVED/AMBIGUOUS/UNPROVEN (可用于流水线卡关)
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diff_cluster import (DTYPES, load, read_defs, decode,          # noqa: E402
                          probe_descriptors, map_spans)

PROVEN = ("PROVEN_IDENTICAL", "PROVEN_BY_DESC", "PROVEN_BY_CONTEXT")

#: 上下文锚定的窗口与最低信息量要求. 上下文必须"有内容" —— 一片 0x00 或 0xFF
#: 在两个文件里当然也相同, 那不构成任何证据.
CTX_WIN = 32
CTX_MIN_DISTINCT = 8


def context_match(src, dst, s, e):
    """
    数据块【前后】的字节是否两文件一致.

    这是给"没有自描述符的表"和"1x1 标量"准备的第三条证据 ——
    它们既没有维度+轴可比对, 数据块本身又太短/太常见,
    在整片 flash 里能匹配上几十处, 靠搜索无法唯一定位.
    前后文一致说明这块地址周围的结构没有移动, 表还在原处.

    窗口自适应收缩: 相邻表的标定值在两个版本间也可能不同, 一刀切 32 字节会误杀.
    收缩到 8 字节仍要求足够的字节多样性, 否则一片 0x00/0xFF 会让任何地址都"通过".
    返回 (是否成立, 说明).
    """
    best_fail = "前后文不一致"
    for win in (CTX_WIN, 16, 8):
        a0, a1 = max(0, s - win), s
        b0, b1 = e, min(len(src), len(dst), e + win)
        if a1 - a0 < win or b1 - b0 < win:
            best_fail = "边界处上下文不足"
            continue
        ctx_s = src[a0:a1] + src[b0:b1]
        if ctx_s != dst[a0:a1] + dst[b0:b1]:
            continue
        need = CTX_MIN_DISTINCT if win >= CTX_WIN else max(4, win // 2)
        if len(set(ctx_s)) < need:
            best_fail = "前后文过于单一(%d 种字节), 不构成证据" % len(set(ctx_s))
            continue
        return True, "前后各 %d 字节完全一致(%d 种字节值), 地址结构未移动" % (win, len(set(ctx_s)))
    return False, best_fail


def region_similarity(a, b, lo, hi):
    n = min(hi, len(a), len(b)) - lo
    if n <= 0:
        return 0, 0
    same = sum(1 for i in range(lo, lo + n) if a[i] == b[i])
    return same, n


def find_all(hay, needle, limit=3):
    out, at = [], 0
    while len(out) < limit:
        i = hay.find(needle, at)
        if i < 0:
            break
        out.append(i)
        at = i + 1
    return out


def axes_of(buf, addr, rows, cols, size, endian):
    c = probe_descriptors(buf, addr + rows * cols * size, rows, cols, endian=endian)
    return (c[0][1], c[0][2], c[0][3], c[0][4]) if c else None


def verify_one(src, dst, d, ci, s, e, endian):
    size = DTYPES[d["dtype"]][0]
    blk = e - s
    r, c = d["rows"], d["cols"]
    out = {"name": d["name"] + ("" if d["copies"] == 1 else " c%d" % ci),
           "addr": s, "rows": r, "cols": c, "dtype": d["dtype"], "copy": ci}

    if e > len(dst):
        out["verdict"] = "UNPROVEN"
        out["why"] = "地址超出 B 文件长度 (0x%X)" % len(dst)
        return out

    if src[s:e] == dst[s:e]:
        out["verdict"] = "PROVEN_IDENTICAL"
        out["why"] = "同址 %d 字节完全相同" % blk
        return out

    ax_s, ax_d = axes_of(src, s, r, c, size, endian), axes_of(dst, s, r, c, size, endian)
    if ax_s and ax_d and ax_s == ax_d:
        n = r * c
        o = decode(src, s, n, d["dtype"], endian)
        m = decode(dst, s, n, d["dtype"], endian)
        out["verdict"] = "PROVEN_BY_DESC"
        out["why"] = "同址自描述符一致(维度 %dx%d + 轴逐点相同), 仅标定值不同" % (r, c)
        out["src_raw"] = [min(o), max(o)]
        out["dst_raw"] = [min(m), max(m)]
        out["relative_ops_safe"] = True
        return out

    ok, why = context_match(src, dst, s, e)
    if ok:
        n = r * c
        o = decode(src, s, n, d["dtype"], endian)
        m = decode(dst, s, n, d["dtype"], endian)
        out["verdict"] = "PROVEN_BY_CONTEXT"
        out["why"] = why + ("; 无自描述符可比" if not ax_d else "")
        out["src_raw"] = [min(o), max(o)]
        out["dst_raw"] = [min(m), max(m)]
        out["relative_ops_safe"] = True
        return out

    hits = find_all(dst, src[s:e])
    if len(hits) == 1:
        out["verdict"] = "MOVED"
        out["new_addr"] = hits[0]
        out["why"] = "原数据块在 B 中唯一出现于 0x%06X (偏移 %+d)" % (hits[0], hits[0] - s)
        return out
    if len(hits) > 1:
        out["verdict"] = "AMBIGUOUS"
        out["hits"] = hits
        out["why"] = "原数据块在 B 中出现 %d 次以上, 无法唯一定位" % len(hits)
        return out

    out["verdict"] = "UNPROVEN"
    bits = []
    if not ax_d:
        bits.append("同址探不到自描述符")
    elif ax_s != ax_d:
        bits.append("同址描述符不一致(维度或轴变了)")
    bits.append(why)
    bits.append("原数据块在 B 中也找不到")
    bits.append("-> 需外部定义源(A2L/DAMOS/OLS/工具定义树)对 B 单独确认")
    out["why"] = "; ".join(bits)
    return out


def run(src, dst, defs, endian, region):
    rows = []
    for d in defs:
        try:
            spans, stride, how, desc = map_spans(src, d, endian)
        except ValueError:
            rows.append({"name": d["name"], "addr": d["addr"], "verdict": "UNPROVEN",
                         "why": "在 A 文件里就读不出来 (地址/维度有误)"})
            continue
        for ci, s, e in spans:
            rows.append(verify_one(src, dst, d, ci, s, e, endian))
    return rows


def report(rows, src, dst, region):
    if region:
        lo, hi = region
        same, n = region_similarity(src, dst, lo, hi)
        print("标定区 0x%06X-0x%06X 相同度: %d/%d = %.2f%%" % (lo, hi, same, n, 100.0 * same / n))
        print("  (100%% = 两份 ORI 的 Datenstand 完全一致, 配方可整体照搬;")
        print("   低于 100%% 不等于不能移植, 但每张表都必须单独过下面的闸门)")
    print()
    w = max(len(r["name"]) for r in rows) if rows else 20
    w = min(w, 42)
    print("%-*s %-9s %-18s %s" % (w, "MAP", "ADDR", "VERDICT", "依据"))
    print("-" * (w + 90))
    for r in rows:
        print("%-*s 0x%06X %-18s %s" % (w, r["name"][:w], r.get("addr", 0),
                                        r["verdict"], r.get("why", "")))

    from collections import Counter
    cnt = Counter(r["verdict"] for r in rows)
    print("\n统计: " + "  ".join("%s=%d" % (k, v) for k, v in sorted(cnt.items())))

    bad = [r for r in rows if r["verdict"] not in PROVEN]
    if bad:
        print("\n!! %d 张表未通过闸门 —— 必须处理后才能进入【出】阶段:" % len(bad))
        for r in bad:
            act = ("把配方地址改成 0x%06X" % r["new_addr"]) if r["verdict"] == "MOVED" \
                else "从配方中剔除, 并在交付说明里写明这张表没做"
            print("   %-42s %-12s -> %s" % (r["name"][:42], r["verdict"], act))
    else:
        print("\n全部通过. 配方可以进入【出】阶段.")

    rel = [r for r in rows if r["verdict"] in ("PROVEN_BY_DESC", "PROVEN_BY_CONTEXT")]
    if rel:
        print("\n注意: 下列 %d 张表在两份 ORI 里【标定值不同】(结构相同)." % len(rel))
        print("      相对算子(scale/offset_raw)可照搬; 绝对算子(flatten/写定值)必须按 B 的原值重判:")
        for r in rel:
            print("   %-42s A raw %s  ->  B raw %s"
                  % (r["name"][:42], r.get("src_raw"), r.get("dst_raw")))
    return 0 if not bad else 1


# ----------------------------------------------------------------- selftest

def _mk(seed_lo, tail=b""):
    """造一份含 3 张自描述表的 bin."""
    import struct
    buf = bytearray(b"\x00" * 0x1000)

    def table(base, rows, cols, first):
        struct.pack_into("<%dH" % (rows * cols), buf, base,
                         *[first + 3 * i for i in range(rows * cols)])
        p = base + rows * cols * 2
        buf[p], buf[p + 1] = rows, cols
        struct.pack_into("<%dH" % rows, buf, p + 2, *[1000 * (i + 1) for i in range(rows)])
        struct.pack_into("<%dH" % cols, buf, p + 2 + 2 * rows,
                         *[10 * (i + 1) for i in range(cols)])
    table(0x100, 3, 4, 500)          # T1
    table(0x200, 2, 6, seed_lo)      # T2 值随 seed 变
    table(0x300, 4, 4, 900)          # T3
    if tail:
        buf[0x800:0x800 + len(tail)] = tail
    return bytes(buf)


def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        ok = ok and bool(cond)

    print("port_verify.py selftest")
    import struct
    src = _mk(700)

    defs = [
        {"name": "T1", "addr": 0x100, "rows": 3, "cols": 4, "dtype": "u16", "copies": 1,
         "unit": "", "factor": 1.0, "offset": 0.0, "stride": None},
        {"name": "T2", "addr": 0x200, "rows": 2, "cols": 6, "dtype": "u16", "copies": 1,
         "unit": "", "factor": 1.0, "offset": 0.0, "stride": None},
        {"name": "T3", "addr": 0x300, "rows": 4, "cols": 4, "dtype": "u16", "copies": 1,
         "unit": "", "factor": 1.0, "offset": 0.0, "stride": None},
    ]

    # 情形 1: 完全相同
    rows = run(src, src, defs, "little", None)
    chk(all(r["verdict"] == "PROVEN_IDENTICAL" for r in rows),
        "同一文件 -> 全部 PROVEN_IDENTICAL")

    # 情形 2: T2 标定值不同, 结构与轴不变 (跨软件版本的典型情况)
    dst2 = _mk(1500)
    rows = run(src, dst2, defs, "little", None)
    v = {r["name"]: r["verdict"] for r in rows}
    chk(v["T1"] == "PROVEN_IDENTICAL" and v["T3"] == "PROVEN_IDENTICAL",
        "未变的表仍为 PROVEN_IDENTICAL")
    chk(v["T2"] == "PROVEN_BY_DESC", "只有值变、结构不变的表 -> PROVEN_BY_DESC, 实际 %s" % v["T2"])
    t2 = [r for r in rows if r["name"] == "T2"][0]
    chk(t2.get("relative_ops_safe") is True and t2["src_raw"] != t2["dst_raw"],
        "PROVEN_BY_DESC 标注了相对算子可用, 并给出两边原值 %s -> %s"
        % (t2.get("src_raw"), t2.get("dst_raw")))
    # 情形 2b: 没有自描述符的表 / 1x1 标量 —— 只能靠前后文锚定
    noise = bytes((i * 37 + 11) & 0xFF for i in range(0x100))
    a = bytearray(_mk(700)); b = bytearray(_mk(700))
    a[0x900:0xA00] = noise; b[0x900:0xA00] = noise          # 有信息量的前后文
    struct.pack_into("<H", a, 0x950, 3200)
    struct.pack_into("<H", b, 0x950, 4000)                  # 标量值不同
    dscal = [{"name": "Scalar", "addr": 0x950, "rows": 1, "cols": 1, "dtype": "u16",
              "copies": 1, "unit": "", "factor": 1.0, "offset": 0.0, "stride": None}]
    rr = run(bytes(a), bytes(b), dscal, "little", None)
    chk(rr[0]["verdict"] == "PROVEN_BY_CONTEXT",
        "1x1 标量靠前后文锚定 -> PROVEN_BY_CONTEXT, 实际 %s" % rr[0]["verdict"])
    chk(rr[0]["src_raw"] == [3200, 3200] and rr[0]["dst_raw"] == [4000, 4000],
        "并给出两边原值 3200 -> 4000")

    # 前后文是一片空白时不得当成证据 (否则整片 0x00 的区域会全部"通过")
    a2 = bytearray(_mk(700)); b2 = bytearray(_mk(700))
    struct.pack_into("<H", a2, 0xB00, 3200)
    struct.pack_into("<H", b2, 0xB00, 4000)
    dscal2 = [dict(dscal[0], addr=0xB00)]
    rr2 = run(bytes(a2), bytes(b2), dscal2, "little", None)
    chk(rr2[0]["verdict"] == "UNPROVEN",
        "空白前后文不构成证据 -> UNPROVEN, 实际 %s" % rr2[0]["verdict"])

    # 情形 3: T3 整体搬到 0x800
    moved = bytearray(_mk(700))
    blk = 0x300, 0x300 + 4 * 4 * 2 + 2 + 2 * 4 + 2 * 4
    payload = bytes(moved[blk[0]:blk[1]])
    moved[blk[0]:blk[1]] = b"\x00" * (blk[1] - blk[0])
    moved[0x800:0x800 + len(payload)] = payload
    rows = run(src, bytes(moved), defs, "little", None)
    v = {r["name"]: r for r in rows}
    chk(v["T3"]["verdict"] == "MOVED" and v["T3"]["new_addr"] == 0x800,
        "整块搬家 -> MOVED 且给出新地址 0x%X, 实际 %s"
        % (0x800, (v["T3"]["verdict"], hex(v["T3"].get("new_addr", 0)))))

    # 情形 4: T3 被抹掉且找不到
    gone = bytearray(_mk(700))
    struct.pack_into("<16H", gone, 0x300, *[7] * 16)
    gone[0x320], gone[0x321] = 0, 0                  # 破坏描述符
    rows = run(src, bytes(gone), defs, "little", None)
    v = {r["name"]: r["verdict"] for r in rows}
    chk(v["T3"] == "UNPROVEN", "结构与数据都对不上 -> UNPROVEN, 实际 %s" % v["T3"])

    # 情形 5: 退出码语义 (report 会打印, 用 devnull 吞掉输出只看返回值)
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        rc_ok = report(run(src, src, defs, "little", None), src, src, None)
        rc_bad = report(run(src, bytes(gone), defs, "little", None), src, bytes(gone), None)
    chk(rc_ok == 0, "全 PROVEN 时 report 返回 0 (流水线放行)")
    chk(rc_bad == 1, "存在 UNPROVEN 时 report 返回 1 (流水线卡关)")

    # 情形 6: B 文件更短
    short = src[:0x280]
    rows = run(src, short, defs, "little", None)
    v = {r["name"]: r["verdict"] for r in rows}
    chk(v["T3"] == "UNPROVEN", "地址超出 B 长度 -> UNPROVEN 而不是崩溃")

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="把配方从 ORI-A 移植到 ORI-B 前的逐表证明")
    ap.add_argument("--src", help="学配方所用的 ORI-A")
    ap.add_argument("--dst", help="要移植过去的 ORI-B")
    ap.add_argument("--defs", help="定义 CSV")
    ap.add_argument("--region", help="标定区, 形如 0x180000-0x1E0000")
    ap.add_argument("--endian", choices=["little", "big"], default="little")
    ap.add_argument("--json", help="产出 proof JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not (a.src and a.dst and a.defs):
        ap.error("需要 --src --dst --defs (或 --selftest)")

    src, dst = load(a.src), load(a.dst)
    region = None
    if a.region:
        lo, hi = a.region.split("-")
        region = (int(lo, 16), int(hi, 16))

    print("A (学配方): %s  0x%X" % (os.path.basename(a.src), len(src)))
    print("B (要移植): %s  0x%X" % (os.path.basename(a.dst), len(dst)))
    if len(src) != len(dst):
        print("两文件大小不同 —— 读取设备不同很正常, 不影响判定, 逐表证明说了算.")
    print()

    rows = run(src, dst, read_defs(a.defs), a.endian, region)
    rc = report(rows, src, dst, region)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"src": os.path.abspath(a.src), "dst": os.path.abspath(a.dst),
                       "region": a.region, "rows": rows}, fh, indent=2, ensure_ascii=False)
        print("\nproof -> %s" % a.json)
    return rc


if __name__ == "__main__":
    sys.exit(main())

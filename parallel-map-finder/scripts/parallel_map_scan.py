#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parallel_map_scan.py — 种子驱动的【平行地图】批量查找(parallel-map-finder 技能)。

场景:改标定时同一物理量在 bin 里常有多份高度相似的表(多变体/多模式/多档位/bank1-2/
进排气/主控+限制副本/Level-2 监控镜像)。只改一份漏掉平行副本 → 别的模式/档位/bank 行为不一致
或监控镜像不同步 → limp/报错。手动找齐极易漏,本引擎自动找齐。

与盲扫的区别:blind-map-finder 是【无种子】从零盲扫整片 flash;本引擎是【有种子】——你已知一张表
(地址+维度+dtype+字节序),在同一 bin 内找所有【结构/数值分布相似】的副本。

相似度两条正交线:
  * 数值接近(near-identical): 归一化 L1 距离小 → 真复制/监控镜像(必须一起改)。
  * 形状相同(shape variant): Pearson 相关高但数值有缩放/偏移 → 模式/档位/变体副本。

用法:
    # 种子用地址+维度(从同一 bin 读种子)
    python parallel_map_scan.py --bin ECU.bin --seed-addr 0x1AF00 --rows 16 --cols 16 --dtype u16 --endian le
    # 只找近似复制(默认) / 也找形状变体
    python parallel_map_scan.py --bin ECU.bin --seed-addr 0x1AF00 --rows 16 --cols 16 --mode both
    python parallel_map_scan.py --bin ECU.bin ... --base 0x800000 --json out.json
    python parallel_map_scan.py --selftest

纪律(doctrine):
  * 输出是【候选 + 相似度分级】,不是"这些一定是同族表"。near-identical 高可信;shape-variant 需
    再经 ecu-map-analysis 判功能、ghidra 看谁读它。
  * 找齐平行副本的目的是【一起改 / 核对监控镜像同步】(doctrine 联动清单"漏改一环")。
  * 字节读写交 bin 引擎;写后 checksum 交 checksum-crc-engine;无种子从零找表用 blind-map-finder。
"""
import sys, struct, math, argparse, json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DT = {"u8": ("B", 1), "s8": ("b", 1), "u16": ("H", 2), "s16": ("h", 2),
      "u32": ("I", 4), "s32": ("i", 4)}


def read_values(buf, dtype, endian):
    """整段按 dtype/字节序解码为元素数组(width 对齐)。"""
    ch, w = DT[dtype]
    fmt = ("<" if endian == "le" else ">") + ch
    n = len(buf) // w
    return list(struct.unpack_from(("<" if endian == "le" else ">") + ch * n, buf, 0)), w


def _pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _l1n(seed, cand, span):
    denom = (span if span > 0 else (max((abs(x) for x in seed), default=1) or 1)) * len(seed)
    return sum(abs(c - s) for c, s in zip(seed, cand)) / denom


def classify(l1n, corr):
    # near-identical 用数值距离(真复制);shape-variant 用相关性——缩放/偏移会让 L1 变大但
    # 相关性仍≈1,那正是同形状变体,不能用 L1 阈值把它误降级。
    if l1n < 0.02:
        return "near-identical(复制/监控镜像)", 0
    if corr is not None and corr >= 0.98:
        return "shape-variant(缩放/偏移:模式/档位副本)", 1
    if corr is not None and corr >= 0.90:
        return "similar-shape(弱)", 2
    return None, 9


def find_parallel(V, seed_idx, B, mode="identical"):
    """在元素数组 V 中找与 V[seed_idx:seed_idx+B] 相似的块。返回候选列表。"""
    seed = V[seed_idx:seed_idx + B]
    if len(seed) < B:
        raise ValueError("种子越界")
    m_seed = sum(seed) / B
    span_seed = max(seed) - min(seed)
    sq_seed = sum(x * x for x in seed) / B
    std_seed = math.sqrt(max(0.0, sq_seed - m_seed * m_seed))
    if span_seed == 0:
        print("[!] 种子是常数块(无动态范围),不适合形状匹配;将只按数值接近找。", file=sys.stderr)

    # 前缀和(均值/方差 O(1) 预筛)
    N = len(V)
    P = [0] * (N + 1)
    Q = [0] * (N + 1)
    for i, v in enumerate(V):
        P[i + 1] = P[i] + v
        Q[i + 1] = Q[i] + v * v

    cands = []
    for i in range(0, N - B + 1):
        s = P[i + B] - P[i]
        mean = s / B
        var = (Q[i + B] - Q[i]) / B - mean * mean
        std = math.sqrt(max(0.0, var))
        # 预筛
        if mode == "identical":
            if span_seed > 0 and abs(mean - m_seed) > 0.05 * span_seed + 1:
                continue
            if abs(std - std_seed) > 0.15 * std_seed + 1:
                continue
        else:  # shape / both: 松均值,按 std 比例(缩放变体 std 也缩放)
            if std_seed > 0 and not (0.25 * std_seed <= std <= 4.0 * std_seed):
                continue
        # 全量度量
        cand = V[i:i + B]
        l1n = _l1n(seed, cand, span_seed)
        corr = _pearson(seed, cand)
        label, tier = classify(l1n, corr)
        if label is None:
            continue
        if mode == "identical" and tier != 0:
            continue
        cands.append({"idx": i, "l1n": l1n, "corr": corr, "label": label,
                      "tier": tier, "mean": mean, "vmin": min(cand), "vmax": max(cand)})

    # 非极大抑制:同一块的重叠窗口只留最佳(半径=B)
    cands.sort(key=lambda c: (c["tier"], c["l1n"], -(c["corr"] or 0)))
    kept, used = [], []
    for c in cands:
        if any(abs(c["idx"] - u) < B for u in used):
            continue
        used.append(c["idx"])
        kept.append(c)
    return seed, kept


def report(seed, kept, seed_idx, width, base, B, rows, cols):
    baseoff = base or 0
    print("=" * 92)
    print("平行地图查找 — 种子 @0x%X  %dx%d  %d 字节/块" %
          (baseoff + seed_idx * width, rows, cols, B * width))
    print("种子值域 %d..%d,共找到 %d 个平行候选(已排除种子自身、已 NMS 去重)" %
          (min(seed), max(seed), len([k for k in kept if k["idx"] != seed_idx])))
    print("=" * 92)
    ni = [k for k in kept if k["tier"] == 0 and k["idx"] != seed_idx]
    sv = [k for k in kept if k["tier"] == 1 and k["idx"] != seed_idx]
    sw = [k for k in kept if k["tier"] == 2 and k["idx"] != seed_idx]
    if ni:
        print("★ near-identical(必须一起改 / 核对监控镜像同步):")
        for k in ni:
            print("  0x%08X  L1=%.4f corr=%s  值域%d..%d  %s"
                  % (baseoff + k["idx"] * width, k["l1n"],
                     "%.3f" % k["corr"] if k["corr"] is not None else "—",
                     k["vmin"], k["vmax"], k["label"]))
    if sv:
        print("● shape-variant(模式/档位/变体副本,数值缩放或偏移):")
        for k in sv:
            print("  0x%08X  L1=%.4f corr=%.3f  值域%d..%d  %s"
                  % (baseoff + k["idx"] * width, k["l1n"], k["corr"],
                     k["vmin"], k["vmax"], k["label"]))
    if sw:
        print("○ similar-shape(弱,需人工确认):")
        for k in sw[:10]:
            print("  0x%08X  corr=%.3f 值域%d..%d" %
                  (baseoff + k["idx"] * width, k["corr"], k["vmin"], k["vmax"]))
    if not (ni or sv or sw):
        print("未找到平行副本。可能:该表唯一 / 维度或 dtype 给错 / 副本被缩放超出阈值(试 --mode shape)。")
    print("-" * 92)
    print("下一步:near-identical 多为复制/监控镜像→改主控时一并改;shape-variant 用 ecu-map-analysis")
    print("判功能、ghidra 看谁读它;确认同族后纳入 stage1 修改矩阵,避免漏改一环(doctrine 联动清单)。")


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("ok " if c else "FAIL ") + m)
        ok = ok and c

    import random
    random.seed(7)
    rows, cols = 8, 8
    B = rows * cols
    # 种子:带结构的块(行递增+列缓变),避免退化
    seed = [100 + r * 12 + c * 3 + (r * c % 5) for r in range(rows) for c in range(cols)]

    def pack(vals):
        return struct.pack("<%dH" % len(vals), *[v & 0xFFFF for v in vals])

    noise = lambda k: [random.randrange(50, 4000) for _ in range(k)]
    dup = seed[:]                                   # 精确复制
    scaled = [int(round(v * 1.10)) for v in seed]   # 形状同、缩放 1.1
    shifted = [v + 40 for v in seed]                # 形状同、偏移 +40

    blob = bytearray()
    blob += pack(noise(0x80))
    seed_at = len(blob) // 2
    blob += pack(seed)
    blob += pack(noise(0x40))
    dup_at = len(blob) // 2
    blob += pack(dup)
    blob += pack(noise(0x30))
    scaled_at = len(blob) // 2
    blob += pack(scaled)
    blob += pack(noise(0x20))
    shifted_at = len(blob) // 2
    blob += pack(shifted)
    blob += pack(noise(0x80))

    V, w = read_values(bytes(blob), "u16", "le")

    # identical 模式:应找到精确复制,不含缩放/偏移
    seedvals, kept = find_parallel(V, seed_at, B, mode="identical")
    ids = {k["idx"] for k in kept}
    chk(dup_at in ids, "identical 找到精确复制")
    ni_idx = {k["idx"] for k in kept if k["tier"] == 0 and k["idx"] != seed_at}
    chk(dup_at in ni_idx, "复制归类 near-identical")
    chk(scaled_at not in ni_idx and shifted_at not in ni_idx, "缩放/偏移不混入 near-identical")

    # shape 模式:应额外找到缩放与偏移变体(corr 高)
    _, kept2 = find_parallel(V, seed_at, B, mode="shape")
    ids2 = {k["idx"]: k for k in kept2}
    chk(scaled_at in ids2 and ids2[scaled_at]["tier"] <= 1, "shape 找到缩放变体(corr 高)")
    chk(shifted_at in ids2 and ids2[shifted_at]["tier"] <= 1, "shape 找到偏移变体(corr 高)")
    chk(ids2[scaled_at]["corr"] > 0.99, "缩放变体相关性>0.99")

    # NMS:精确复制处不应产生一堆重叠候选
    dup_cluster = [k for k in kept if abs(k["idx"] - dup_at) < B]
    chk(len(dup_cluster) == 1, "NMS:复制块只留 1 个(无重叠冗余)")

    # 种子自身应在结果里但报告会排除;确认能识别自身(L1=0)
    self_hit = [k for k in kept if k["idx"] == seed_at]
    chk(self_hit and self_hit[0]["l1n"] < 1e-9, "种子自身 L1=0 可识别(报告中排除)")

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Seed-driven parallel/sibling map finder within one bin")
    ap.add_argument("--bin", dest="binpath", default=None)
    ap.add_argument("--seed-addr", type=lambda s: int(s, 0), default=None, help="种子表地址(文件偏移或含 --base)")
    ap.add_argument("--rows", type=int, default=None)
    ap.add_argument("--cols", type=int, default=None)
    ap.add_argument("--dtype", choices=sorted(DT), default="u16")
    ap.add_argument("--endian", choices=["le", "be"], default="le")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0, help="镜像基址(地址=文件偏移+base)")
    ap.add_argument("--mode", choices=["identical", "shape", "both"], default="identical")
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not (a.binpath and a.seed_addr is not None and a.rows and a.cols):
        ap.error("需要 --bin --seed-addr --rows --cols(或 --selftest)")

    buf = open(a.binpath, "rb").read()
    V, w = read_values(buf, a.dtype, a.endian)
    seed_off = a.seed_addr - a.base
    if seed_off < 0 or seed_off % w:
        ap.error("种子偏移非 %d 字节对齐或为负(检查 --base/--dtype)" % w)
    seed_idx = seed_off // w
    B = a.rows * a.cols
    mode = "identical" if a.mode == "identical" else "shape"
    seed, kept = find_parallel(V, seed_idx, B, mode=mode)
    if a.mode == "both":
        # both:先 identical 再 shape 合并(shape 已含近似,这里直接用 shape 全集)
        pass
    report(seed, kept, seed_idx, w, a.base, B, a.rows, a.cols)
    if a.json:
        out = {"seed_addr": a.base + seed_idx * w, "rows": a.rows, "cols": a.cols,
               "dtype": a.dtype, "endian": a.endian, "mode": a.mode,
               "candidates": [{"addr": a.base + k["idx"] * w, "l1n": k["l1n"],
                               "corr": k["corr"], "class": k["label"],
                               "vmin": k["vmin"], "vmax": k["vmax"]}
                              for k in kept if k["idx"] != seed_idx]}
        json.dump(out, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("JSON ->", a.json)


if __name__ == "__main__":
    main()

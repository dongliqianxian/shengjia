#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
invariant_locate.py — 在无定义的 ECU/TCU bin 里定位【已知物理常数】。

与 speed-limiter-removal/scripts/value_locate.py 的分工:
  * value_locate.py : 你只知道【一个】值(限速 250、怠速 800)。多缩放×双字节序穷举。
                      单值搜索天生高误报,它自己也这么说。
  * 本引擎          : 你知道的是【一组物理相关的值】,或【两个物理相关的量】。
                      利用"关系"而不是"数值"来定位 —— 误报低一到两个数量级。

三种模式:

  1) --vector  尺度无关的向量指纹【最强】
     已知一组物理上成套的值(变速箱各档速比、多缸同类修正、标准电阻表…)。
     关键:定点标度未知,但【向量的形状与标度无关】。把候选与目标各自除以自身均值再比较,
     标度自动消掉。命中后 implied scale 是【副产品】—— 你顺带拿到了这个 bin 的定点格式。
     实测:丰田 FJ 4.0 (DENSO V850, 736KB) 用 A750F 五速速比向量全片只命中 3 处,
     误差 0.06%,并直接给出 Q12(1.000=4096) 这个标度。

  2) --joint   共享标度的联合约束
     已知【两个】物理相关但数值无关的量(主减速比 ~3.4-4.6 与轮胎半径 ~0.35-0.43 m)。
     要求二者落在【同一个窗口】内且【用同一个标度】解码 —— 同族常数共用标度是物理事实,
     这条约束比"分别搜两个值"强得多。

  3) --verify  把候选升级成证据
     给定"地址:期望原始值"清单,报告:
       - 拼接字节签名在全文件的出现次数(=1 才叫唯一)
       - 两两共现在全文件的出现次数(=1 才叫唯一)
     这是可以写进报告的【证据陈述】,不是感觉。

用法:
    # 1. 向量指纹:找 A750F 五速速比(含倒挡)
    python invariant_locate.py --bin ECU.bin \
        --vector 3.520,2.042,1.400,1.000,0.716,3.224 --label A750F

    # 2. 联合约束:主减速比 + 轮胎半径(米),共享标度
    python invariant_locate.py --bin ECU.bin \
        --joint final_drive=3.4:4.6 --joint tyre_r=0.35:0.43 \
        --scales 1000,4096,1024,256,100 --window 24

    # 3. 证据化
    python invariant_locate.py --bin ECU.bin \
        --verify 0xFA20:390,0xFA22:3727,0xFA24:2566

    python invariant_locate.py --selftest

纪律(doctrine):
  * 本引擎输出仍是【候选 + 证据强度】。唯一性检验只排除巧合,不证明语义。
  * 【语义与角色必须由代码坐实】—— 见 SKILL.md 的"基址寄存器"与"算术用法定角色"两节。
    没有代码印证之前,一律说"候选"。
  * 写盘后 checksum 交 checksum-crc-engine;字节读写交 bin 引擎。
"""
import sys, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DT = {"u8": 1, "u16": 2, "u32": 4}


# ---------------------------------------------------------------- readers
def make_reader(buf, dtype, big):
    """Return f(offset)->int for the given dtype/endianness, byte-granular."""
    n = len(buf)
    if dtype == "u8":
        return lambda o: buf[o] if o < n else None
    if dtype == "u16":
        if big:
            return lambda o: (buf[o] << 8) | buf[o + 1] if o + 1 < n else None
        return lambda o: buf[o] | (buf[o + 1] << 8) if o + 1 < n else None
    if big:
        return lambda o: ((buf[o] << 24) | (buf[o + 1] << 16) |
                          (buf[o + 2] << 8) | buf[o + 3]) if o + 3 < n else None
    return lambda o: (buf[o] | (buf[o + 1] << 8) |
                      (buf[o + 2] << 16) | (buf[o + 3] << 24)) if o + 3 < n else None


# ------------------------------------------------------- 1. vector mode
def vector_scan(buf, target, dtype="u16", tol=0.025, steps=(2, 4),
                endians=("le", "be"), also_reversed=True, align=2, top=40):
    """Scale-invariant shape match of a known physical vector."""
    L = len(target)
    if L < 3:
        raise ValueError("--vector needs at least 3 values (shorter = meaningless)")
    variants = [("fwd", list(target))]
    if also_reversed:
        variants.append(("rev", list(reversed(target))))

    n = len(buf)
    hits = []
    for vname, t in variants:
        tmean = sum(t) / L
        tn = [x / tmean for x in t]
        # cheap gate: ratio of first to last element
        gate = t[0] / t[-1]
        for en in endians:
            rd = make_reader(buf, dtype, en == "be")
            w = DT[dtype]
            for step in steps:
                span = (L - 1) * step + w
                for off in range(0, n - span, align):
                    a = rd(off)
                    if not a:
                        continue
                    z = rd(off + (L - 1) * step)
                    if not z:
                        continue
                    r = a / z
                    if r < gate * 0.90 or r > gate * 1.10:
                        continue                       # gate rejects >99%
                    vals = [rd(off + i * step) for i in range(L)]
                    if any((v is None or v <= 0) for v in vals):
                        continue
                    m = sum(vals) / L
                    err = 0.0
                    ok = True
                    for v, tt in zip(vals, tn):
                        e = abs(v / m - tt) / tt
                        if e > tol:
                            ok = False
                            break
                        if e > err:
                            err = e
                    if not ok:
                        continue
                    scale = sum(v / tt for v, tt in zip(vals, t)) / L
                    hits.append({"err": err, "off": off, "endian": en, "step": step,
                                 "order": vname, "scale": scale, "raw": vals,
                                 "dtype": dtype})
    hits.sort(key=lambda h: h["err"])
    return hits[:top]


def nice_scale(s):
    """Name the implied fixed-point scale if it is a recognisable one."""
    for p in range(0, 17):
        v = 1 << p
        if abs(s - v) / v < 0.004:
            return f"Q{p} (1.0 = {v})" if p else "raw (1.0 = 1)"
    for d in (10, 100, 1000, 10000):
        if abs(s - d) / d < 0.004:
            return f"decimal x{d}"
    return "non-standard"


# -------------------------------------------------------- 2. joint mode
def _match(spec, x):
    """spec is ('band', lo, hi) or ('exact', value, tol)."""
    if spec[0] == "band":
        return spec[1] <= x <= spec[2]
    return abs(x - spec[1]) <= spec[2]


def joint_scan(buf, specs, scales, dtype="u16", window=24,
               endians=("le", "be"), align=2, top=40):
    """Two+ physically related quantities, SAME scale, inside one window.

    Ranking uses EMPIRICAL surprise, not an a-priori band width: raw values are
    NOT uniformly distributed (small integers are everywhere in a bin), so a
    band that looks narrow on paper can still flood the results. We therefore
    count how many positions each spec actually matches at each scale and rank
    by -log2(observed rate). A scale where the constants are genuinely rare
    wins over one where they are common.
    """
    import math
    if len(specs) < 2:
        raise ValueError("need at least two quantities")
    n = len(buf)
    w = DT[dtype]
    npos = max(1, (n - w) // align)
    out = []
    diag = []
    for en in endians:
        rd = make_reader(buf, dtype, en == "be")
        for sc in scales:
            # positions per spec
            pos = {}
            for name, spec in specs.items():
                p = []
                for o in range(0, n - w, align):
                    v = rd(o)
                    if v is None or v <= 0:
                        continue
                    x = v / sc
                    if _match(spec, x):
                        p.append((o, v, x))
                pos[name] = p
            names = list(specs.keys())
            surprise = 0.0
            for k in names:
                rate = max(1, len(pos[k])) / npos
                surprise += -math.log2(min(1.0, rate))
            if any(len(pos[k]) == 0 for k in names):
                continue
            anchor = min(names, key=lambda k: len(pos[k]))
            others = [k for k in names if k != anchor]
            idx = {k: {o: (v, x) for o, v, x in pos[k]} for k in others}
            for o, v, x in pos[anchor]:
                combo = {anchor: (o, v, x)}
                okall = True
                for k in others:
                    best = None
                    for d in range(-window, window + 1, align):
                        oo = o + d
                        if d == 0 or oo < 0 or oo not in idx[k]:
                            continue
                        if best is None or abs(d) < abs(best[0] - o):
                            best = (oo, *idx[k][oo])
                    if best is None:
                        okall = False
                        break
                    combo[k] = best
                if not okall:
                    continue
                spread = max(c[0] for c in combo.values()) - min(c[0] for c in combo.values())
                out.append({"endian": en, "scale": sc, "spread": spread,
                            "surprise": surprise, "combo": combo})
            diag.append({"endian": en, "scale": sc, "surprise": surprise,
                         "per_spec": {k: len(pos[k]) for k in names}})
    # Rank by empirical surprise first (a hit where the constants are genuinely
    # rare beats a hit where they are common), then by how tightly they cluster.
    out.sort(key=lambda h: (-h["surprise"], h["spread"]))
    return out[:top], diag


# ------------------------------------------------------- 3. verify mode
def _findall(buf, pat):
    res, i = [], 0
    while True:
        i = buf.find(pat, i)
        if i < 0:
            return res
        res.append(i)
        i += 1


def verify(buf, items, dtype="u16", endian="le"):
    """items: list of (addr, expected_raw). Report uniqueness evidence."""
    w = DT[dtype]
    big = endian == "be"

    def enc(v):
        return v.to_bytes(w, "big" if big else "little")

    rep = {"items": [], "signature": None, "pairs": []}
    items = sorted(items)
    for a, want in items:
        got = int.from_bytes(buf[a:a + w], "big" if big else "little")
        rep["items"].append({"addr": a, "expected": want, "actual": got,
                             "match": got == want,
                             "alone": len(_findall(buf, enc(want)))})
    # contiguous signature (only meaningful if the items are adjacent)
    lo = items[0][0]
    hi = items[-1][0] + w
    if hi - lo <= 64:
        sig = buf[lo:hi]
        rep["signature"] = {"start": lo, "len": len(sig),
                            "count": len(_findall(buf, sig)),
                            "hex": " ".join(f"{b:02X}" for b in sig)}
    # pairwise co-location across the whole file
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a1, v1 = items[i]
            a2, v2 = items[j]
            d = a2 - a1
            p1 = set(_findall(buf, enc(v1)))
            p2 = set(_findall(buf, enc(v2)))
            co = sum(1 for x in p1 if (x + d) in p2)
            rep["pairs"].append({"v1": v1, "v2": v2, "delta": d,
                                 "n1": len(p1), "n2": len(p2), "colocated": co})
    return rep


# ------------------------------------------------------------- CLI glue
def parse_joint(s):
    """name=lo:hi  -> a physical BAND (funnel: use when you don't know the value)."""
    name, rng = s.split("=", 1)
    lo, hi = rng.split(":", 1)
    return name.strip(), ("band", float(lo), float(hi))


def parse_expect(s):
    """name=value[:tol] -> an EXACT expected value (discriminator).

    Default tol is 0.2% relative. This is the mode to prefer whenever you know
    the candidate value (you usually do: your car has 3.727 or 3.909 or 4.10),
    because it turns the search from a funnel into a decision.
    """
    name, rest = s.split("=", 1)
    if ":" in rest:
        val, tol = rest.split(":", 1)
        val, tol = float(val), float(tol)
    else:
        val = float(rest)
        tol = abs(val) * 0.002
    return name.strip(), ("exact", val, tol)


def spec_str(spec):
    if spec[0] == "band":
        return f"band {spec[1]} .. {spec[2]}"
    return f"exact {spec[1]} +/- {spec[2]:g}"


def selftest():
    import struct
    ok = True
    # synthetic bin: A750F ratios at Q12 planted at 0x100, noise elsewhere
    buf = bytearray((i * 37 + 11) % 251 for i in range(0x800))
    ratios = [3.520, 2.042, 1.400, 1.000, 0.716]
    for i, r in enumerate(ratios):
        struct.pack_into("<H", buf, 0x100 + 2 * i, int(round(r * 4096)))
    hits = vector_scan(bytes(buf), ratios, tol=0.02, endians=("le",), steps=(2,))
    got = [h for h in hits if h["off"] == 0x100]
    print(f"  vector_scan finds planted vector : {'PASS' if got else 'FAIL'}")
    ok &= bool(got)
    if got:
        s = got[0]["scale"]
        print(f"  implied scale ~4096              : {'PASS' if abs(s-4096)<20 else 'FAIL'} ({s:.1f})")
        print(f"  scale named correctly            : {nice_scale(s)}")
        ok &= abs(s - 4096) < 20

    # joint: plant 3727 and 2566 adjacent, both /1000
    buf2 = bytearray((i * 91 + 7) % 253 for i in range(0x800))
    struct.pack_into("<H", buf2, 0x200, 390)
    struct.pack_into("<H", buf2, 0x202, 3727)
    struct.pack_into("<H", buf2, 0x204, 2566)
    j, _ = joint_scan(bytes(buf2), {"fd": ("exact", 3.727, 0.01),
                                    "r": ("band", 0.35, 0.43)},
                      [1000], endians=("le",), window=16)
    found = [h for h in j if any(c[0] == 0x202 for c in h["combo"].values())]
    print(f"  joint_scan finds planted pair    : {'PASS' if found else 'FAIL'}")
    ok &= bool(found)

    v = verify(bytes(buf2), [(0x200, 390), (0x202, 3727), (0x204, 2566)])
    allm = all(x["match"] for x in v["items"])
    print(f"  verify reads back planted words  : {'PASS' if allm else 'FAIL'}")
    ok &= allm
    uniq = v["signature"]["count"] == 1
    print(f"  signature unique in synthetic bin: {'PASS' if uniq else 'FAIL'}")
    ok &= uniq
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin")
    ap.add_argument("--vector", help="comma-separated known physical values")
    ap.add_argument("--label", default="vector")
    ap.add_argument("--joint", action="append", default=[],
                    help="name=lo:hi   physical band (funnel). Repeatable.")
    ap.add_argument("--expect", action="append", default=[],
                    help="name=value[:tol]  exact expected value (discriminator). "
                         "Prefer this whenever you know the candidate value. Repeatable.")
    ap.add_argument("--scales", default="1000,4096,1024,256,128,100,10",
                    help="shared fixed-point scales to try in --joint")
    ap.add_argument("--verify", help="addr:raw,addr:raw,...  (addr may be 0x..)")
    ap.add_argument("--dtype", default="u16", choices=list(DT))
    ap.add_argument("--endian", default="both", choices=("le", "be", "both"))
    ap.add_argument("--tol", type=float, default=0.025)
    ap.add_argument("--window", type=int, default=24)
    ap.add_argument("--steps", default="2,4")
    ap.add_argument("--align", type=int, default=2)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.bin:
        ap.error("--bin required (or --selftest)")
    buf = open(a.bin, "rb").read()
    ends = ("le", "be") if a.endian == "both" else (a.endian,)
    print(f"file: {a.bin}  size={len(buf)} = 0x{len(buf):X}\n")

    if a.vector:
        t = [float(x) for x in a.vector.split(",")]
        steps = tuple(int(x) for x in a.steps.split(","))
        hits = vector_scan(buf, t, a.dtype, a.tol, steps, ends, align=a.align, top=a.top)
        print("=" * 78)
        print(f"VECTOR FINGERPRINT  '{a.label}'  {t}")
        print(f"  scale-invariant shape match, tol={a.tol*100:.1f}%")
        print("=" * 78)
        print(f"hits: {len(hits)}")
        for h in hits:
            phys = [round(v / h["scale"], 4) for v in h["raw"]]
            print(f"\n  0x{h['off']:06X}  err={h['err']*100:5.2f}%  {h['endian'].upper()} "
                  f"{h['dtype']} step={h['step']} order={h['order']}")
            print(f"      implied scale = {h['scale']:.2f}   -> {nice_scale(h['scale'])}")
            print(f"      raw  = {h['raw']}")
            print(f"      phys = {phys}")
        if not hits:
            print("  (none) — widen --tol, try --steps, or the vector is not stored as a block")

    if a.joint or a.expect:
        specs = dict(parse_joint(s) for s in a.joint)
        specs.update(dict(parse_expect(s) for s in a.expect))
        scales = [float(x) for x in a.scales.split(",")]
        hits, diag = joint_scan(buf, specs, scales, a.dtype, a.window, ends,
                                align=a.align, top=a.top)
        print("\n" + "=" * 78)
        print("JOINT CONSTRAINT (shared scale, one window)")
        for k, sp in specs.items():
            print(f"  {k}: {spec_str(sp)}")
        print("=" * 78)
        print("  per-scale discrimination (higher surprise = fewer chance hits):")
        for d in sorted(diag, key=lambda x: -x["surprise"]):
            per = " ".join(f"{k}={v}" for k, v in d["per_spec"].items())
            print(f"    {d['endian'].upper()} scale={d['scale']:<8g} "
                  f"surprise={d['surprise']:6.1f} bits   band hits: {per}")
        print(f"\nhits: {len(hits)}   (ranked by surprise, then tightness)")
        for h in hits:
            print(f"\n  {h['endian'].upper()} scale={h['scale']:g} spread={h['spread']}B "
                  f"surprise={h['surprise']:.1f} bits")
            for k, (o, v, x) in sorted(h["combo"].items(), key=lambda kv: kv[1][0]):
                print(f"      0x{o:06X}  raw={v:<6d} -> {k} = {x:.4f}")
        if not hits:
            print("  (none) — try more --scales, a bigger --window, or the other endian")

    if a.verify:
        items = []
        for part in a.verify.split(","):
            ad, val = part.split(":")
            items.append((int(ad, 0), int(val, 0)))
        for en in ends:
            r = verify(buf, items, a.dtype, en)
            print("\n" + "=" * 78)
            print(f"EVIDENCE ({en.upper()}, {a.dtype})")
            print("=" * 78)
            for it in r["items"]:
                flag = "OK " if it["match"] else "MISMATCH"
                print(f"  0x{it['addr']:06X}  expect {it['expected']:<6d} got {it['actual']:<6d} "
                      f"[{flag}]   this value alone occurs {it['alone']}x in file")
            if r["signature"]:
                s = r["signature"]
                verdict = "UNIQUE — coincidence excluded" if s["count"] == 1 else \
                          f"occurs {s['count']}x — NOT unique"
                print(f"\n  contiguous signature @0x{s['start']:06X} ({s['len']} bytes): {s['hex']}")
                print(f"      -> {verdict}")
            print("\n  pairwise co-location across the whole file:")
            for p in r["pairs"]:
                v = "UNIQUE" if p["colocated"] == 1 else f"{p['colocated']}x"
                print(f"      {p['v1']} & {p['v2']} at delta {p['delta']:+d}: "
                      f"alone {p['n1']}x / {p['n2']}x, co-located {v}")
            print("\n  NOTE: uniqueness excludes coincidence. It does NOT prove semantics.")
            print("        Confirm with code (SKILL.md: base register + arithmetic role).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

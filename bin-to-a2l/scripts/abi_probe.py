#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 1 of bin-to-a2l: discover the calibration container ABI.

Many ECUs store each table as a SELF-DESCRIBING descriptor, e.g.
    [u16 rows][u16 cols][X axis: rows*u16][Y axis: cols*u16][data: rows*cols*u16]
If that is true for a given image, the whole map inventory follows mechanically.
If it is NOT true, this skill does not apply -> hand back to `blind-map-finder`.

Method: hypothesis-test several plausible layouts x both endiannesses over the whole
image, score each by how many SELF-CONSISTENT blocks it yields (plausible dims AND
both axes strictly monotonic), and compare the winner against a wrong-endian control.

The wrong-endian control is the key guard: a layout that only wins by a small margin
over reading dimensions in the wrong byte order is finding noise, not structure.
"""
import argparse, json, math, struct, sys
from collections import Counter


# ---------------------------------------------------------------- code presence
# Calibrated against a KNOWN reference (MD1CS089 image where Ghidra proved
# 9,633 functions in 0x000000-0x3FFFFF and pure data in 0x400000-0x5FFFFF):
#     proven CODE region : per-64KB entropy mean 6.41, max 7.06
#     proven CAL  region : per-64KB entropy mean 3.47, max 4.81
# The two populations are cleanly separated; 6.2 sits between them.
CODE_ENTROPY = 6.2


def _entropy(b):
    if not b:
        return 0.0
    c = Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def code_check(data, block=0x10000):
    """Does this image contain the ASW, or is it a calibration partition only?

    Matters because it decides whether a disassembly route exists AT ALL. A bench
    read of just the cal partition cannot be disassembled - the code is not there,
    and no amount of Ghidra setup changes that.
    """
    blocks = []
    for s in range(0, len(data), block):
        blk = data[s:s + block]
        ff = blk.count(0xFF) / len(blk)
        z = blk.count(0) / len(blk)
        if ff > 0.90 or z > 0.90:
            blocks.append((s, None))          # fill
            continue
        blocks.append((s, _entropy(blk)))
    live = [e for _, e in blocks if e is not None]
    code = [(s, e) for s, e in blocks if e is not None and e > CODE_ENTROPY]
    return dict(blocks=blocks,
                live_blocks=len(live),
                fill_blocks=len(blocks) - len(live),
                max_entropy=max(live) if live else 0.0,
                mean_entropy=(sum(live) / len(live)) if live else 0.0,
                code_blocks=code,
                has_code=bool(code))

# ---------------------------------------------------------------- layouts
# each returns (x_axis_off, y_axis_off, data_off, total_len) given (o, rows, cols)
def _L(name, fn, note):
    fn.name = name; fn.note = note; return fn

LAYOUTS = [
    _L("A_hdr_XY_data",
       lambda o, r, c, w: (o+4, o+4+r*2, o+4+r*2+c*2, 4+r*2+c*2+r*c*w),
       "[rows][cols][Xaxis][Yaxis][data]"),
    _L("B_hdr_swapped",
       lambda o, r, c, w: (o+4, o+4+c*2, o+4+c*2+r*2, 4+r*2+c*2+r*c*w),
       "[cols][rows][Xaxis][Yaxis][data] - dim header transposed"),
    _L("C_hdr_data_XY",
       lambda o, r, c, w: (o+4+r*c*w, o+4+r*c*w+r*2, o+4, 4+r*2+c*2+r*c*w),
       "[rows][cols][data][Xaxis][Yaxis]"),
    _L("D_hdr_pad4_XY_data",
       lambda o, r, c, w: (o+8, o+8+r*2, o+8+r*2+c*2, 8+r*2+c*2+r*c*w),
       "[rows][cols][pad4][Xaxis][Yaxis][data]"),
    _L("E_hdr_YX_data",
       lambda o, r, c, w: (o+4+c*2, o+4, o+4+r*2+c*2, 4+r*2+c*2+r*c*w),
       "[rows][cols][Yaxis][Xaxis][data] - axis order swapped"),
]


def probe(data, min_dim=4, max_dim=48, min_cells=16, widths=(2, 1), quiet=False):
    N = len(data)

    def mk(endian):
        p = '<' if endian == 'le' else '>'
        u = lambda o: struct.unpack_from(p + 'H', data, o)[0]
        s = lambda o: struct.unpack_from(p + 'h', data, o)[0]
        return u, s

    def mono(rd, o, n):
        if o < 0 or o + n * 2 > N:
            return False
        prev = rd(o)
        for i in range(1, n):
            v = rd(o + i * 2)
            if v <= prev:
                return False
            prev = v
        return True

    results = []
    for endian in ('le', 'be'):
        u, s = mk(endian)
        for w in widths:
            for fn in LAYOUTS:
                hits = 0
                o = 0
                while o < N - 8:
                    r = u(o); c = u(o + 2)
                    if min_dim <= r <= max_dim and min_dim <= c <= max_dim and r * c >= min_cells:
                        xo, yo, do, tot = fn(o, r, c, w)
                        if o + tot <= N and 0 <= xo and 0 <= yo:
                            if (mono(u, xo, r) or mono(s, xo, r)) and \
                               (mono(u, yo, c) or mono(s, yo, c)):
                                hits += 1
                                o += 2
                                continue
                    o += 2
                results.append(dict(layout=fn.name, note=fn.note, endian=endian,
                                    width=w, hits=hits))
                if not quiet:
                    print(f"    {fn.name:22s} {endian}  data_width={w}  hits={hits}",
                          file=sys.stderr)
    results.sort(key=lambda d: -d['hits'])
    return results


def verdict(results, min_hits=200, min_ratio=8.0):
    best = results[0]
    # control: same layout+width, opposite endianness
    ctrl = next((r for r in results
                 if r['layout'] == best['layout'] and r['width'] == best['width']
                 and r['endian'] != best['endian']), None)
    ctrl_hits = ctrl['hits'] if ctrl else 0
    ratio = best['hits'] / max(ctrl_hits, 1)

    # structurally different runner-up (different layout family)
    other = next((r for r in results if r['layout'] != best['layout']
                  and r['endian'] == best['endian']), None)

    # residual ambiguities
    amb = []
    ties_dim = [r for r in results
                if r['endian'] == best['endian'] and r['width'] == best['width']
                and r['layout'] in ('B_hdr_swapped', 'E_hdr_YX_data')
                and r['hits'] >= best['hits'] * 0.75]
    if ties_dim:
        amb.append("ROW/COL order and axis order cannot be settled by structure alone "
                   "(%s scores within 25%%). Resolve by decoding one map and checking the "
                   "axis reads as a real physical quantity (e.g. RPM)."
                   % ", ".join(r['layout'] for r in ties_dim))
    tie_w = [r for r in results
             if r['layout'] == best['layout'] and r['endian'] == best['endian']
             and r['width'] != best['width'] and r['hits'] >= best['hits'] * 0.95]
    if tie_w:
        amb.append("DATA WIDTH is not determined by axis monotonicity (u8 and u16 score "
                   "the same). Resolve by physical band: decode one known-domain map "
                   "(boost/pressure/temperature) and see which width lands in range.")

    ok = best['hits'] >= min_hits and ratio >= min_ratio
    return dict(self_describing=ok, best=best, control_hits=ctrl_hits,
                endian_ratio=ratio, runner_up=other, ambiguities=amb,
                recommendation=("PROCEED with bin-to-a2l" if ok else
                                "NOT a self-describing container -> use `blind-map-finder`"))


def main():
    ap = argparse.ArgumentParser(description="Discover the calibration container ABI")
    ap.add_argument("--bin", dest="binpath")
    ap.add_argument("--min-dim", type=int, default=4)
    ap.add_argument("--max-dim", type=int, default=48)
    ap.add_argument("--min-cells", type=int, default=16)
    ap.add_argument("--json", dest="jsonout")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.binpath:
        ap.error("--bin required (or --selftest)")

    data = open(a.binpath, 'rb').read()
    cc = code_check(data)
    print("probing %d layouts x 2 endiannesses x 2 data widths over %.1f MB ..."
          % (len(LAYOUTS), len(data) / 1e6), file=sys.stderr)
    res = probe(data, a.min_dim, a.max_dim, a.min_cells)
    v = verdict(res)

    print("=" * 78)
    print("CONTAINER ABI PROBE")
    print("=" * 78)
    print(f"{'layout':<24}{'endian':>7}{'width':>7}{'hits':>9}")
    for r in res[:8]:
        print(f"{r['layout']:<24}{r['endian']:>7}{r['width']:>7}{r['hits']:>9}")
    b = v['best']
    print("-" * 78)
    print(f"winner            : {b['layout']}  ({b['note']})")
    print(f"endianness        : {b['endian']}   data width: {b['width']} byte(s)")
    print(f"blocks found      : {b['hits']}")
    print(f"wrong-endian ctrl : {v['control_hits']}   ratio {v['endian_ratio']:.1f}x")
    print(f"self-describing   : {v['self_describing']}")
    print(f"-> {v['recommendation']}")
    if v['ambiguities']:
        print("\nRESIDUAL AMBIGUITIES (must be resolved before trusting the inventory):")
        for s in v['ambiguities']:
            print("  * " + s)

    print("\n" + "-" * 78)
    print("CODE PRESENCE  (decides whether a disassembly route exists at all)")
    print("-" * 78)
    print("  non-fill 64KB blocks: %d   fill blocks: %d"
          % (cc['live_blocks'], cc['fill_blocks']))
    print("  entropy  mean %.2f   max %.2f   (code threshold %.1f)"
          % (cc['mean_entropy'], cc['max_entropy'], CODE_ENTROPY))
    if cc['has_code']:
        lo = min(s for s, _ in cc['code_blocks'])
        hi = max(s for s, _ in cc['code_blocks'])
        print("  -> CONTAINS CODE: %d block(s), 0x%06X-0x%06X"
              % (len(cc['code_blocks']), lo, hi + 0xFFFF))
        print("     a disassembly route exists; setup via `ghidra-ida-setup`.")
        print("     NOTE: on self-describing platforms xref-based map finding still")
        print("     fails (see SKILL.md section 6) - code is useful for LOGIC, not addresses.")
    else:
        print("  -> NO CODE: this is a CALIBRATION PARTITION ONLY.")
        print("     Disassembly is not 'hard' here, it is impossible: the ASW is not in")
        print("     the file. Identifying an unknown map by code needs a FULL dump.")
        print("     Reference populations (per 64KB, measured on a proven MD1CS089 image):")
        print("       proven code region : mean 6.41  max 7.06")
        print("       proven cal  region : mean 3.47  max 4.81")
    if a.jsonout:
        json.dump(dict(results=res, verdict={k: x for k, x in v.items()
                                             if k != 'runner_up'}),
                  open(a.jsonout, 'w'), indent=1)
        print(f"\nwrote {a.jsonout}")
    return 0 if v['self_describing'] else 2


def selftest():
    """synthesise an image with known descriptors and confirm layout A is recovered"""
    import random
    random.seed(7)
    buf = bytearray(random.getrandbits(8) for _ in range(400000))
    made = 0
    off = 1000
    while off < 380000 and made < 60:
        r, c = random.choice([(8, 8), (12, 10), (16, 16), (20, 18)])
        need = 4 + r * 2 + c * 2 + r * c * 2
        struct.pack_into('<H', buf, off, r)
        struct.pack_into('<H', buf, off + 2, c)
        v = random.randint(500, 1500)
        for i in range(r):
            struct.pack_into('<H', buf, off + 4 + i * 2, v); v += random.randint(50, 400)
        v = 0
        for i in range(c):
            struct.pack_into('<H', buf, off + 4 + r * 2 + i * 2, v); v += random.randint(50, 500)
        for i in range(r * c):
            struct.pack_into('<H', buf, off + 4 + r * 2 + c * 2 + i * 2,
                             random.randint(1000, 3000))
        off += need + random.randint(20, 300)
        made += 1
    res = probe(bytes(buf), quiet=True)
    v = verdict(res, min_hits=20, min_ratio=3.0)
    ok = v['best']['layout'] == 'A_hdr_XY_data' and v['best']['endian'] == 'le'
    print("selftest: planted %d descriptors" % made)
    print("          winner = %s / %s / width %d, hits=%d (control %d, %.1fx)"
          % (v['best']['layout'], v['best']['endian'], v['best']['width'],
             v['best']['hits'], v['control_hits'], v['endian_ratio']))
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

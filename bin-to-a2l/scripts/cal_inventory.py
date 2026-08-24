#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 2 of bin-to-a2l: exhaustive inventory once the container ABI is known.

Emits, for every descriptor found: address, dims, both axis addresses + decoded
ranges, value statistics, an axis-kind guess, and a ROUGHNESS metric.

Roughness (mean |2nd difference| / full range, along each axis) separates
BASE maps from CORRECTION maps. Measured on a real MD1CS089 image:
    base maps (rail/boost/fuel/torque) : 0.6% - 2.8%
    correction maps (injection timing) : 6.4% - 11.9%
No overlap. This matters because correction maps are cell-by-cell dyno results:
scaling them destroys work that cannot be reproduced.
"""
import argparse, json, math, struct, sys

# axis-kind heuristics: (name, factor, unit, test(first,last,n,signed))
AXIS_KINDS = [
    ("TEMP",  0.1,   "degC",   lambda a, b, n, s: s and a < 0 and -600 <= a and b <= 1600),
    ("PEDAL", 1/81.92, "%",    lambda a, b, n, s: b == 8192),
    ("RPM",   0.5,   "rpm",    lambda a, b, n, s: 0 <= a and 5000 <= b <= 9600 and n >= 8),
    ("IQ",    0.01,  "mg/stk", lambda a, b, n, s: a == 0 and 2000 <= b <= 12000),
    ("LAMB",  0.01,  "-",      lambda a, b, n, s: a == 0 and 100 <= b <= 800),
    ("PCT",   0.1,   "%",      lambda a, b, n, s: a == 0 and 200 <= b <= 1200),
    ("PRESS", 0.1,   "bar",    lambda a, b, n, s: 500 <= a and b <= 30000),
    ("HPA",   1.0,   "hPa",    lambda a, b, n, s: 600 <= a and b <= 4000),
]


def load_abi(path):
    if not path:
        return dict(layout="A_hdr_XY_data", endian="le", width=2)
    j = json.load(open(path))
    b = j["verdict"]["best"] if "verdict" in j else j
    return dict(layout=b["layout"], endian=b["endian"], width=b["width"])


def make_readers(data, endian):
    p = '<' if endian == 'le' else '>'
    return (lambda o: struct.unpack_from(p + 'H', data, o)[0],
            lambda o: struct.unpack_from(p + 'h', data, o)[0])


def scan(data, abi, min_dim=4, max_dim=48, min_cells=16):
    if abi["layout"] != "A_hdr_XY_data":
        print("WARNING: only layout A is implemented for inventory; probe reported %s"
              % abi["layout"], file=sys.stderr)
    u, s = make_readers(data, abi["endian"])
    N = len(data)
    w = abi["width"]

    def mono(rd, o, n):
        prev = rd(o)
        for i in range(1, n):
            v = rd(o + i * 2)
            if v <= prev:
                return False
            prev = v
        return True

    def axis(o, n):
        for rd, sg in ((u, False), (s, True)):
            if mono(rd, o, n):
                return [rd(o + i * 2) for i in range(n)], sg
        return None, False

    out, o = [], 0
    while o < N - 8:
        r, c = u(o), u(o + 2)
        if min_dim <= r <= max_dim and min_dim <= c <= max_dim and r * c >= min_cells:
            need = 4 + r * 2 + c * 2 + r * c * w
            if o + need <= N:
                xa, xs = axis(o + 4, r)
                if xa:
                    ya, ys = axis(o + 4 + r * 2, c)
                    if ya:
                        out.append(record(data, u, s, o, r, c, w, xa, xs, ya, ys))
                        o += 2
                        continue
        o += 2
    return out


def kind_of(a, b, n, signed):
    for nm, f, un, t in AXIS_KINDS:
        try:
            if t(a, b, n, signed):
                return nm, f, un
        except Exception:
            pass
    return "RAW", 1.0, "-"


def record(data, u, s, hdr, r, c, w, xa, xs, ya, ys):
    dat = hdr + 4 + r * 2 + c * 2
    n = r * c
    uv = [u(dat + i * 2) for i in range(n)] if w == 2 else list(data[dat:dat + n])
    sv = [s(dat + i * 2) for i in range(n)] if w == 2 else \
         [x - 256 if x > 127 else x for x in data[dat:dat + n]]
    rng = max(uv) - min(uv)
    g = [uv[i * c:(i + 1) * c] for i in range(r)]
    if rng and c > 2 and r > 2:
        d2c = sum(abs(g[i][j-1] - 2*g[i][j] + g[i][j+1])
                  for i in range(r) for j in range(1, c-1)) / (r * (c - 2))
        d2r = sum(abs(g[i-1][j] - 2*g[i][j] + g[i+1][j])
                  for j in range(c) for i in range(1, r-1)) / (c * (r - 2))
        rough = (round(d2c / rng * 100, 2), round(d2r / rng * 100, 2))
    else:
        rough = (0.0, 0.0)
    xk, xf, xu = kind_of(xa[0], xa[-1], r, xs)
    yk, yf, yu = kind_of(ya[0], ya[-1], c, ys)
    return dict(hdr=hdr, data=dat, rows=r, cols=c, width=w,
                x_addr=hdr + 4, x_kind=xk, x_raw=[xa[0], xa[-1]],
                x_phys=[round(xa[0] * xf, 2), round(xa[-1] * xf, 2)], x_unit=xu,
                y_addr=hdr + 4 + r * 2, y_kind=yk, y_raw=[ya[0], ya[-1]],
                y_phys=[round(ya[0] * yf, 2), round(ya[-1] * yf, 2)], y_unit=yu,
                umin=min(uv), umax=max(uv), smin=min(sv), smax=max(sv),
                neg=sum(1 for v in sv if v < 0), flat=(min(uv) == max(uv)),
                rough_x=rough[0], rough_y=rough[1],
                klass=("FLAT" if min(uv) == max(uv) else
                       "CORRECTION" if max(rough) >= 5.0 else
                       "BASE" if max(rough) <= 3.0 else "MIXED"))


def main():
    ap = argparse.ArgumentParser(description="Exhaustive calibration inventory")
    ap.add_argument("--bin", dest="binpath")
    ap.add_argument("--abi", help="abi_probe.py --json output")
    ap.add_argument("--json", dest="jsonout")
    ap.add_argument("--named-only", action="store_true",
                    help="only rows where BOTH axes were classified")
    ap.add_argument("--min-dim", type=int, default=4)
    ap.add_argument("--max-dim", type=int, default=48)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.binpath:
        ap.error("--bin required")

    data = open(a.binpath, 'rb').read()
    abi = load_abi(a.abi)
    maps = scan(data, abi, a.min_dim, a.max_dim)
    named = [m for m in maps if m['x_kind'] != 'RAW' and m['y_kind'] != 'RAW'
             and not m['flat']]
    show = named if a.named_only else maps

    print("=" * 108)
    print("CALIBRATION INVENTORY   layout=%s endian=%s width=%d" %
          (abi['layout'], abi['endian'], abi['width']))
    print("total descriptors: %d    both axes classified & non-flat: %d" %
          (len(maps), len(named)))
    print("=" * 108)
    print(f"{'data':>9} {'dims':>7} {'X axis':>22} {'Y axis':>22} "
          f"{'values(raw)':>16} {'rough%':>11} {'class':>10}")
    for m in show[:a.top]:
        print(f"0x{m['data']:06X} {m['rows']:3d}x{m['cols']:<3d} "
              f"{m['x_kind']:>6}{str(m['x_phys']):>16} "
              f"{m['y_kind']:>6}{str(m['y_phys']):>16} "
              f"[{m['umin']:6d},{m['umax']:6d}] "
              f"{m['rough_x']:5.1f}/{m['rough_y']:<5.1f} {m['klass']:>10}")
    if len(show) > a.top:
        print(f"... {len(show)-a.top} more (use --json for the full set)")

    from collections import Counter
    print("\nclass census:", dict(Counter(m['klass'] for m in maps)))
    print("axis-pair census (top 10):")
    for k, v in Counter((m['x_kind'], m['y_kind']) for m in named).most_common(10):
        print(f"   {k[0]:>6} x {k[1]:<6} : {v}")

    if a.jsonout:
        json.dump(maps, open(a.jsonout, 'w'), indent=0)
        print(f"\nwrote {len(maps)} records to {a.jsonout}")
    return 0


def selftest():
    import random
    random.seed(3)
    buf = bytearray(b'\x00' * 200000)
    # one smooth BASE map and one rough CORRECTION map, same dims
    for off, rough in ((1000, False), (20000, True)):
        r = c = 16
        struct.pack_into('<H', buf, off, r); struct.pack_into('<H', buf, off + 2, c)
        for i in range(r):
            struct.pack_into('<H', buf, off + 4 + i * 2, 1600 + i * 400)      # RPM axis
        for j in range(c):
            struct.pack_into('<H', buf, off + 4 + r * 2 + j * 2, j * 500)     # IQ axis
        base = off + 4 + r * 2 + c * 2
        for i in range(r):
            for j in range(c):
                v = 1000 + i * 40 + j * 60
                if rough:
                    v += random.randint(-400, 400)
                struct.pack_into('<H', buf, base + (i * c + j) * 2, max(0, v))
    maps = scan(bytes(buf), dict(layout="A_hdr_XY_data", endian="le", width=2))
    got = {m['data']: m for m in maps}
    a1 = 1000 + 4 + 64
    a2 = 20000 + 4 + 64
    ok = (a1 in got and a2 in got
          and got[a1]['klass'] == 'BASE' and got[a2]['klass'] == 'CORRECTION'
          and got[a1]['x_kind'] == 'RPM')
    for a, lbl in ((a1, 'smooth'), (a2, 'rough')):
        if a in got:
            m = got[a]
            print("  %-7s 0x%06X rough=%.1f/%.1f class=%s xkind=%s"
                  % (lbl, a, m['rough_x'], m['rough_y'], m['klass'], m['x_kind']))
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

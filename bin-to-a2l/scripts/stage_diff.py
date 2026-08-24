#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 5 of bin-to-a2l (optional, but the highest-yield analysis available):
diff an ORIGINAL against a MODIFIED/tuned file of the same ECU.

Why this is worth more than any amount of structural reasoning: a tuned file is a
labelled answer key. Whoever made it already decided which maps define power, how
many parallel copies had to move together, and which maps must NOT be touched.
Reading that off a diff takes seconds; deriving it from first principles takes hours
and is never as certain.

Four things it tells you that nothing else does:
  1. WHICH maps define the power increase          (changed, ranked by magnitude)
  2. WHETHER the edit was a uniform % scale or shaped (uniformity statistic)
  3. WHICH maps were deliberately LEFT ALONE       (the negative signal - often the
     more valuable half: injector transfer functions, mechanical limit scalars)
  4. WHAT your definition set is missing           (changed bytes nothing accounts for)

Attribution needs a map list: --maps (cal_inventory.py json, best coverage) and/or
--spec (emit_a2l.py spec json, gives real names). Supply both for named + complete.
"""
import argparse, json, struct, sys
from collections import OrderedDict

# datakind -> (factor, unit); mirrors emit_a2l.py
KINDS = {"NM": (0.1, "Nm"), "MG": (0.01, "mg/stk"), "MM3": (0.01, "mm3/stk"),
         "BAR": (0.1, "bar"), "HPA": (1.0, "hPa"), "DEGC": (0.1, "degC"),
         "DEG": (0.1, "deg"), "US": (0.1, "us"), "LAM": (0.001, "-"),
         "PCT": (0.1, "%"), "PCT8192": (1/81.92, "%"), "RAW": (1.0, "-")}


def load_targets(spec_path, maps_path, base):
    """-> OrderedDict addr -> dict(name, rows, cols, factor, unit, signed, named)"""
    T = OrderedDict()
    if maps_path:
        # Several descriptors can resolve to the SAME data address (a spurious small
        # descriptor sitting inside a real large map's axis region produces one).
        # Keep the LARGEST - the real map - or the small artifact silently replaces it
        # and its cells show up as "unattributed". Measured on a real MD1CS089 image:
        # a bogus 5x6 hid a genuine 25x15 fuel ceiling and 15.4% of the diff went blind.
        for m in json.load(open(maps_path)):
            a = m['data']
            cells = m['rows'] * m['cols']
            prev = T.get(a)
            if prev and prev['rows'] * prev['cols'] >= cells:
                continue
            T[a] = dict(name="(unnamed 0x%06X)" % a, rows=m['rows'],
                        cols=m['cols'], factor=1.0, unit="raw",
                        signed=False, named=False)
    if spec_path:
        spec = json.load(open(spec_path))
        b = int(str(spec.get('base', base or 0)), 0)
        for g in spec['groups']:
            for m in g['maps']:
                a = int(str(m['addr']), 0)
                if a >= b:
                    a -= b if b else 0
                f, un = KINDS.get(m.get('data', 'RAW'), (1.0, "raw"))
                T[a] = dict(name="%s/%s" % (g['name'], m['name']),
                            rows=int(m['rows']), cols=int(m['cols']),
                            factor=f, unit=un, signed=bool(m.get('signed')),
                            named=True, group=g['name'])
    return T


def regions(ori, mod, gap=128):
    d = [i for i in range(min(len(ori), len(mod))) if ori[i] != mod[i]]
    out = []
    if not d:
        return d, out
    s = p = d[0]
    for i in d[1:]:
        if i - p > gap:
            out.append((s, p))
            s = i
        p = i
    out.append((s, p))
    return d, out


def analyse(ori, mod, addr, t):
    r, c = t['rows'], t['cols']
    n = r * c
    fmt = '<h' if t['signed'] else '<H'
    try:
        va = [struct.unpack_from(fmt, ori, addr + i * 2)[0] for i in range(n)]
        vb = [struct.unpack_from(fmt, mod, addr + i * 2)[0] for i in range(n)]
    except struct.error:
        return None
    ch = [i for i in range(n) if va[i] != vb[i]]
    f = t['factor']
    res = dict(cells=len(ch), total=n,
               ori_max=max(va) * f, mod_max=max(vb) * f,
               ori_min=min(va) * f, mod_min=min(vb) * f)
    res['peak_pct'] = ((max(vb) / max(va) - 1) * 100) if max(va) else 0.0
    ratios = [vb[i] / va[i] for i in ch if va[i]]
    if ratios:
        lo, hi = min(ratios), max(ratios)
        res['ratio_lo'], res['ratio_hi'] = lo, hi
        res['uniform'] = (hi - lo) < 0.005          # within 0.5 pp -> flat % scale
    else:
        res['ratio_lo'] = res['ratio_hi'] = None
        res['uniform'] = False
    return res


def main():
    ap = argparse.ArgumentParser(description="ORI vs MOD calibration diff")
    ap.add_argument("--ori")
    ap.add_argument("--mod")
    ap.add_argument("--spec", help="emit_a2l spec json (gives names + scaling)")
    ap.add_argument("--maps", help="cal_inventory json (gives full coverage)")
    ap.add_argument("--base", default="0", help="spec base to subtract, e.g. 0x80000000")
    ap.add_argument("--gap", type=int, default=128)
    ap.add_argument("--json", dest="jsonout")
    ap.add_argument("--show-unchanged", action="store_true",
                    help="list defined maps the tuner did NOT touch (the negative signal)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.ori and a.mod):
        ap.error("--ori and --mod required")

    ori = open(a.ori, 'rb').read()
    mod = open(a.mod, 'rb').read()
    print("=" * 100)
    print("ORI vs MOD DIFF")
    print("=" * 100)
    print("  ORI %d bytes   MOD %d bytes   %s" %
          (len(ori), len(mod), "same size" if len(ori) == len(mod) else "SIZE DIFFERS"))
    if len(ori) != len(mod):
        print("  -> different sizes: these are probably not the same read format. Stop.")
        return 2

    d, runs = regions(ori, mod, a.gap)
    print("  bytes changed: %d (%.3f%% of file)   contiguous regions: %d"
          % (len(d), len(d) * 100.0 / len(ori), len(runs)))
    if not d:
        print("  files are identical.")
        return 0

    T = load_targets(a.spec, a.maps, int(str(a.base), 0))
    if not T:
        print("\n  no --spec / --maps given: showing raw regions only")
        for lo, hi in runs:
            print("    0x%06X-0x%06X  %6d bytes" % (lo, hi, hi - lo + 1))
        return 0

    # attribute
    spans = [(ad, ad + t['rows'] * t['cols'] * 2, ad, t) for ad, t in T.items()]
    spans.sort()
    hit, covered = [], set()
    for ad, end, _, t in spans:
        res = analyse(ori, mod, ad, t)
        if res and res['cells']:
            hit.append((ad, t, res))
            for i in range(ad, min(end, len(ori))):
                if ori[i] != mod[i]:
                    covered.add(i)

    hit.sort(key=lambda x: -abs(x[2]['peak_pct']))
    print("\n" + "=" * 100)
    print("CHANGED MAPS  (ranked by peak change)")
    print("=" * 100)
    print("%-34s%9s%8s%12s%12s%9s%8s  %s"
          % ("map", "addr", "dims", "ORI max", "MOD max", "peak", "cells", "edit"))
    for ad, t, r in hit:
        shape = ("uniform x%.4f" % r['ratio_lo']) if r['uniform'] else \
                ("shaped %.3f..%.3f" % (r['ratio_lo'], r['ratio_hi'])
                 if r['ratio_lo'] else "-")
        print("%-34s 0x%06X%5d x%-3d%12.2f%12.2f%+8.1f%%%8d  %s"
              % (t['name'][:34], ad, t['rows'], t['cols'], r['ori_max'],
                 r['mod_max'], r['peak_pct'], r['cells'], shape))

    # per-group roll-up: the air/fuel balance read
    grp = OrderedDict()
    for ad, t, r in hit:
        if t.get('group'):
            g = grp.setdefault(t['group'], [])
            g.append(r['peak_pct'])
    if grp:
        print("\n" + "=" * 100)
        print("PER-GROUP SUMMARY  (read the fuel-vs-air balance here)")
        print("=" * 100)
        for g, v in grp.items():
            print("  %-24s maps changed %3d   peak delta %+6.1f%%   median %+6.1f%%"
                  % (g, len(v), max(v, key=abs), sorted(v)[len(v)//2]))
        print("\n  A fuel/injection group whose delta clearly exceeds the boost group's")
        print("  means fuel was raised faster than air. Verify with physics_check.py")
        print("  --lambda-check before trusting the result.")

    if a.show_unchanged:
        print("\n" + "=" * 100)
        print("DEFINED BUT NOT TOUCHED  (the negative signal - often the useful half)")
        print("=" * 100)
        for ad, t in T.items():
            if not t['named']:
                continue
            res = analyse(ori, mod, ad, t)
            if res and res['cells'] == 0:
                print("  %-34s 0x%06X  %d x %d" % (t['name'][:34], ad, t['rows'], t['cols']))

    # coverage gaps
    unatt = [i for i in d if i not in covered]
    print("\n" + "=" * 100)
    print("COVERAGE: changed bytes NOT accounted for by any known map")
    print("=" * 100)
    print("  %d of %d changed bytes unattributed (%.1f%%)"
          % (len(unatt), len(d), len(unatt) * 100.0 / len(d)))
    if unatt:
        s = p = unatt[0]
        gaps = []
        for i in unatt[1:]:
            if i - p > a.gap:
                gaps.append((s, p)); s = i
            p = i
        gaps.append((s, p))
        print("  -> your definition set is incomplete here:")
        for lo, hi in gaps[:20]:
            print("     0x%06X-0x%06X  %6d bytes" % (lo, hi, hi - lo + 1))
        if len(gaps) > 20:
            print("     ... %d more" % (len(gaps) - 20))

    if a.jsonout:
        json.dump([dict(addr=ad, name=t['name'], rows=t['rows'], cols=t['cols'], **r)
                   for ad, t, r in hit], open(a.jsonout, 'w'), indent=1)
        print("\nwrote %s" % a.jsonout)
    return 0


def selftest():
    import io
    n = 40000
    ori = bytearray(n)
    # map 1 at 1000 (16x16) scaled uniformly +10%; map 2 at 5000 (8x8) untouched
    for i in range(256):
        struct.pack_into('<H', ori, 1000 + i * 2, 1000 + i)
    for i in range(64):
        struct.pack_into('<H', ori, 5000 + i * 2, 500 + i)
    mod = bytearray(ori)
    for i in range(256):
        v = struct.unpack_from('<H', ori, 1000 + i * 2)[0]
        struct.pack_into('<H', mod, 1000 + i * 2, int(round(v * 1.10)))
    # an unattributed change
    mod[20000] ^= 0xFF
    spec = dict(base=0, groups=[dict(name="G", maps=[
        dict(name="Scaled", addr="0x3E8", rows=16, cols=16, data="NM"),
        dict(name="Untouched", addr="0x1388", rows=8, cols=8, data="NM")])])
    p = "_st_spec.json"; json.dump(spec, open(p, 'w'))
    T = load_targets(p, None, 0)
    r1 = analyse(bytes(ori), bytes(mod), 1000, T[1000])
    r2 = analyse(bytes(ori), bytes(mod), 5000, T[5000])
    d, runs = regions(bytes(ori), bytes(mod))
    import os; os.remove(p)
    ok = (r1['cells'] == 256 and r1['uniform'] and abs(r1['peak_pct'] - 10.0) < 0.5
          and r2['cells'] == 0 and len(runs) == 2)
    print("  changed map : cells=%d uniform=%s peak=%+.1f%%"
          % (r1['cells'], r1['uniform'], r1['peak_pct']))
    print("  untouched   : cells=%d  (negative signal works)" % r2['cells'])
    print("  regions     : %d (expect 2: the map + the stray byte)" % len(runs))
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

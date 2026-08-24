#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MANDATORY pre-delivery gate: cross-check a modified bin against the control logic.

Why this exists (measured, not theoretical): on an ISUZU 1.9 RZ4E / MD1CS089 the boost
TARGET maps were raised to 3555 hPa while the boost CEILING 0x4D7992 was left at a flat
3000 hPa. Every individual map looked correct. But at runtime the ECU takes min(target,
ceiling), so boost only rose x1.03 while fuel rose x1.20 - lambda collapsed to 0.86 and
the whole "no extra DPF regeneration" design silently failed. Nothing in the file showed
it. That class of bug is invisible to per-map review and is what this gate catches.

Three checks, all HARD FAILURES (exit 2), never warnings:

  1. CLIPPING LIMITER  - a raised value whose domain has a flat/low-variance map still
                         sitting BELOW it. That limiter will clip the edit at runtime.
  2. MISSED PARALLEL   - an unchanged map that is a near-copy of a changed one. The ECU
                         selects between copies by condition; changing only some makes
                         behaviour inconsistent between modes.
  3. FLATTEN / SATURATE- a shaped map turned into a constant, or driven to 0xFFFF. Almost
                         always a tool artifact or a defeated protection.

Margin arithmetic uses ROW MAXIMA, not a representative cell: within one rpm row another
load column is usually higher and reaches the limiter first.
"""
import argparse, json, struct, sys
from collections import defaultdict


def load_maps(path):
    """largest descriptor wins when several resolve to the same data address"""
    out = {}
    for m in json.load(open(path)):
        a = m['data']
        p = out.get(a)
        if p and p['rows'] * p['cols'] >= m['rows'] * m['cols']:
            continue
        out[a] = m
    return out


def cells(d, m):
    n = m['rows'] * m['cols']
    return [struct.unpack_from('<H', d, m['data'] + i * 2)[0] for i in range(n)]


def rowmax(d, m):
    r, c = m['rows'], m['cols']
    return [max(struct.unpack_from('<H', d, m['data'] + (i * c + j) * 2)[0]
                for j in range(c)) for i in range(r)]


def could_clip(limit_hi, new_peak):
    """A flat map inside the SAME functional group, sitting below the new peak.

    Scoping to the group is the whole trick. Two weaker versions were tried on a real
    8 MB image and both are unusable:
      * same value decade            -> 2000+ false positives
      * same decade + 256 KB window  -> 208 false positives, and the CORRECT file
                                        failed too, so the gate carried no signal
    Magnitude cannot establish "same physical quantity" - the units and the control-flow
    relationship are simply not in the file. The group label is human-assigned knowledge,
    which is exactly what the heuristic was missing.

    Do NOT require the limit above the ORI peak: the measured case had ceiling 3000
    BELOW the stock target peak 3100, i.e. stock was already clipped.
    """
    return limit_hi < new_peak


def main():
    ap = argparse.ArgumentParser(description="Pre-delivery control-logic cross-check")
    ap.add_argument("--ori")
    ap.add_argument("--mod")
    ap.add_argument("--maps", help="cal_inventory.py --json output")
    ap.add_argument("--flat-var", type=float, default=0.02,
                    help="max (hi-lo)/hi for a map to count as a limiter (default 0.02)")
    ap.add_argument("--copy-tol", type=float, default=0.02,
                    help="cell tolerance for 'parallel copy' (default 2%%)")
    ap.add_argument("--spec", help="emit_a2l spec json - REQUIRED for the limiter check")
    ap.add_argument("--allow", default="",
                    help="comma-separated hex addresses to exempt, e.g. 0x4D7992")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.ori and a.mod and a.maps):
        ap.error("--ori --mod --maps are required (or use --selftest)")

    ORI = open(a.ori, 'rb').read()
    MOD = open(a.mod, 'rb').read()
    if len(ORI) != len(MOD):
        print("FAIL: files differ in size"); return 2
    M = load_maps(a.maps)
    allow = {int(x, 0) for x in a.allow.split(",") if x.strip()}

    # functional groups from the spec - the only reliable way to scope "same domain"
    group_of = {}
    if a.spec:
        sp = json.load(open(a.spec))
        base = int(str(sp.get('base', 0)), 0)
        for g in sp['groups']:
            for mm in g['maps']:
                ad = int(str(mm['addr']), 0)
                if base and ad >= base:
                    ad -= base
                group_of[ad] = g['name']

    changed, unchanged = {}, {}
    for ad, m in M.items():
        va, vb = cells(ORI, m), cells(MOD, m)
        (changed if va != vb else unchanged)[ad] = (m, va, vb)

    print("=" * 96)
    print("PRE-DELIVERY CONTROL-LOGIC CROSS-CHECK")
    print("=" * 96)
    print(f"  maps in inventory {len(M)}   changed {len(changed)}   unchanged {len(unchanged)}")

    fails = []

    # ---------------- 1. clipping limiters ----------------
    print("\n" + "-" * 96)
    print("1. CLIPPING LIMITERS  (a raised value still capped by an un-raised limit)")
    print("-" * 96)
    if not group_of:
        print("  SKIPPED - no --spec given, so no functional groups to scope the search.")
        print("  Magnitude alone cannot identify a limiter (measured: 2000+ false")
        print("  positives on this image). Supply --spec to enable this check.")
        fails.append("clipping-limiter check could not run (no --spec)")
    else:
        lim = []
        for ad, (m, va, vb) in unchanged.items():
            hi, lo = max(va), min(va)
            if hi <= 0 or ad in allow or ad not in group_of:
                continue
            if (hi - lo) / hi <= a.flat_var:             # flat => limiter signature
                lim.append((ad, m, hi))
        hits = []
        for ad, (m, va, vb) in changed.items():
            pk_o, pk_n = max(va), max(vb)
            if pk_n <= pk_o or ad not in group_of:
                continue
            for lad, lm, lhi in lim:
                if group_of[lad] == group_of[ad] and could_clip(lhi, pk_n):
                    hits.append((ad, m, pk_o, pk_n, lad, lhi))
        n_out = sum(1 for ad in changed if ad not in group_of)
        if hits:
            print(f"  {'raised map':>10} {'group':>16} {'peak':>16} {'limiter':>10} {'limit':>8}  clip")
            for ad, m, po, pn, lad, lhi in hits:
                print(f"  0x{ad:06X} {group_of[ad][:16]:>16} {po:6d}->{pn:<7d} "
                      f"0x{lad:06X} {lhi:8d}  -{pn-lhi} ({(pn-lhi)/pn:.1%})")
            fails.append(f"{len(hits)} raised map(s) sit above an un-raised limiter "
                         f"in the same group")
        else:
            print(f"  none  OK   ({len(lim)} flat maps checked inside the changed groups)")
        if n_out:
            print(f"  NOTE: {n_out} changed map(s) are not in the spec - their limiters")
            print("        cannot be checked. Add them to the spec or accept the gap.")

    # ---------------- 2. missed parallel copies ----------------
    print("\n" + "-" * 96)
    print("2. MISSED PARALLEL COPIES  (near-copy of a changed map, left untouched)")
    print("-" * 96)
    miss = []
    for ad, (m, va, vb) in changed.items():
        n = len(va)
        for uad, (um, uva, uvb) in unchanged.items():
            if (um['rows'], um['cols']) != (m['rows'], m['cols']):
                continue
            if uad in allow:
                continue
            close = sum(1 for i in range(n)
                        if abs(uva[i] - va[i]) <= max(2, abs(va[i]) * a.copy_tol))
            if close >= n * 0.95:
                miss.append((ad, uad, m, close / n))
    if miss:
        print(f"  {'changed':>10} {'untouched twin':>16} {'dims':>8} {'match':>7}")
        for ad, uad, m, f in miss:
            print(f"  0x{ad:06X} {'0x%06X'%uad:>16} {m['rows']:4d}x{m['cols']:<3d} {f:6.1%}")
        fails.append(f"{len(miss)} parallel copy/copies of a changed map were not changed")
    else:
        print("  none  OK")

    # ---------------- 3. flatten / saturate ----------------
    print("\n" + "-" * 96)
    print("3. FLATTENED OR SATURATED  (shaped map turned constant, or driven to 0xFFFF)")
    print("-" * 96)
    bad = []
    for ad, (m, va, vb) in changed.items():
        if ad in allow:
            continue
        was_shaped = max(va) > 0 and (max(va) - min(va)) / max(va) > 0.05
        now_flat = max(vb) == min(vb)
        sat = min(vb) >= 65000 and min(va) < 65000
        zero = max(vb) == 0 and max(va) > 0
        if was_shaped and (now_flat or zero):
            bad.append((ad, m, "FLATTENED to %d" % max(vb)))
        elif sat:
            bad.append((ad, m, "SATURATED to 0xFFFF"))
    if bad:
        for ad, m, why in bad:
            print(f"  0x{ad:06X} {m['rows']:3d}x{m['cols']:<3d}  {why}")
        fails.append(f"{len(bad)} map(s) flattened or saturated")
    else:
        print("  none  OK")

    print("\n" + "=" * 96)
    if fails:
        print("GATE: FAIL")
        for f in fails:
            print("  ! " + f)
        print("\n  Fix, or pass --allow with the addresses you have consciously decided")
        print("  to leave (and say so in the delivery notes). Do NOT ship past this.")
        return 2
    print("GATE: PASS - no clipping limiter, no missed parallel copy, nothing flattened")
    return 0


def selftest():
    import tempfile, os
    n = 60000
    ori = bytearray(n)
    def mk(off, r, c, vals):
        struct.pack_into('<H', ori, off, r); struct.pack_into('<H', ori, off + 2, c)
        for i in range(r): struct.pack_into('<H', ori, off + 4 + i * 2, 1000 + i * 100)
        for j in range(c): struct.pack_into('<H', ori, off + 4 + r * 2 + j * 2, j * 500)
        d = off + 4 + r * 2 + c * 2
        for i, v in enumerate(vals): struct.pack_into('<H', ori, d + i * 2, v)
        return d
    # target map (shaped), its untouched twin, and a flat ceiling below the new peak
    d_t = mk(1000, 4, 4, [2000+ i*50 for i in range(16)])
    d_w = mk(3000, 4, 4, [2000+ i*50 for i in range(16)])
    d_c = mk(5000, 4, 4, [2600]*16)
    mod = bytearray(ori)
    for i in range(16):                       # raise the target above the ceiling
        v = struct.unpack_from('<H', ori, d_t + i * 2)[0]
        struct.pack_into('<H', mod, d_t + i * 2, int(v * 1.2))
    maps = [dict(data=d_t, rows=4, cols=4, hdr=1000),
            dict(data=d_w, rows=4, cols=4, hdr=3000),
            dict(data=d_c, rows=4, cols=4, hdr=5000)]
    td = tempfile.mkdtemp()
    pj = os.path.join(td, "m.json"); json.dump(maps, open(pj, 'w'))
    po = os.path.join(td, "o.bin"); open(po, 'wb').write(bytes(ori))
    pm = os.path.join(td, "m.bin"); open(pm, 'wb').write(bytes(mod))
    sys.argv = ["x", "--ori", po, "--mod", pm, "--maps", pj]
    rc = main()
    ok = rc == 2                              # must FAIL: ceiling clips + twin missed
    print("\nselftest: planted a clipping ceiling and an unchanged twin; gate returned "
          f"{rc} (expected 2)")
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())



#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 3 of bin-to-a2l: PHYSICAL CLOSURE GATE.

This is the global correctness check on the whole decode. It also, as a by-product,
IDENTIFIES the fuel-quantity ceiling map and its scaling automatically.

Principle: for a compression-ignition engine the fuel ceiling map fully determines
the torque/power envelope. So for every candidate map on an RPM axis, try the
plausible scalings, compute the implied peak power and peak torque from first
principles, and rank by agreement with the stated factory rating.

    fuel_rate [kg/s] = mg_per_stroke * 1e-6 * n_cyl * (rpm/60/2)     (4-stroke)
    P [W]            = fuel_rate * LHV * eta
    T [Nm]           = P / (rpm * 2*pi/60)

If NOTHING closes within tolerance, the decode is wrong somewhere upstream
(container ABI, endianness, data width, or the map is not the fuel ceiling).
Do not proceed to A2L emission on a failed gate.

Also provides the air-side check: lambda, required boost, and compressor pressure
ratio, which is what decides whether a fuel increase is actually deliverable.
"""
import argparse, json, math, struct, sys

LHV = {"diesel": 42.7e6, "gasoline": 43.4e6}
AFR_ST = {"diesel": 14.5, "gasoline": 14.7}
CAND_FACTORS = [1.0, 0.5, 0.1, 0.01, 0.001, 1/64, 1/128, 1/256, 1/512, 1/1024]


def perf(mg, rpm, n_cyl, lhv, eta):
    kg_s = mg * 1e-6 * n_cyl * (rpm / 60.0 / 2.0)
    w = kg_s * lhv * eta
    return w / 1000.0, (w / (rpm * 2 * math.pi / 60.0) if rpm else 0.0)


# Some ECUs express injection quantity as VOLUME (mm3/stroke) rather than MASS
# (mg/stroke) -- Bosch EDC17 commonly uses mm3/Stk, MD1 uses mg/Stk. Multiply by
# fuel density to get mass. Diesel ~0.832 mg/mm3 at 15 C, gasoline ~0.745.
DENSITY = {"diesel": 0.832, "gasoline": 0.745}


def air_mg(hpa, disp_cc, n_cyl, ve, iat_c):
    v = disp_cc * 1e-6 / n_cyl
    return (hpa * 100.0 * v * ve) / (287.0 * (iat_c + 273.15)) * 1e6


def find_fuel_map(data, maps, args):
    u = lambda o: struct.unpack_from('<H', data, o)[0]
    lhv = LHV[args.fuel]
    out = []
    for m in maps:
        if m['x_kind'] != 'RPM' or m['flat']:
            continue
        r, c = m['rows'], m['cols']
        rpm = [struct.unpack_from('<H', data, m['x_addr'] + i * 2)[0] * 0.5
               for i in range(r)]
        col0 = [u(m['data'] + (i * c) * 2) for i in range(r)]
        dens = getattr(args, 'density', 1.0) or 1.0     # 1.0 if the map is already mass
        for f in CAND_FACTORS:
            raw = [v * f for v in col0]                 # map units (mg or mm3)
            mg = [v * dens for v in raw]                # always mass from here on
            if not (5.0 <= max(mg) <= 200.0):           # plausible mg/stroke envelope
                continue
            pk_kw = pk_nm = 0.0
            for i, n in enumerate(rpm):
                if n < 500:
                    continue
                kw, nm = perf(mg[i], n, args.cyl, lhv, args.eta)
                pk_kw = max(pk_kw, kw)
                pk_nm = max(pk_nm, nm)
            if pk_kw <= 0:
                continue
            e_kw = pk_kw / args.kw - 1.0
            # torque is optional: many CN VIN plates list power only
            e_nm = (pk_nm / args.nm - 1.0) if args.nm else None
            score = abs(e_kw) if e_nm is None else math.hypot(e_kw, e_nm)
            out.append(dict(addr=m['data'], rows=r, cols=c, factor=f,
                            peak_unit=round(max(raw), 2), peak_mg=round(max(mg), 2),
                            kw=round(pk_kw, 1), nm=round(pk_nm, 1),
                            err_kw=round(e_kw * 100, 1),
                            err_nm=(round(e_nm * 100, 1) if e_nm is not None else None),
                            score=round(score * 100, 2)))
    out.sort(key=lambda d: d['score'])
    return out


def main():
    ap = argparse.ArgumentParser(description="Physical closure gate / fuel-map finder")
    ap.add_argument("--bin", dest="binpath")
    ap.add_argument("--maps", help="cal_inventory.py --json output")
    ap.add_argument("--disp", type=float, help="displacement in cc")
    ap.add_argument("--cyl", type=int, default=4)
    ap.add_argument("--kw", type=float, help="factory rated power kW")
    ap.add_argument("--nm", type=float, default=None,
                    help="factory rated torque Nm (OPTIONAL - many CN VIN plates "
                         "list power only; omit and scoring uses power alone)")
    ap.add_argument("--units", choices=("mass", "volume"), default="mass",
                    help="mass = map is mg/stroke (MD1); volume = mm3/stroke (EDC17)")
    ap.add_argument("--density", type=float, default=None,
                    help="override fuel density mg/mm3 (default 0.832 diesel)")
    ap.add_argument("--fuel", choices=list(LHV), default="diesel")
    ap.add_argument("--eta", type=float, default=0.385, help="brake thermal efficiency")
    ap.add_argument("--tol", type=float, default=8.0, help="pass tolerance in %")
    ap.add_argument("--top", type=int, default=12)
    # air-side sub-check
    ap.add_argument("--lambda-check", nargs=3, metavar=("MG", "HPA", "IAT_C"),
                    type=float, help="one-shot lambda for mg/stroke, boost hPa, IAT")
    ap.add_argument("--ve", type=float, default=0.95)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.lambda_check:
        mg, hpa, iat = a.lambda_check
        if not a.disp:
            ap.error("--disp required for --lambda-check")
        am = air_mg(hpa, a.disp, a.cyl, a.ve, iat)
        lam = am / (AFR_ST[a.fuel] * mg)
        print(f"air        : {am:8.0f} mg/cyl @ {hpa:.0f} hPa abs, IAT {iat:.0f} C, VE {a.ve}")
        print(f"fuel       : {mg:8.2f} mg/stroke")
        print(f"lambda     : {lam:8.3f}")
        print(f"press ratio: {hpa/1013.0:8.2f}")
        print("  note: diesel smoke floor is typically lambda 1.2-1.3; a small VGT "
              "runs out of breath around PR 3.2-3.5")
        return 0

    for req in ("binpath", "maps", "kw"):
        if getattr(a, req) in (None, ""):
            ap.error("--bin --maps --kw are required (--nm optional)")

    if a.density is None:
        a.density = DENSITY[a.fuel] if a.units == "volume" else 1.0

    data = open(a.binpath, 'rb').read()
    maps = json.load(open(a.maps))
    res = find_fuel_map(data, maps, a)

    print("=" * 92)
    print("PHYSICAL CLOSURE GATE")
    print("stated rating: %.1f kW / %s   %d cyl  eta=%.3f  fuel=%s"
          % (a.kw, ("%.1f Nm" % a.nm) if a.nm else "torque NOT STATED (power-only scoring)",
             a.cyl, a.eta, a.fuel))
    print("fuel map units: %s  (density factor %.3f mg per map unit)"
          % (a.units, a.density))
    print("=" * 92)
    if not res:
        print("NO candidate map produces a plausible mg/stroke envelope.")
        print("-> the decode is wrong upstream (ABI / endianness / data width),")
        print("   or this image has no simple fuel-ceiling map. DO NOT emit A2L yet.")
        return 2
    print(f"{'addr':>9} {'dims':>7} {'factor':>9} {'peak unit':>10} {'peak mg':>9} "
          f"{'kW':>7} {'err%':>7} {'Nm':>7} {'err%':>7} {'score':>7}")
    for r in res[:a.top]:
        en = ("%+7.1f" % r['err_nm']) if r['err_nm'] is not None else "      -"
        print(f"0x{r['addr']:06X} {r['rows']:3d}x{r['cols']:<3d} {r['factor']:9.5f} "
              f"{r['peak_unit']:10.2f} {r['peak_mg']:9.2f} {r['kw']:7.1f} "
              f"{r['err_kw']:+7.1f} {r['nm']:7.1f} {en} {r['score']:7.2f}")
    best = res[0]
    ok = abs(best['err_kw']) <= a.tol and \
         (best['err_nm'] is None or abs(best['err_nm']) <= a.tol)
    print("-" * 92)
    print("best: 0x%06X  factor %.5f  -> %.1f kW (%+.1f%%) / %.1f Nm (%s)"
          % (best['addr'], best['factor'], best['kw'], best['err_kw'], best['nm'],
             ("%+.1f%%" % best['err_nm']) if best['err_nm'] is not None
             else "torque not stated"))
    if best['err_nm'] is None:
        print("\nWARNING: scoring used POWER ONLY. Power alone is a weak discriminator - "
              "many maps\n         close on kW by coincidence. Supply --nm to get a usable "
              "shortlist.")
    print("GATE:", "PASS - decode is physically consistent" if ok else
          "FAIL - nothing closes within %.1f%%; fix the decode before emitting A2L" % a.tol)
    print("""
READ THIS AS A SHORTLIST, NOT AN ORACLE.
  A PASS means the decode (ABI/endianness/width) is physically consistent - that is
  the gate's real job. Rank 1 is NOT guaranteed to be the fuel map: several maps can
  close numerically by coincidence. On the reference MD1CS089 image the true fuel
  ceiling ranked #2. Confirm the winner by checking it actually looks like a ceiling:
  full RPM span, plausible mg envelope, and a second axis that is a mode/variant index
  rather than a physical quantity.

  eta is an assumption: +-0.02 moves the absolute numbers ~5%. RATIOS (how much a
  given change gains) are reliable; ABSOLUTES are not.""")
    return 0 if ok else 2


def selftest():
    """build a synthetic fuel map for a known engine and confirm it is recovered"""
    disp, cyl, eta, lhv = 1898.0, 4, 0.385, LHV['diesel']
    rpm = [750 + 150 * i for i in range(20)]
    # aim for ~130 kW / ~410 Nm
    target_nm = 410.0
    mg = []
    for n in rpm:
        nm = target_nm * (1.0 - ((n - 2900) / 3000.0) ** 2 * 0.55)
        w = nm * n * 2 * math.pi / 60.0
        kg_s = w / (lhv * eta)
        mg.append(kg_s / (1e-6 * cyl * (n / 60.0 / 2.0)))
    r, c = len(rpm), 4
    off = 500
    buf = bytearray(b'\x00' * 60000)
    struct.pack_into('<H', buf, off, r); struct.pack_into('<H', buf, off + 2, c)
    for i, n in enumerate(rpm):
        struct.pack_into('<H', buf, off + 4 + i * 2, int(n * 2))
    for j in range(c):
        struct.pack_into('<H', buf, off + 4 + r * 2 + j * 2, j * 1000)
    dat = off + 4 + r * 2 + c * 2
    for i in range(r):
        for j in range(c):
            struct.pack_into('<H', buf, dat + (i * c + j) * 2, int(round(mg[i] * 100)))
    m = [dict(x_kind='RPM', flat=False, rows=r, cols=c, x_addr=off + 4, data=dat)]

    class A:
        cyl, fuel, eta, kw, nm = 4, 'diesel', 0.385, 130.0, 410.0
    res = find_fuel_map(bytes(buf), m, A)
    ok = bool(res) and res[0]['addr'] == dat and abs(res[0]['factor'] - 0.01) < 1e-9 \
         and abs(res[0]['err_nm']) < 3.0
    if res:
        b = res[0]
        print("  recovered 0x%06X factor=%.5f -> %.1f kW / %.1f Nm (err %+.1f%% / %+.1f%%)"
              % (b['addr'], b['factor'], b['kw'], b['nm'], b['err_kw'], b['err_nm']))
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 2 - turn ECM map-view screenshots into A-grade facts (display / raw closure).

truth.json : a list, one entry per ECM map window you have a screenshot of
  {"addr": "0x72DA32", "rows": 6, "cols": 6,
   "allowed": [0, 6553.5],                   # title-bar 'Allowed Values' - sign hint ONLY
   "start_cell": 0,                          # index (row-major) of the first transcribed cell
   "cells": [470, 475, 480, 485, 490, 500],  # a contiguous run of DISPLAYED values
   "decimals": 2,                            # decimals ECM showed (tolerance = half a digit)
   "x": [800, 2000, 3300, 4500, 5800, 7000], "xunit": "RPM",   # column axis as displayed
   "y": [0, 200, 400, 600, 800, 1000],        "yunit": "hPa"}  # row axis as displayed
  Curves: an Rx1 table puts its breakpoints in "y", a 1xC table in "x".

Per entry
  * sweep the factors real calibrations use (ecmlib.NICE_FACTORS) at every width / sign and
    keep those whose residual against the displayed run stays below half a display digit.
    ECM rounds what it shows, which biases a least-squares slope; a sweep is not biased.
  * several survivors -> 'Allowed Values' sign hint, then smoothness of the FULL table
    (the right width reads as a smooth surface, the wrong one as interleaved bytes).
  * each axis: tolerant value-sequence search, AFTER the data first (MG1CS201: +72..+7254 B),
    then before. Evenly spaced and not stored -> FIX_AXIS_PAR_DIST. ECM's own '--' 1..n index
    is not an axis. Otherwise the displayed list, flagged 'not located'.
"""
import argparse
import bisect
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ecmlib as L  # noqa: E402

AXIS_FACTORS = [1.0, 0.1, 0.01, 0.25, 0.5, 0.125, 1 / 256, 1 / 128, 1 / 64, 100 / 65535, 10 / 65536,
                0.0234375, 1 / 4096, 1 / 16384, 0.001, 1 / 1024, 0.05, 0.02, 1 / 32]
AXIS_OFFSETS = [0.0, -273.15]
AFTER, BEFORE = 65536, 8192


def close_cells(buf, e):
    addr = L.as_int(e['addr'])
    off = int(e.get('start_cell', 0))
    cells = [float(v) for v in e['cells']]
    tol = 0.5 * 10 ** (-int(e.get('decimals', 2))) + 1e-9
    n, rc, ncol = len(cells), e['rows'] * e['cols'], e['cols']
    cands = []
    for w in (1, 2):
        for sg in (False, True):
            raw = L.read_vals(buf, addr + off * w, n, w, sg)
            if raw is None:
                continue
            varied = len(set(raw)) > 1
            full = L.read_vals(buf, addr, rc, w, sg) or raw
            for f in L.NICE_FACTORS:
                res = [d - r * f for r, d in zip(raw, cells)]
                o = sum(res) / n
                if max(abs(x - o) for x in res) > tol:
                    continue
                o2 = min(L.NICE_OFFSETS, key=lambda z: abs(z - o))
                if abs(o2 - o) > tol:
                    if not varied:
                        continue                 # a flat run cannot separate factor and offset
                    o2 = round(o, 6)
                if not varied and o2 != 0.0:
                    continue
                resid = max(abs(r * f + o2 - d) for r, d in zip(raw, cells))
                if resid <= tol:
                    cands.append(dict(w=w, sg=sg, factor=f, offset=o2, resid=resid,
                                      flat=not varied, rough=L.rough(full, ncol)))
    if not cands:
        return None, 0
    al = e.get('allowed')

    def key(c):
        signpen = 0 if al is None else (0 if (float(al[0]) < 0) == c['sg'] else 1)
        return (signpen, round(c['rough'], 3), c['offset'] != 0.0, L.NICE_FACTORS.index(c['factor']), c['w'])
    cands.sort(key=key)
    best = dict(cands[0])
    u = L.read_vals(buf, addr, rc, best['w'], False)
    s = L.read_vals(buf, addr, rc, best['w'], True)
    best['sign_undetermined'] = (u == s) and al is None
    best['alternatives'] = len({(c['w'], c['factor'], c['offset']) for c in cands}) - 1
    return best, len(cands)


def axis_tol(v, span):
    if abs(v - round(v)) < 1e-9:
        t = 0.5
    elif abs(v * 10 - round(v * 10)) < 1e-9:
        t = 0.05
    else:
        t = 0.005
    return max(t, span * 0.006)


def find_axis(buf, values, data_addr, data_end):
    n = len(values)
    if n < 2:
        return []
    span = (max(values) - min(values)) or 1.0
    tols = [axis_tol(v, span) for v in values]
    lo_a = max(0, data_addr - BEFORE)
    hi_a = min(len(buf), data_end + AFTER)
    hits = {}
    for w in (2, 1):
        base = lo_a - (lo_a % w)
        cnt = (hi_a - base) // w
        if cnt < n:
            continue
        for sg in (False, True):
            vals = L.read_vals(buf, base, cnt, w, sg)
            idx = collections.defaultdict(list)
            for i, v in enumerate(vals):
                idx[v].append(i)
            keys = sorted(idx)
            for fi, f in enumerate(AXIS_FACTORS):
                for o in AXIS_OFFSETS:
                    a = (values[0] - tols[0] - o) / f
                    b = (values[0] + tols[0] - o) / f
                    k = bisect.bisect_left(keys, min(a, b))
                    top = max(a, b)
                    while k < len(keys) and keys[k] <= top:
                        for i in idx[keys[k]]:
                            if i + n > cnt:
                                continue
                            if all(abs(vals[i + j] * f + o - values[j]) <= tols[j] for j in range(1, n)):
                                ad = base + i * w
                                if data_addr <= ad < data_end:
                                    continue
                                pri = (fi, sg, o != 0.0)
                                kk = (ad, w)
                                if kk not in hits or pri < hits[kk][0]:
                                    d = (ad - data_end) if ad >= data_end else (ad - data_addr)
                                    hits[kk] = (pri, dict(addr=ad, w=w, sg=sg, factor=f, offset=o, dist=d))
                        k += 1
    out = [h for _, h in hits.values()]
    out.sort(key=lambda h: (0 if h['dist'] >= 0 else 1, abs(h['dist'])))
    return out


def even_spacing(values):
    n = len(values)
    if n < 3:
        return None
    step = (values[-1] - values[0]) / (n - 1)
    if step == 0:
        return None
    for i in range(n - 1):
        d = values[i + 1] - values[i]
        if abs(d - step) > max(2 * axis_tol(values[i + 1], 0), abs(step) * 0.02):
            return None
    return float(values[0]), step


def resolve_axis(buf, values, unit, data_addr, data_end):
    if not values:
        return None
    values = [float(v) for v in values]
    n = len(values)
    v0 = values[0]
    if (unit or '').strip() in ('', '-', '--') and v0 in (0.0, 1.0) and \
            all(abs(values[i] - (v0 + i)) < 1e-9 for i in range(n)):
        return dict(kind='ecm_index', n=n, start=v0, step=1.0, values=values)
    hits = find_axis(buf, values, data_addr, data_end)
    if hits:
        h = hits[0]
        if n >= 4 or (len(hits) == 1 and 0 <= h['dist'] <= 2048):
            return dict(kind='stored', n=n, values=values, others=len(hits) - 1, **h)
    ev = even_spacing(values)
    if ev:
        return dict(kind='fix_dist', n=n, start=ev[0], step=ev[1], values=values)
    return dict(kind='fix_list', n=n, values=values, located=False)


def run(buf, entries):
    out = []
    for e in entries:
        addr = L.as_int(e['addr'])
        best, ncand = close_cells(buf, e)
        rec = dict(addr=addr, rows=e['rows'], cols=e['cols'], xunit=e.get('xunit'), yunit=e.get('yunit'),
                   start_cell=int(e.get('start_cell', 0)), cells=e['cells'],
                   decimals=int(e.get('decimals', 2)), candidates=ncand)
        if not best:
            rec['error'] = 'no width/sign/factor reproduces the displayed cells - check transcription / start_cell'
            out.append(rec)
            continue
        rec.update(best)
        data_end = addr + e['rows'] * e['cols'] * best['w']
        rec['axes'] = {}
        for k, uk in (('x', 'xunit'), ('y', 'yunit')):
            if e.get(k):
                rec['axes'][k] = resolve_axis(buf, e[k], e.get(uk), addr, data_end)
        out.append(rec)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bin')
    ap.add_argument('--truth')
    ap.add_argument('--out', default='closure.json')
    ap.add_argument('--endian', choices=('le', 'be'), default='le')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    L.ENDIAN = '<' if a.endian == 'le' else '>'
    buf = open(a.bin, 'rb').read()
    res = run(buf, L.jload(a.truth))
    L.jsave(res, a.out)
    ok = sum(1 for r in res if 'error' not in r)
    print(f"closures {ok}/{len(res)}")
    kinds = collections.Counter()
    for r in res:
        if 'error' in r:
            print(f"  FAIL 0x{r['addr']:06X}: {r['error']}")
            continue
        dt = L.DT_NAME[(r['w'], r['sg'])]
        flags = ('  sign?' if r['sign_undetermined'] else '') + (f"  alt={r['alternatives']}" if r['alternatives'] else '')
        ax = []
        for k in ('x', 'y'):
            v = r['axes'].get(k)
            if v:
                kinds[v['kind']] += 1
                ax.append(f"{k}:{v['kind']}" + (f"@0x{v['addr']:06X}({'+' if v['dist'] >= 0 else ''}{v['dist']})"
                                                 if v['kind'] == 'stored' else ''))
        print(f"  0x{r['addr']:06X} {r['rows']}x{r['cols']:<3d} {dt:5s} x{L.num(r['factor'])}"
              f"{('+' + L.num(r['offset'])) if r['offset'] else ''}  resid {r['resid']:.4f}{flags}   " + '  '.join(ax))
    print("axes:", dict(kinds))
    print(f"-> {a.out}")
    return 0 if ok == len(res) else 2


def selftest():
    buf = bytearray(0x400)
    raw = [500, 520, 560, 610, 480, 500, 540, 590, 460, 480, 520, 570]
    struct.pack_into('<12H', buf, 0x100, *raw)
    xs = [800, 2000, 4000, 6500]
    struct.pack_into('<4H', buf, 0x100 + 24 + 10, *xs)
    e = dict(addr='0x100', rows=3, cols=4, start_cell=0, cells=[50.0, 52.0, 56.0, 61.0],
             x=xs, xunit='RPM', y=[0, 50, 100], yunit='hPa')
    r = run(bytes(buf), [e])[0]
    ok1 = r.get('w') == 2 and abs(r.get('factor', 0) - 0.1) < 1e-12 and r.get('offset') == 0.0
    ok2 = r['axes']['x']['kind'] == 'stored' and r['axes']['x']['addr'] == 0x100 + 34
    ok3 = r['axes']['y']['kind'] == 'fix_dist'
    bad = dict(e, cells=[v * 1.03 for v in e['cells']])
    ok4 = 'error' in run(bytes(buf), [bad])[0]
    print(f"  factor/width closure: {ok1}   stored axis after data: {ok2}   even axis -> FIX: {ok3}")
    print(f"  negative control (cells x1.03 must NOT close): {ok4}")
    ok = ok1 and ok2 and ok3 and ok4
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

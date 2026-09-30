#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Can a real A2L of the same ECU family but ANOTHER dataset name the ECM-located maps?

For every ECM object that is a MAP or CURVE (1x1 values are far too common to vote with),
take every A2L CHARACTERISTIC with the same data width, the same shape (transpose allowed)
and the same factor, and vote on the address difference A2L - ECM. A dataset change that only
inserts or removes blocks yields a PIECEWISE-CONSTANT difference: neighbours agree on one value.

Two gates separate a real segment from coincidence (50,000 candidates make chance clusters of
3 appear everywhere):
  coherence   supporters / voting objects inside the ECM span they cover >= 0.5
  uniqueness  an object explained equally well by several differences proves nothing - a run
              of equally spaced small tables lets every shift by one stride 'match'. Such objects
              are dropped, and a cluster must keep >= min_support supporters without them.

Verdict   PORTABLE      real clusters cover >= 60 % of the voting objects
          LOCAL-ONLY    some real clusters - borrow names inside them only (--names-out)
          NOT PORTABLE  none
Measured: MG1CS201 R2B22002B A2L vs R4C9J7V5B bin -> LOCAL-ONLY; pedal maps KF_MDR_ABV_*,
          launch rpm KF_NMAX_ANFAHR_MODE_*, KF_MDIMAX_* land in real segments.
"""
import argparse
import collections
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ecmlib as L  # noqa: E402

TOK = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')
DW = {'UBYTE': 1, 'SBYTE': 1, 'UWORD': 2, 'SWORD': 2, 'ULONG': 4, 'SLONG': 4, 'FLOAT32_IEEE': 4}


def parse_a2l(path):
    txt = open(path, encoding='latin-1').read()

    def blocks(kind):
        for m in re.finditer(r'/begin\s+' + kind + r'\b(.*?)/end\s+' + kind + r'\b', txt, re.S):
            yield m.group(1)
    RL = {}
    for bl in blocks('RECORD_LAYOUT'):
        t = TOK.findall(bl)
        for i, k in enumerate(t):
            if k == 'FNC_VALUES':
                try:
                    RL[t[0]] = t[i + 2]
                except IndexError:
                    pass
    CM = {}
    for bl in blocks('COMPU_METHOD'):
        t = TOK.findall(bl)
        f = None
        if 'COEFFS_LINEAR' in t:
            f = float(t[t.index('COEFFS_LINEAR') + 1])
        elif 'COEFFS' in t:
            i = t.index('COEFFS')
            a, b, c, d, e, ff = [float(x) for x in t[i + 1:i + 7]]
            if a == 0 and d == 0 and e == 0 and b != 0:
                f = ff / b
        elif len(t) > 2 and t[2] == 'IDENTICAL':
            f = 1.0
        CM[t[0]] = f
    chars = []
    for bl in blocks('CHARACTERISTIC'):
        t = TOK.findall(bl)
        if len(t) < 9:
            continue
        try:
            addr = int(t[3], 16)
        except ValueError:
            continue
        axes = []
        for am in re.finditer(r'/begin\s+AXIS_DESCR(.*?)/end\s+AXIS_DESCR', bl, re.S):
            at = TOK.findall(am.group(1))
            try:
                axes.append(int(at[3]))
            except (IndexError, ValueError):
                pass
        typ = t[2]
        if typ == 'VALUE':
            dims = (1,)
        elif typ == 'CURVE' and axes:
            dims = (axes[0],)
        elif typ == 'MAP' and len(axes) >= 2:
            dims = (axes[0], axes[1])
        elif typ == 'VAL_BLK':
            md = re.search(r'\bMATRIX_DIM\s+(\d+)(?:\s+(\d+))?', bl)
            mn = re.search(r'\bNUMBER\s+(\d+)', bl)
            dims = tuple(int(x) for x in md.groups() if x and int(x) > 1) if md else \
                ((int(mn.group(1)),) if mn else None)
            if not dims:
                continue
        else:
            continue
        chars.append(dict(n=t[0], t=typ, a=addr, w=DW.get(RL.get(t[4])), dims=dims, f=CM.get(t[6])))
    return chars


def key(d):
    if len(d) == 1:
        return ('V',) if d[0] == 1 else ('C', d[0])
    x, y = sorted(d)
    return ('M', x, y)


def _cluster(diff, sup, voters):
    lo, hi = min(sup), max(sup)
    inside = sum(1 for v in voters if lo <= v <= hi)
    return (diff, len(sup), (len(sup) / inside) if inside else 0.0, lo, hi, sup)


def probe(chars, objs, min_support=3):
    idx = collections.defaultdict(list)
    for c in chars:
        if c['w']:
            idx[(c['w'], key(c['dims']))].append(c)
    voters = []
    fv = collections.defaultdict(dict)                  # diff -> {ecm addr: a2l char}
    for o in objs:
        if not o.get('cells') or o.get('grade') == 'GAP' or not o.get('w'):
            continue
        d = (o['r'] * o['c'],) if (o['r'] == 1 or o['c'] == 1) else (o['r'], o['c'])
        k = (o['w'], key(d))
        if k[1][0] == 'V':
            continue
        voters.append(o['addr'])
        for c in idx.get(k, []):
            if c['f'] and o['factor'] and abs(c['f'] / o['factor'] - 1) < 1e-6:
                fv[c['a'] - o['addr']][o['addr']] = c
    voters.sort()
    real = sorted((_cluster(d, s, voters) for d, s in fv.items() if len(s) >= min_support),
                  key=lambda r: (-r[1], -r[2]))
    coherent = [r for r in real if r[2] >= 0.5]
    # every object goes to its single best offset (support, then coherence). Only a TIE for the
    # best - a periodic run of equal small tables - makes it ambiguous; a shifted subset of a
    # longer run (support 4 vs 6) does not.
    cand = collections.defaultdict(list)
    for r in coherent:
        for ad in r[5]:
            cand[ad].append(((r[1], round(r[2], 6)), r[0]))
    ambiguous, owner = set(), {}
    for ad, lst in cand.items():
        lst.sort(reverse=True)
        if len(lst) > 1 and lst[0][0] == lst[1][0]:
            ambiguous.add(ad)
        else:
            owner[ad] = lst[0][1]
    good = []
    for r in coherent:
        s2 = {ad: c for ad, c in r[5].items() if owner.get(ad) == r[0]}
        if len(s2) >= min_support:
            r2 = _cluster(r[0], s2, voters)
            if r2[2] >= 0.5:
                good.append(r2)
    covered = set()
    for r in good:
        covered.update(r[5])
    cov = len(covered) / len(voters) if voters else 0.0
    verdict = 'PORTABLE' if cov >= 0.6 else ('LOCAL-ONLY' if good else 'NOT PORTABLE')
    return dict(verdict=verdict, coverage=cov, voters=len(voters), real=real, good=good, ambiguous=ambiguous)


def selftest():
    """Parser + the three gates on synthetic data, with the two negative controls that bit us."""
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    def obj(ad, r, c, w=2, f=0.1):
        return dict(addr=ad, r=r, c=c, cells=r * c, w=w, factor=f, grade='B')

    def ch(n, ad, dims, w=2, f=0.1):
        return dict(n=n, t='MAP' if len(dims) == 2 else 'CURVE', a=ad, w=w, dims=dims, f=f)

    def names(r):
        return {ad: c['n'] for g in r['good'] for ad, c in g[5].items()}

    # 1 parser: record-layout width, LINEAR and RAT_FUNC factors, MAP / CURVE / VALUE / VAL_BLK shapes
    txt = ('/begin RECORD_LAYOUT RL_W FNC_VALUES 1 UWORD ROW_DIR DIRECT /end RECORD_LAYOUT\n'
           '/begin RECORD_LAYOUT RL_B FNC_VALUES 1 SBYTE ROW_DIR DIRECT /end RECORD_LAYOUT\n'
           '/begin COMPU_METHOD CM_LIN "" LINEAR "%10.2" "Nm" COEFFS_LINEAR 0.1 0 /end COMPU_METHOD\n'
           '/begin COMPU_METHOD CM_RAT "" RAT_FUNC "%10.2" "deg" COEFFS 0 2 0 0 0 1 /end COMPU_METHOD\n'
           '/begin CHARACTERISTIC KF_T "torque" MAP 0x1000 RL_W 0 CM_LIN 0 6553.5\n'
           '  /begin AXIS_DESCR STD_AXIS NO_INPUT_QUANTITY CM_LIN 8 0 100 /end AXIS_DESCR\n'
           '  /begin AXIS_DESCR STD_AXIS NO_INPUT_QUANTITY CM_LIN 6 0 100 /end AXIS_DESCR\n'
           '/end CHARACTERISTIC\n'
           '/begin CHARACTERISTIC KL_Z "spark" CURVE 0x2000 RL_B 0 CM_RAT -64 63.5\n'
           '  /begin AXIS_DESCR STD_AXIS NO_INPUT_QUANTITY CM_LIN 12 0 100 /end AXIS_DESCR\n'
           '/end CHARACTERISTIC\n'
           '/begin CHARACTERISTIC K_V "" VALUE 0x3000 RL_W 0 CM_LIN 0 6553.5 /end CHARACTERISTIC\n'
           '/begin CHARACTERISTIC KB "" VAL_BLK 0x4000 RL_W 0 CM_LIN 0 6553.5 MATRIX_DIM 4 3 /end CHARACTERISTIC\n')
    fd, tmp = tempfile.mkstemp(suffix='.a2l')
    with os.fdopen(fd, 'w', encoding='latin-1') as fh:
        fh.write(txt)
    try:
        got = {c['n']: (c['a'], c['w'], c['dims'], c['f']) for c in parse_a2l(tmp)}
    finally:
        os.remove(tmp)
    check("parser: MAP 8x6 UWORD x0.1 / CURVE 12 SBYTE RAT_FUNC x0.5 / VALUE / VAL_BLK 4x3",
          got == {'KF_T': (0x1000, 2, (8, 6), 0.1), 'KL_Z': (0x2000, 1, (12,), 0.5),
                  'K_V': (0x3000, 2, (1,), 0.1), 'KB': (0x4000, 2, (4, 3), 0.1)})

    D = 0x9000000                                   # MG1CS201 A2Ls address the calibration at 0x09xxxxxx
    # 2 a real shifted segment, irregular spacing, transposed shape, same-shape noise elsewhere
    ecm = [0x700000, 0x700200, 0x700500, 0x700A00, 0x700A80, 0x701000]
    chars = [ch(f'KF_{i}', a + D, (6, 8)) for i, a in enumerate(ecm)]
    chars += [ch(f'NOISE_{i}', 0x9100000 + i * 0x333, (8, 6)) for i in range(20)]
    r = probe(chars, [obj(a, 8, 6) for a in ecm])
    check("real segment -> PORTABLE, 6/6 names at the one true offset",
          r['verdict'] == 'PORTABLE' and names(r) == {a: f'KF_{i}' for i, a in enumerate(ecm)})

    # 3 NEGATIVE: 3 equally spaced 4-point curves vs a longer equally spaced A2L run - seven offsets
    #   explain all three perfectly. Periodic structure, not evidence (the R2B22002B false positive).
    r = probe([ch(f'KL_{i}', 0x9500000 + 8 * i, (4,), f=1.0) for i in range(9)],
              [obj(0x720000 + 8 * i, 4, 1, f=1.0) for i in range(3)])
    check("periodic run, 7 equally good offsets -> NOT PORTABLE, no names, 3 ambiguous",
          r['verdict'] == 'NOT PORTABLE' and not names(r) and len(r['ambiguous']) == 3)

    # 4 NEGATIVE: a chance cluster of 3 spread over a span where 17 other voters disagree
    ecm = [0x730000 + 0x100 * i for i in range(20)]
    r = probe([ch(f'KF_X{i}', ecm[i] + D, (8, 6)) for i in (0, 9, 19)], [obj(a, 8, 6) for a in ecm])
    check("incoherent chance cluster (3 of 20 inside its span) -> NOT PORTABLE",
          r['verdict'] == 'NOT PORTABLE' and not names(r))

    # 5 one real segment among foreign tables -> LOCAL-ONLY, names only inside the segment
    ecm = [0x740000, 0x740300, 0x740700, 0x740720, 0x740900, 0x740F00, 0x741100, 0x741500, 0x741580, 0x741A00]
    seg = ecm[2:6]
    r = probe([ch(f'KF_S{i}', a + D, (8, 6)) for i, a in enumerate(seg)], [obj(a, 8, 6) for a in ecm])
    check("real segment covering 4 of 10 -> LOCAL-ONLY, exactly those 4 names",
          r['verdict'] == 'LOCAL-ONLY' and names(r) == {a: f'KF_S{i}' for i, a in enumerate(seg)})

    # 6 an equally spaced run matched AS A WHOLE is not periodic ambiguity: the true offset explains
    #   6, every neighbour offset at most 5 - unique best, names kept
    r = probe([ch(f'KL_R{i}', 0x750000 + D + 8 * i, (4,), f=1.0) for i in range(6)],
              [obj(0x750000 + 8 * i, 4, 1, f=1.0) for i in range(6)])
    check("whole equal-spaced run 6/6 (neighbour offsets 5/6) -> PORTABLE, names kept, none ambiguous",
          r['verdict'] == 'PORTABLE' and len(names(r)) == 6 and not r['ambiguous'])

    print(f"selftest: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--a2l')
    ap.add_argument('--objects')
    ap.add_argument('--min-support', type=int, default=3)
    ap.add_argument('--names-out', help='write {"0xADDR": "BoschName"} for objects inside REAL clusters')
    ap.add_argument('--selftest', action='store_true', help='synthetic checks incl. periodic / incoherent negative controls')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.a2l and a.objects):
        ap.error('--a2l and --objects are required')
    t0 = time.time()
    chars = parse_a2l(a.a2l)
    objs = L.jload(a.objects)
    r = probe(chars, objs, a.min_support)
    good_diffs = {g[0] for g in r['good']}
    print(f"A2L characteristics {len(chars):,} parsed in {time.time() - t0:.1f}s; voting ECM objects {r['voters']}")
    print(f"clusters with >= {a.min_support} factor+shape-matched supporters: {len(r['real'])}; "
          f"objects explained by several offsets (periodic, dropped): {len(r['ambiguous'])}; REAL: {len(r['good'])}")
    for diff, n, coh, lo, hi, _ in r['real'][:12]:
        print(f"  diff 0x{diff:08X}  support {n:3d}  coherence {coh:4.2f}  ECM 0x{lo:06X}..0x{hi:06X}"
              + ('   REAL' if diff in good_diffs else ''))
    print(f"coverage of voting objects by real clusters: {r['coverage'] * 100:.0f}%   VERDICT: {r['verdict']}")
    names = {}
    for diff, n, coh, lo, hi, sup in r['good']:
        for ad, c in sorted(sup.items()):
            names.setdefault(f"0x{ad:06X}", c['n'])
    for k, v in names.items():
        print(f"  {k} -> {v}")
    if a.names_out:
        json.dump(names, open(a.names_out, 'w'), indent=1)
        print(f"-> {a.names_out} ({len(names)} names, usable as emit_a2l.py --short)")
    return 0


if __name__ == '__main__':
    sys.exit(main())

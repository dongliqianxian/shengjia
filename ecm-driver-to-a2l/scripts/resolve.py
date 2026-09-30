#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 3 - decide width, sign, factor, offset, copies and evidence grade for every tree row.

Evidence grades (global rule: A verified / B high-probability / C hypothesis):
  A  the object itself closed against an ECM screenshot (closure.json)
  B  inherits the factor of a closed family - same ECM category, same unit, same width - with
     every non-sentinel value inside the physical band and no wrap-around in the reading
  C  unit candidate table + physical band only, an ambiguous encoding, or a label conflict
Physical bands: a NAME-aware band (ecm_bands.tsv) beats the unit band. Measured on MG1CS201:
  the unit band alone let 380..590 deg cam phasing and 626.85 degC fan thresholds through.
Width, decided in passes (each pass only sees what earlier passes established):
  1 proof   (A) own closure / odd start address / the whole [N] group of real TABLES
                (>= 4 cells each) ends exactly on the next ECM object / a word would overlap
                the next listed object
  2 data    (B) 16-bit no-limit words, wrap-around count, byte symmetry
  3 family  (B) >= 2 siblings (same name family + unit) decided by 1-2 agree
  4 vote    (B) scalars: one width's best decoding sits on a >= 10 grid and is >= 10x rounder
                (350.0 Nm as a word vs 172 as a byte). Integer byte values sit on a 1-2 grid
                and prove nothing; physical zeros are excluded.
  5 layout  (B) the whole [N] group fits only as bytes / tiny-group geometry hint
  6 prior   (C) majority of PROVEN widths of the same category + unit + shape / default word
  Data outranks layout: ECO #2 [3] 'fits only as bytes' but is plainly a word table.
Copies: ECM's [N] means "N maps in this group", NOT "N contiguous copies". A copy is emitted only
while it fits before the next ECM object, stays clear of known axis arrays, is not erased flash
(all 0xFF, >= 8 bytes) and - for tables - still decodes inside the band or resembles copy 1.
All-sentinel objects carry no scale of their own and inherit it from a decided sibling.
"""
import argparse
import collections
import os
import re
import sqlite3
import statistics
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ecmlib as L  # noqa: E402

UNIT_TABLE = {
    'Nm': [(0.1, 0.0), (1.0, 0.0), (0.09765625, 0.0)],
    'kW': [(0.1, 0.0), (0.01, 0.0), (1.0, 0.0)],
    'hPa': [(0.125, 0.0), (1.0, 0.0), (0.078125, 0.0), (0.1, 0.0)],
    'RPM': [(1.0, 0.0), (0.25, 0.0), (0.5, 0.0)],
    'km/h': [(1 / 128, 0.0), (0.01, 0.0), (1.0, 0.0), (0.1, 0.0)],
    'Lambda': [(1 / 4096, 0.0), (1 / 32768, 0.0), (1 / 1024, 0.0), (0.001, 0.0)],
    '%': [(1 / 256, 0.0), (100 / 65535, 0.0), (100 / 255, 0.0), (0.01, 0.0), (0.1, 0.0), (1.0, 0.0)],
    '% Air': [(0.01, 0.0), (0.0234375, 0.0), (1 / 256, 0.0)],
    'deg BTDC': [(0.5, 0.0), (0.75, 0.0), (0.25, 0.0), (0.1, 0.0), (1 / 1024, 0.0), (0.001, 0.0)],
    'deg': [(0.0234375, 0.0), (0.1, 0.0), (0.5, 0.0), (1.0, 0.0)],
    'degC': [(0.1, -273.15), (0.01, 0.0), (1.0, -40.0), (0.75, -48.0), (1.0, 0.0)],
    'mm': [(0.001, 0.0), (0.01, 0.0), (1 / 256, 0.0)],
    'ratio': [(1 / 16384, 0.0), (1 / 8192, 0.0), (1 / 4096, 0.0)],
    'factor': [(1 / 16384, 0.0), (1 / 4096, 0.0), (1 / 1024, 0.0), (1 / 256, 0.0), (0.01, 0.0),
               (1 / 32768, 0.0), (1.0, 0.0)],
    'kg/h': [(0.1, 0.0), (1 / 64, 0.0), (1.0, 0.0)],
    's': [(0.01, 0.0), (0.1, 0.0), (0.001, 0.0), (1.0, 0.0)],
    'ms': [(1.0, 0.0), (0.1, 0.0)],
    'bar': [(1 / 8192, 0.0), (0.001, 0.0), (0.01, 0.0)],
    'hPa/V': [(0.125, 0.0), (1.0, 0.0)],
    '%/km/h': [(1 / 256, 0.0), (0.01, 0.0)],
    'V': [(1 / 1024, 0.0), (0.001, 0.0), (0.01, 0.0)],
    'mg/stk': [(0.01, 0.0)],
    '': [(1.0, 0.0)],
}
BANDS = {'Nm': (-200, 6600), 'kW': (0, 1000), 'hPa': (0, 8200), 'RPM': (0, 12000), 'km/h': (0, 400),
         'Lambda': (0.4, 2.5), '%': (-200, 300), '% Air': (0, 700), 'deg BTDC': (-64, 72),
         'deg': (-100, 720), 'degC': (-60, 1300), 'mm': (0, 66), 'ratio': (0, 6), 'factor': (-64, 64),
         'kg/h': (0, 6600), 's': (0, 3600), 'ms': (0, 65535), 'bar': (0, 8), 'hPa/V': (0, 8200),
         '%/km/h': (-256, 256), 'V': (0, 20), 'mg/stk': (0, 300), '': (-1e12, 1e12)}
# a candidate whose data-type full scale cannot reach a typical value of the unit is not a
# candidate (u8 x0.1 Nm tops out at 25.5 Nm)
FS_MIN = {'Nm': 100, 'kW': 50, 'hPa': 500, 'RPM': 1000, 'km/h': 50, 'degC': 60, 'Lambda': 1.0,
          '% Air': 100, 'mm': 5, 'bar': 1, 'kg/h': 100, 'ratio': 2}
ANGLE_UNITS = {'deg BTDC', 'deg', 'degCA'}
ANGLE_CANDS = [(0.5, 0.0, True), (0.5, -64.0, False), (0.25, 0.0, False), (0.75, 0.0, True), (0.75, -48.0, False)]
KELVIN = (-273.15, -273.14)
ALIAS = {'rpm': 'RPM', '1/min': 'RPM', 'degc': 'degC', '°c': 'degC', '癥': 'degC', 'c': 'degC',
         'lambda': 'Lambda', '%air': '% Air', '% air': '% Air', 'nm': 'Nm', 'kw': 'kW', 'hpa': 'hPa',
         'deg btdc': 'deg BTDC', '°': 'deg', 'v': 'V', 'hpa/v': 'hPa/V'}


def norm_unit(u):
    k = (u or '').strip()
    return ALIAS.get(k.lower(), k)


def _pairs(path):
    if path and os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            sep = ' => ' if ' => ' in line else ('\t' if '\t' in line else None)
            if sep:
                a, b = line.split(sep, 1)
                yield a.strip(), b.strip()


def load_functions(path):
    return [(re.compile(p), t) for p, t in _pairs(path)]


def load_bands(path):
    out = []
    for p, rhs in _pairs(path):
        try:
            unit, lo, hi = rhs.rsplit(' ', 2)
            out.append((re.compile(p), unit.strip(), float(lo), float(hi)))
        except ValueError:
            continue
    return out


def func_of(name, rules):
    for p, t in rules:
        if p.search(name):
            return t
    return '待判定'


def band_for(x, nb):
    for rx, u, lo, hi in nb:
        if u == x['unit'] and rx.search(x['name']):
            return (lo, hi), 'name'
    return BANDS.get(x['unit'], BANDS['']), 'unit'


def load_catalog(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.text_factory = lambda z: z.decode('utf-8', 'replace')
    cur = con.cursor()
    cat = {i: d for i, d in cur.execute("select CAT_ID,CAT_DESC from MAPS_CATEG where CAT_LANG='ENU'")}
    d2 = collections.defaultdict(list)
    for mid, desc, mtype, parent in cur.execute(
            "select MAP_ID,MAP_DESC,MAP_TYPE,MAP_PARENT from MAPS_TABLE where MAP_LANG='ENU'"):
        d2[desc].append((mid, mtype, parent))
    con.close()
    return cat, d2


def map_id(name, ecm_cat, catalog):
    """exact description first, then the family ('#N' stripped); ECM's own category disambiguates
    descriptions the catalogue reuses across categories."""
    if not catalog:
        return None, None, None, 'no-db'
    cat, d2 = catalog

    def pick(c):
        for mid, t, p in c:
            if cat.get(t) == ecm_cat:
                return mid, cat.get(t), p
        mid, t, p = c[0]
        return mid, cat.get(t), p
    if name in d2:
        return (*pick(d2[name]), 'exact')
    base = L.family(name)
    if base in d2:
        return (*pick(d2[base]), 'family')
    return None, None, None, 'none'


def wraps(v, w, sg=False):
    """adjacent jumps of half the type range or more, ignoring pairs that touch a sentinel - the
    signature of the wrong signedness, or of two byte cells read as one word."""
    half = 128 if w == 1 else 32768
    n = 0
    for a, b in zip(v, v[1:]):
        if L.is_sentinel(a, w, sg) or L.is_sentinel(b, w, sg):
            continue
        if abs(a - b) >= half:
            n += 1
    return n


def byte_symmetry(buf, addr, nbytes):
    """True: lo and hi bytes look alike -> interleaved byte cells.  False: hi bytes nearly
    constant under varying lo bytes -> word cells.  None: cannot tell."""
    raw = buf[addr:addr + nbytes]
    if len(raw) < 8:
        return None
    lo, hi = list(raw[0::2]), list(raw[1::2])
    sl, sh = statistics.pstdev(lo), statistics.pstdev(hi)
    if sl < 1e-9 and sh < 1e-9:
        return None
    if sh < 0.2 * max(sl, 1e-9):
        return False
    if 0.5 <= sh / max(sl, 1e-9) <= 2.0 and abs(statistics.mean(hi) - statistics.mean(lo)) < 32:
        return True
    return None


def sentinel_words16(buf, addr, nbytes):
    if nbytes < 4 or nbytes % 2:
        return False
    v = L.read_vals(buf, addr, nbytes // 2, 2, False)
    return bool(v) and all(z in (0x7FFF, 0x8000) for z in v)


def decode_round(x, w, buf, nb):
    """best roundness over every in-band unit candidate at width w (zeros excluded);
    -1 = no candidate fits, None = nothing to judge."""
    band, _ = band_for(x, nb)
    best, judged = None, False
    for sg in (False, True):
        v = L.read_vals(buf, x['addr'], x['cells'], w, sg)
        if v is None:
            return None
        good = [z for z in v if z != 0 and not L.is_sentinel(z, w, sg)]
        if not good:
            continue
        judged = True
        for f, o in UNIT_TABLE.get(x['unit'], UNIT_TABLE['']):
            if L.phys_range(w, sg, f, o)[1] < FS_MIN.get(x['unit'], float('-inf')):
                continue
            p = [z * f + o for z in good]
            if not (band[0] - 1e-9 <= min(p) and max(p) <= band[1] + 1e-9):
                continue
            base = 0.0 if o in KELVIN else o
            q = [t for t in (z * f + base for z in good) if abs(t) > 1e-9]
            if q:
                r = L.roundness(q)
                best = r if best is None else max(best, r)
    if best is not None:
        return best
    return -1 if judged else None


def proven_width(x, c):
    if c:
        return c['w'], 'A', 'own screenshot closure'
    if x['odd']:
        return 1, 'A', 'odd start address'
    if x.get('geom_width'):
        return x['geom_width'], 'A', f"all {x['cnt']} copies end exactly on the next ECM object"
    if x['cells'] * 2 > x['room'] >= x['cells']:
        return 1, 'A', f"a word table ({x['cells'] * 2} B) would overlap the next listed object ({x['room']} B away)"
    return None, None, None


def data_width(x, buf):
    cells, room, n, a = x['cells'], x['room'], x['cnt'], x['addr']
    if cells >= 6:
        u8 = L.read_vals(buf, a, cells, 1, False)
        u16 = L.read_vals(buf, a, cells, 2, False) if cells * 2 <= room else None
        nbytes = cells * 2 if u16 is not None else cells
    else:                                   # tiny scalars: judge the whole group's bytes
        nbytes = max(4, n * cells)
        nbytes -= nbytes % 2
        if sentinel_words16(buf, a, nbytes):
            return 2, 'B', 'the bytes are 16-bit no-limit words (0x7FFF / 0x8000)'
        u8 = L.read_vals(buf, a, nbytes, 1, False)
        u16 = L.read_vals(buf, a, nbytes // 2, 2, False)
    if u8 and u16 and len(u8) >= 4:
        w8, w16 = wraps(u8, 1), wraps(u16, 2)
        gap = max(2 if cells < 6 else 3, len(u8) // 10)
        if w8 - w16 >= gap:
            return 2, 'B', f'byte reading wraps {w8}x, word reading {w16}x'
        if w16 - w8 >= gap:
            return 1, 'B', f'word reading wraps {w16}x, byte reading {w8}x'
        sym = byte_symmetry(buf, a, nbytes)
        if sym is True and len(set(u8)) > 2:
            return 1, 'B', 'lo and hi bytes look alike - interleaved byte cells'
        if sym is False:
            return 2, 'B', 'hi bytes nearly constant under varying lo bytes - word cells'
    return None, None, None


def fallback_width(x, buf, nb, fammaj, catmaj):
    cells, room, n = x['cells'], x['room'], x['cnt']
    fam = L.family(x['name'])
    for key, label in (((fam, x['unit'], cells == 1), 'same-shape siblings'), ((fam, x['unit'], '*'), 'siblings')):
        cnt = fammaj.get(key)
        if cnt:
            top = cnt.most_common(2)
            if top[0][1] >= 2 and (len(top) == 1 or top[0][1] >= 2 * top[1][1]):
                return top[0][0], 'B', f"{top[0][1]} {label} of '{fam}' proven or measured as u{top[0][0] * 8}"
    if cells <= 4 and cells * 2 <= room:
        r2, r1 = decode_round(x, 2, buf, nb), decode_round(x, 1, buf, nb)
        if r2 is not None and r1 is not None:
            if r2 >= 10 and (r1 == -1 or r2 >= 10 * max(r1, 0.01)):
                return 2, 'B', f"as a word the value sits on a {r2:g} grid, as a byte " + \
                    ('no encoding of the unit fits' if r1 == -1 else f'only on {r1:g}')
            if r1 >= 10 and (r2 == -1 or r1 >= 10 * max(r2, 0.01)):
                return 1, 'B', f"as a byte the value sits on a {r1:g} grid, as a word " + \
                    ('no encoding of the unit fits' if r2 == -1 else f'only on {r2:g}')
    if n > 1 and n * cells * 2 > room >= n * cells:
        return 1, 'B', f"the whole [{n}] group fits only as bytes ({n * cells} of {room} B)"
    if x.get('geom_hint'):
        return x['geom_hint'], 'B', 'tiny-group geometry hint'
    m = catmaj.get((x['cat'], x['unit'], cells == 1))
    if m:
        return m, 'C', f"majority of proven widths for '{x['unit']}' {'scalars' if cells == 1 else 'tables'} in {x['cat']}"
    return 2, 'C', 'default word width'


def in_band(vals, f, o, band, w, sg):
    good = [v for v in vals if not L.is_sentinel(v, w, sg)]
    if not good:
        return True, None                     # all cells are the 'no limit' sentinel
    p = [v * f + o for v in good]
    lo, hi = min(p), max(p)
    return (band[0] - 1e-9 <= lo and hi <= band[1] + 1e-9), (lo, hi)


def _txt(rng, unit):
    return 'all cells are the no-limit sentinel' if rng is None else f"{rng[0]:.2f}..{rng[1]:.2f} {unit}"


def decide_scale(x, w, c, fam, buf, nb):
    unit = x['unit']
    if c:
        return dict(sg=c['sg'], factor=c['factor'], offset=c['offset'], grade='A',
                    why='own screenshot closure (display / raw, zero residual)')
    band, bsrc = band_for(x, nb)
    u = L.read_vals(buf, x['addr'], x['cells'], w, False)
    s = L.read_vals(buf, x['addr'], x['cells'], w, True)
    if u is None:
        return dict(sg=False, factor=1.0, offset=0.0, grade='C', why='object runs past the end of the file')
    wu, ws = wraps(u, w, False), wraps(s, w, True)
    fk = fam.get((x['cat'], unit, w))
    if fk:
        f, o, fsg = fk
        vals = s if fsg else u
        if wraps(vals, w, fsg) <= min(wu, ws):
            ok, rng = in_band(vals, f, o, band, w, fsg)
            if ok:
                return dict(sg=fsg, factor=f, offset=o, grade='B', sentinel_only=rng is None,
                            why=f"family factor of {x['cat']} / '{unit}' closed on a screenshot; {_txt(rng, unit)}")
    if w == 1 and unit in ANGLE_UNITS:
        cands = []
        for f, o, sg in ANGLE_CANDS:
            vals = s if sg else u
            if wraps(vals, 1, sg) > 0:
                continue
            ok, rng = in_band(vals, f, o, band, 1, sg)
            if ok:
                cands.append(dict(factor=f, offset=o, sg=sg, rng=rng))
        if len(cands) == 1:
            k = cands[0]
            return dict(sg=k['sg'], factor=k['factor'], offset=k['offset'], grade='C',
                        why=f"the only byte-angle encoding inside the {bsrc} band: {_txt(k['rng'], unit)}")
        if len(cands) > 1:
            k = cands[0]
            return dict(sg=k['sg'], factor=k['factor'], offset=k['offset'], grade='C', ambiguous=True,
                        alts=[dict(factor=z['factor'], offset=z['offset'], sg=z['sg']) for z in cands[1:]],
                        why=f"{len(cands)} byte encodings read continuously inside the {bsrc} band - shown: "
                            f"{_txt(k['rng'], unit)}; one screenshot of this group decides it")
        return dict(sg=False, factor=1.0, offset=0.0, grade='C', label_conflict=bsrc == 'name',
                    why=f"no byte-angle encoding lands in the {bsrc} band {band}; raw {min(u)}..{max(u)} emitted unscaled")
    order = [False] if u == s else ([False, True] if wu <= ws else [True, False])
    passing = []
    for sg in order:
        vals = s if sg else u
        good = [v for v in vals if not L.is_sentinel(v, w, sg)]
        for f, o in UNIT_TABLE.get(unit, UNIT_TABLE['']):
            if L.phys_range(w, sg, f, o)[1] < FS_MIN.get(unit, float('-inf')):
                continue
            ok, rng = in_band(vals, f, o, band, w, sg)
            if not ok:
                continue
            base = 0.0 if o in KELVIN else o          # judge Kelvin scales round in Kelvin
            q = [t for t in (v * f + base for v in good) if abs(t) > 1e-9]
            rnd = L.roundness(q) if q else 1.0
            util = max(abs(v * f + o) for v in good) if good else None
            passing.append(dict(sg=sg, factor=f, offset=o, rng=rng, rnd=rnd, util=util))
    if not passing:
        return dict(sg=False, factor=1.0, offset=0.0, grade='C', label_conflict=bsrc == 'name',
                    why=f"no candidate lands in the {bsrc} band {band} for '{unit}'; raw {min(u)}..{max(u)} emitted unscaled")
    pick, note = passing[0], ''
    if pick['rnd'] == 0:
        better = [t for t in passing[1:] if t['rnd'] >= 0.5]
        if better:
            pick, note = better[0], '; roundness rescue (first candidate gave values on no decimal grid)'
    if bsrc == 'name' and pick['util'] is not None and x['cells'] >= 4:
        span = band[1] - band[0]
        if pick['util'] < 0.05 * span:
            better = [t for t in passing if t['util'] is not None and t['util'] >= 0.25 * span]
            if better:
                pick, note = better[0], '; range-use rescue (the table used < 5 % of the physical band)'
    return dict(sg=pick['sg'], factor=pick['factor'], offset=pick['offset'], grade='C',
                sentinel_only=pick['rng'] is None,
                why=f"unit candidate inside the {bsrc} band: {_txt(pick['rng'], unit)}{note}")


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    ma, mb = sum(a[:n]) / n, sum(b[:n]) / n
    va = sum((x - ma) ** 2 for x in a[:n])
    vb = sum((y - mb) ** 2 for y in b[:n])
    if va == 0 or vb == 0:
        return 1.0 if va == vb else 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a[:n], b[:n])) / (va * vb) ** 0.5


def _similar(first, v, w, sg):
    f1 = [abs(z) for z in first if not L.is_sentinel(z, w, sg)]
    fk = [abs(z) for z in v if not L.is_sentinel(z, w, sg)]
    if not f1 or not fk:
        return True
    m1, mk = statistics.median(f1), statistics.median(fk)
    if m1 == 0 and mk == 0:
        return True
    if m1 > 0 and 0.2 <= mk / m1 <= 5:
        return True
    return _corr(first, v) >= 0.3


def verify_copies(x, buf, reserved, band):
    w, sg = x['w'], x['sg']
    size = x['cells'] * w
    limit = x['next'] if x['next'] else len(buf)
    first = L.read_vals(buf, x['addr'], x['cells'], w, sg)
    kept, why = 1, ''
    whole_fits = x['addr'] + x['cnt'] * size <= limit      # the declared group fits: nothing to guess
    for k in range(1, x['cnt']):
        a, e = x['addr'] + k * size, x['addr'] + (k + 1) * size
        if e > limit:
            why = 'next ECM object'
            break
        if any(a < re_ and e > rs for rs, re_ in reserved):
            why = 'known axis array'
            break
        chunk = buf[a:e]
        if size >= 8 and chunk.count(0xFF) == len(chunk):
            why = 'erased flash'
            break
        if x['cells'] >= 6 and not x.get('geom_width') and not whole_fits:
            v = L.read_vals(buf, a, x['cells'], w, sg)
            if not in_band(v, x['factor'], x['offset'], band, w, sg)[0] and not _similar(first, v, w, sg):
                why = 'copy leaves the physical band and does not resemble copy 1'
                break
        kept += 1
    return kept, x['addr'] + size > limit, why


def resolve_rows(rows, buf, clo, catalog=None, rules=(), nb=()):
    reserved = []
    for c in clo.values():
        for ax in (c.get('axes') or {}).values():
            if ax and ax.get('kind') == 'stored':
                reserved.append((ax['addr'], ax['addr'] + ax['n'] * ax['w']))
    for x in rows:
        x['unit'] = norm_unit(x['unit'])
    solid = [x for x in rows if x.get('cells')]
    gaps = [x for x in rows if not x.get('cells')]
    for x in solid:                                          # pass 1: proofs
        x['w'], x['w_grade'], x['w_why'] = proven_width(x, clo.get(x['addr']))
        x['w_src'] = 'proof' if x['w'] else None
    for x in solid:                                          # pass 2: the object's own bytes
        if not x['w']:
            w, g, why = data_width(x, buf)
            if w:
                x['w'], x['w_grade'], x['w_why'], x['w_src'] = w, g, why, 'data'
    fammaj = collections.defaultdict(collections.Counter)
    catc = collections.defaultdict(collections.Counter)
    for x in solid:
        if x['w']:
            f = L.family(x['name'])
            fammaj[(f, x['unit'], x['cells'] == 1)][x['w']] += 1
            fammaj[(f, x['unit'], '*')][x['w']] += 1
            if x['w_src'] == 'proof':
                catc[(x['cat'], x['unit'], x['cells'] == 1)][x['w']] += 1
    catmaj = {k: v.most_common(1)[0][0] for k, v in catc.items()}
    for x in solid:                                          # passes 3-6
        if not x['w']:
            x['w'], x['w_grade'], x['w_why'] = fallback_width(x, buf, nb, fammaj, catmaj)
            x['w_src'] = 'fallback'
    famsets = collections.defaultdict(set)
    for x in solid:
        c = clo.get(x['addr'])
        if c:
            famsets[(x['cat'], x['unit'], c['w'])].add((c['factor'], c['offset'], c['sg']))
    fam = {k: next(iter(v)) for k, v in famsets.items() if len({(f, o) for f, o, _ in v}) == 1}
    conflicts = {k: sorted(v) for k, v in famsets.items() if len({(f, o) for f, o, _ in v}) > 1}
    for x in solid:
        x.update(decide_scale(x, x['w'], clo.get(x['addr']), fam, buf, nb))
        for k in ('ambiguous', 'label_conflict', 'sentinel_only'):
            x.setdefault(k, False)
        if not x['sentinel_only']:                           # zeros / sentinels only: no scale information
            vv = L.read_vals(buf, x['addr'], x['cells'], x['w'], x['sg']) or [0]
            if all(z == 0 or L.is_sentinel(z, x['w'], x['sg']) for z in vv):
                x['sentinel_only'] = True
    rank = {'A': 0, 'B': 1, 'C': 2}                           # all-sentinel: borrow a sibling's scale
    donor = {}
    for x in solid:
        if not x['sentinel_only'] and not x['label_conflict']:
            k = (L.family(x['name']), x['unit'], x['w'])
            if k not in donor or rank[x['grade']] < rank[donor[k]['grade']]:
                donor[k] = x
    for x in solid:
        k = (L.family(x['name']), x['unit'], x['w'])
        if x['sentinel_only'] and x['grade'] != 'A' and k in donor:
            y = donor[k]
            x.update(sg=y['sg'], factor=y['factor'], offset=y['offset'],
                     why=x['why'] + f"; all cells are sentinels - scaling taken from sibling '{y['name']}'")
    for x in solid:
        v = L.read_vals(buf, x['addr'], x['cells'], x['w'], x['sg']) or [0]
        x['raw_min'], x['raw_max'] = min(v), max(v)
        x['phy_min'] = min(v) * x['factor'] + x['offset']
        x['phy_max'] = max(v) * x['factor'] + x['offset']
        good = [t for t in (z * x['factor'] + (0.0 if x['offset'] in KELVIN else x['offset'])
                            for z in v if not L.is_sentinel(z, x['w'], x['sg'])) if abs(t) > 1e-9]
        x['round'] = L.roundness(good) if good else 1.0
        x['flag'] = x['unit'] in ('', '-') and x['raw_min'] >= 0 and x['raw_max'] <= 1
        x['copies'], x['first_copy_overlaps_next'], x['copy_stop'] = \
            verify_copies(x, buf, reserved, band_for(x, nb)[0])
        x['stride_ok'] = x['copies'] >= x['cnt']
        x['mapid'], x['dbcat'], x['parent'], x['idhow'] = map_id(x['name'], x['cat'], catalog)
        x['family'] = L.family(x['name'])
        x['func'] = func_of(x['name'], rules)
        x['closed'] = x['addr'] in clo
    for x in gaps:
        x.update(w=None, grade='GAP', why='tree row unreadable - not emitted', ambiguous=False, label_conflict=False,
                 family=L.family(x['name']), func=func_of(x['name'], rules), copies=0, stride_ok=True)
    return solid + gaps, conflicts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tree')
    ap.add_argument('--closure')
    ap.add_argument('--bin')
    ap.add_argument('--db', help='ECM ecm30.db (read-only) for MAP_ID / family / category cross-check')
    ap.add_argument('--functions', default=os.path.join(HERE, 'ecm_functions.tsv'))
    ap.add_argument('--bands', default=os.path.join(HERE, 'ecm_bands.tsv'))
    ap.add_argument('--out', default='objects.json')
    ap.add_argument('--endian', choices=('le', 'be'), default='le')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    L.ENDIAN = '<' if a.endian == 'le' else '>'
    tree = L.jload(a.tree)
    buf = open(a.bin, 'rb').read()
    clo = {c['addr']: c for c in (L.jload(a.closure) if a.closure else []) if 'error' not in c}
    catalog = load_catalog(a.db) if a.db else None
    objs, conflicts = resolve_rows(tree['rows'], buf, clo, catalog, load_functions(a.functions), load_bands(a.bands))
    L.jsave(objs, a.out)
    solid = [o for o in objs if o['grade'] != 'GAP']
    g = collections.Counter(o['grade'] for o in objs)
    wg = collections.Counter(o['w_grade'] for o in solid)
    print(f"objects {len(solid)} (+{g.get('GAP', 0)} tree gaps)   grade A {g.get('A', 0)}  B {g.get('B', 0)}  C {g.get('C', 0)}"
          f"   ambiguous encodings {sum(1 for o in solid if o['ambiguous'])}"
          f"   label conflicts {sum(1 for o in solid if o['label_conflict'])}")
    print(f"width evidence: A {wg.get('A', 0)}  B {wg.get('B', 0)}  C {wg.get('C', 0)}   by pass "
          f"{dict(collections.Counter(o['w_src'] for o in solid))}")
    stops = collections.Counter(o['copy_stop'] for o in solid if not o['stride_ok'])
    print(f"copies: declared {sum(o['cnt'] for o in solid)}  located {sum(o['copies'] for o in solid)}"
          f"   groups with unlocated copies {sum(1 for o in solid if not o['stride_ok'])} {dict(stops)}"
          f"   first copy overlaps next object {sum(1 for o in solid if o['first_copy_overlaps_next'])}")
    if catalog:
        h = collections.Counter(o.get('idhow') for o in solid)
        mism = [o for o in solid if o.get('dbcat') and o['dbcat'] != o['cat']]
        print(f"MAP_ID: exact {h.get('exact', 0)}  family {h.get('family', 0)}  none {h.get('none', 0)}"
              f"   category cross-check mismatches {len(mism)}")
    for k, v in conflicts.items():
        print(f"  family NOT propagated (closures disagree) {k}: {v}")
    print(f"-> {a.out}")
    return 0


def selftest():
    buf = bytearray(0x300)
    struct.pack_into('<6H', buf, 0x100, 3500, 3500, 3400, 3300, 3200, 3100)       # closed Nm table
    struct.pack_into('<4H', buf, 0x140, 4000, 4200, 4300, 4400)                   # same family
    struct.pack_into('<8B', buf, 0x181, 100, 110, 120, 130, 140, 150, 160, 170)   # byte angle, mid-range
    struct.pack_into('<H', buf, 0x1A0, 12750)                                     # km/h needing rescue
    struct.pack_into('<H', buf, 0x1B0, 9000)                                      # fan temperature
    struct.pack_into('<H', buf, 0x1C0, 3500)                                      # 350.0 Nm scalar
    rows = [dict(cat='T', name='Lim #1', unit='Nm', addr=0x100, cnt=1, r=2, c=3),
            dict(cat='T', name='Lim #2', unit='Nm', addr=0x140, cnt=1, r=4, c=1),
            dict(cat='S', name='Spark advance base map #1', unit='deg BTDC', addr=0x181, cnt=1, r=2, c=4),
            dict(cat='L', name='Vmax #1', unit='km/h', addr=0x1A0, cnt=1, r=1, c=1),
            dict(cat='D', name='Fan activation temperature #1', unit='degC', addr=0x1B0, cnt=1, r=1, c=1),
            dict(cat='E', name='Ceiling #2', unit='Nm', addr=0x1C0, cnt=1, r=1, c=1)]
    addrs = sorted(r['addr'] for r in rows)
    for r in rows:
        r['cells'] = r['r'] * r['c']
        r['next'] = next((a for a in addrs if a > r['addr']), None)
        r['room'] = (r['next'] or len(buf)) - r['addr']
        r['odd'] = bool(r['addr'] & 1)
        r['geom_width'] = r['geom_hint'] = None
    nb = load_bands(os.path.join(HERE, 'ecm_bands.tsv'))
    clo = {0x100: dict(addr=0x100, w=2, sg=False, factor=0.1, offset=0.0)}
    objs, _ = resolve_rows([dict(r) for r in rows], bytes(buf), clo, nb=nb)
    by = {o['name']: o for o in objs}
    checks = {
        'closed object is A': by['Lim #1']['grade'] == 'A',
        'family member is B x0.1': by['Lim #2']['grade'] == 'B' and by['Lim #2']['factor'] == 0.1,
        'mid-range byte angle is ambiguous': by['Spark advance base map #1']['ambiguous'] and by['Spark advance base map #1']['w'] == 1,
        'km/h 12750 -> 127.50 (u16 x0.01)': by['Vmax #1']['w'] == 2 and abs(by['Vmax #1']['phy_max'] - 127.5) < 1e-9,
        'fan 9000 -> 90.00 degC (not 626.85)': abs(by['Fan activation temperature #1']['phy_max'] - 90) < 1e-9,
        '350 Nm scalar stays a word': by['Ceiling #2']['w'] == 2 and abs(by['Ceiling #2']['phy_max'] - 350) < 1e-9,
    }
    for k, v in checks.items():
        print(f"  {'ok ' if v else 'BAD'} {k}")
    objs2, _ = resolve_rows([dict(r) for r in rows], bytes(buf), {}, nb=nb)
    ok2 = all(o['grade'] == 'C' for o in objs2)
    print(f"  {'ok ' if ok2 else 'BAD'} negative control: without the closure nothing is graded above C")
    ok = all(checks.values()) and ok2
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

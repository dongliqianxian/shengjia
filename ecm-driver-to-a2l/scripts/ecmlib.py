# -*- coding: utf-8 -*-
"""Shared helpers for the ecm-driver-to-a2l scripts. Standard library only."""
import json
import re
import struct

# AURIX / TriCore / most Bosch data is little endian. Set '>' (via --endian be) only when
# multi-vendor-ecu-engine has established a big-endian core (SH / PowerPC).
ENDIAN = '<'

DT_NAME = {(1, False): 'UBYTE', (1, True): 'SBYTE', (2, False): 'UWORD', (2, True): 'SWORD',
           (4, False): 'ULONG', (4, True): 'SLONG'}
RAW_RANGE = {(1, False): (0, 255), (1, True): (-128, 127), (2, False): (0, 65535),
             (2, True): (-32768, 32767), (4, False): (0, 4294967295), (4, True): (-2147483648, 2147483647)}

# Display rule (global): physical values 2 decimals, pure index axes integer.
# Only the DISPLAY is rounded - COEFFS keep full precision (1/4096 printed as 0.00 kills the table).
FMT_PHYS = '%10.2'
FMT_INDEX = '%6.0'

# factors that real calibrations use; closure sweeps these instead of trusting a least-squares
# slope, because ECM rounds what it displays and that perturbs a fitted slope.
NICE_FACTORS = [1.0, 0.1, 0.01, 0.001, 0.0001, 0.5, 0.25, 0.125, 0.0625, 1 / 32, 1 / 64, 1 / 128,
                1 / 256, 1 / 512, 1 / 1024, 1 / 2048, 1 / 4096, 1 / 8192, 1 / 16384, 1 / 32768,
                1 / 65536, 0.0234375, 0.75, 0.375, 0.09765625, 100 / 65535, 100 / 32767, 100 / 255,
                100 / 1024, 10 / 65536, 0.2, 0.05, 0.02, 0.005, 1 / 255, 2.0, 4.0, 10.0]
NICE_OFFSETS = [0.0, -273.15, -273.14, -40.0, -48.0, -50.0, -64.0, -100.0, -128.0]
GRANS = (1000, 100, 50, 25, 10, 5, 2, 1, 0.5, 0.25, 0.1, 0.05, 0.01)


def fmt_char(w, sg):
    return ({1: 'b', 2: 'h', 4: 'i'} if sg else {1: 'B', 2: 'H', 4: 'I'})[w]


def read_vals(buf, addr, n, w, sg):
    if n <= 0 or addr < 0 or addr + n * w > len(buf):
        return None
    return list(struct.unpack_from(ENDIAN + str(n) + fmt_char(w, sg), buf, addr))


def is_sentinel(v, w, sg):
    """'no limit' markers - excluded from physical-band checks, never from the data."""
    if w == 1:                      # 127/128 are ordinary byte data (e.g. spark raw 128) - only 0xFF
        return v in ((127, -128) if sg else (255,))
    if w == 2:
        return v in ((32767, -32768) if sg else (65535, 32767, 32768))
    return False


def rough(v, ncol):
    """mean |2nd difference| along the fast axis / range. 0 = perfectly smooth."""
    if not v or len(v) < 3:
        return 0.0
    rng = max(v) - min(v)
    if rng == 0:
        return 0.0
    ncol = max(1, min(ncol, len(v)))
    acc = k = 0
    if ncol >= 3:
        for s in range(0, len(v) - ncol + 1, ncol):
            row = v[s:s + ncol]
            for j in range(1, len(row) - 1):
                acc += abs(row[j - 1] - 2 * row[j] + row[j + 1])
                k += 1
    if k == 0:
        for j in range(1, len(v) - 1):
            acc += abs(v[j - 1] - 2 * v[j] + v[j + 1])
            k += 1
    return acc / k / rng if k else 0.0


def roundness(p):
    """coarsest granularity that >=90 % of the values sit on (hand-entered numbers are round)."""
    if not p:
        return 0.0
    for g in GRANS:
        if sum(1 for v in p if abs(v / g - round(v / g)) < 1e-6) >= 0.9 * len(p):
            return g
    return 0.0


def phys_range(w, sg, factor, offset):
    lo, hi = RAW_RANGE[(w, sg)]
    a, b = lo * factor + offset, hi * factor + offset
    return min(a, b), max(a, b)


def ident(s, maxlen=100):
    s = re.sub(r'[^0-9A-Za-z]+', '_', s).strip('_')
    s = re.sub(r'_+', '_', s) or 'X'
    if s[0].isdigit():
        s = 'M_' + s
    return s[:maxlen]


def num(v):
    """full-precision, round-trip-exact number text for COEFFS and limits."""
    if isinstance(v, int):
        return str(v)
    if abs(v - round(v)) < 1e-12 and abs(v) < 1e15:
        return str(int(round(v)))
    return repr(float(v))


def family(name):
    """ECM numbers instances at run time ('#7'), so the family is the name without it."""
    return re.sub(r'\s*#\d+\s*$', '', name).strip()


def load_tree(path):
    rows, expect = [], {}
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        line = line.rstrip('\n\r')
        if not line.strip():
            continue
        if line.startswith('#CAT'):
            p = line.split('\t')
            if len(p) >= 3:
                expect[p[1].strip()] = int(p[2])
            continue
        if line.startswith('#'):
            continue
        p = line.split('\t')
        if len(p) < 7:
            raise ValueError(f"{path}:{ln}: need 7 tab-separated columns, got {len(p)}")
        cat, name, unit, addr, cnt, r, c = [x.strip() for x in p[:7]]

        def q(x):
            return None if x in ('?', '') else int(x)
        rows.append(dict(line=ln, cat=cat, name=name, unit=unit, addr=int(addr, 16),
                         cnt=q(cnt), r=q(r), c=q(c), note=p[7].strip() if len(p) > 7 else ''))
    return rows, expect


def as_int(a):
    return int(a, 16) if isinstance(a, str) else int(a)


def jload(p):
    return json.load(open(p, encoding='utf-8'))


def jsave(obj, p):
    json.dump(obj, open(p, 'w', encoding='utf-8'), indent=1, ensure_ascii=False, default=str)

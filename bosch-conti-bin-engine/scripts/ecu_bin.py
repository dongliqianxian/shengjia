#!/usr/bin/env python3
"""
ecu_bin.py — generic A2L-style ECU binary engine for the bosch-conti-bin-engine skill
(and, via multi-vendor-ecu-engine, for any factor/offset-scaled ECU map regardless of badge).

Pure Python 3, no dependencies. Sibling of denso_bin.py:
  - DENSO uses fixed proprietary encodings  -> denso_bin.py
  - Bosch/Conti/most others use A2L linear  -> this file: phys = raw*factor + offset

Describe a map by: address, rows, cols, datatype (u8/s8/u16/s16/u32/s32/f32), endian, factor, offset,
and (optionally) stride/header for strided layouts. Axes are separate Axis arrays.

SAFETY: never recomputes a checksum. Bosch/Conti checksums are multi-layer and may be signed; fix in
WinOLS plugin / ECM Titanium / the flash tool. Every write path prints CHECKSUM_REMINDER.

Run `python ecu_bin.py --selftest`.
"""

import struct
import os


# datatype -> (struct char, byte width, signed)
_DT = {
    "u8":  ("B", 1, False), "s8":  ("b", 1, True),
    "u16": ("H", 2, False), "s16": ("h", 2, True),
    "u32": ("I", 4, False), "s32": ("i", 4, True),
    "f32": ("f", 4, False), "f64": ("d", 8, False),
}


def _fmt(datatype, endian):
    if datatype not in _DT:
        raise ValueError(f"unknown datatype '{datatype}'. Known: {list(_DT)}")
    ec = "<" if endian in ("little", "<", "le") else ">"
    return ec + _DT[datatype][0]


def _width(datatype):
    return _DT[datatype][1]


def _clamp_raw(datatype, raw):
    ch, width, signed = _DT[datatype]
    if ch in ("f", "d"):
        return raw
    bits = width * 8
    if signed:
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    else:
        lo, hi = 0, (1 << bits) - 1
    return max(lo, min(hi, int(round(raw))))


# --------------------------------------------------------------------------- #
# Axis (breakpoint array)
# --------------------------------------------------------------------------- #

class Axis:
    """A 1-D breakpoint array: phys = raw*factor + offset."""

    def __init__(self, name, address, count, datatype, endian="little", factor=1.0, offset=0.0):
        self.name = name
        self.address = address
        self.count = count
        self.datatype = datatype
        self.endian = endian
        self.factor = factor
        self.offset = offset
        self.width = _width(datatype)

    def read(self, data, engineering=True):
        fmt = _fmt(self.datatype, self.endian)
        out = []
        for i in range(self.count):
            raw = struct.unpack_from(fmt, data, self.address + i * self.width)[0]
            out.append(raw * self.factor + self.offset if engineering else raw)
        return out

    def is_monotonic(self, data):
        v = self.read(data)
        return all(v[i] < v[i + 1] for i in range(len(v) - 1)) or \
               all(v[i] > v[i + 1] for i in range(len(v) - 1))


# --------------------------------------------------------------------------- #
# Map (2-D / 1-D / scalar table), A2L linear scaling
# --------------------------------------------------------------------------- #

class EcuMap:
    """
    Describe one Bosch/Conti map. phys = raw*factor + offset.

    Layouts:
      PACKED  (stride_bytes=None): contiguous row-major; cell[r][c] at addr + (r*cols+c)*width.
      STRIDED (stride_bytes set):  each row = header_bytes + cols cells, row pitch = stride_bytes.

    For a 1-D table use rows=1; for a scalar use rows=1, cols=1.
    """

    def __init__(self, name, address, rows, cols, datatype, endian="little",
                 factor=1.0, offset=0.0, stride_bytes=None, header_bytes=0):
        self.name = name
        self.address = address
        self.rows = rows
        self.cols = cols
        self.datatype = datatype
        self.endian = endian
        self.factor = factor
        self.offset = offset
        self.width = _width(datatype)
        self.header_bytes = header_bytes
        self.stride_bytes = stride_bytes if stride_bytes is not None else cols * self.width
        self._fmt = _fmt(datatype, endian)

    def cell_offset(self, r, c):
        return self.address + r * self.stride_bytes + self.header_bytes + c * self.width

    def _to_eng(self, raw):
        return raw * self.factor + self.offset

    def _to_raw(self, eng):
        return _clamp_raw(self.datatype, (eng - self.offset) / self.factor)

    def read(self, data, engineering=True):
        out = []
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                raw = struct.unpack_from(self._fmt, data, self.cell_offset(r, c))[0]
                row.append(self._to_eng(raw) if engineering else raw)
            out.append(row)
        return out

    def write_cell(self, buf, r, c, eng_value):
        raw = self._to_raw(eng_value)
        struct.pack_into(self._fmt, buf, self.cell_offset(r, c), raw)

    def apply(self, buf, fn, rows=None, cols=None):
        """fn(r, c, current_eng) -> new_eng (return None/unchanged to skip). Returns #changed."""
        rrange = rows if rows is not None else range(self.rows)
        crange = cols if cols is not None else range(self.cols)
        changed = 0
        for r in rrange:
            for c in crange:
                off = self.cell_offset(r, c)
                raw = struct.unpack_from(self._fmt, buf, off)[0]
                cur = self._to_eng(raw)
                new = fn(r, c, cur)
                if new is None or new == cur:
                    continue
                new_raw = self._to_raw(new)
                if new_raw != raw:
                    struct.pack_into(self._fmt, buf, off, new_raw)
                    changed += 1
        return changed


# --------------------------------------------------------------------------- #
# File helpers
# --------------------------------------------------------------------------- #

def load(path):
    with open(path, "rb") as f:
        return bytearray(f.read())


def save_as(buf, src_path, suffix="_Stage1", out_dir=None, ext=None):
    d = out_dir or os.path.dirname(src_path) or "."
    base = os.path.basename(src_path)
    stem, srcext = os.path.splitext(base)
    out = os.path.join(d, f"{stem}{suffix}{ext or srcext}")
    with open(out, "wb") as f:
        f.write(buf)
    return out


CHECKSUM_REMINDER = (
    "⚠ CHECKSUM: file written but NOT valid for flashing. Bosch/Conti integrity is multi-layer and "
    "may be signed (EDC17/MED17/MEVD17 cal+program CRC; MD1/MG1 RSA/secure boot). Fix recomputable "
    "layers via checksum-crc-engine, then use the WinOLS checksum plugin / ECM Titanium Auto-Fix and "
    "the flash tool's auto-checksum. Do NOT call this file flash-ready."
)


def diff_maps(stock_buf, mod_buf, emap, tol=0.01):
    s = emap.read(stock_buf)
    m = emap.read(mod_buf)
    diffs = []
    for r in range(emap.rows):
        for c in range(emap.cols):
            if abs(s[r][c] - m[r][c]) > tol:
                diffs.append((r, c, s[r][c], m[r][c]))
    return diffs


def find_ascii(data, needle):
    nb = needle.encode() if isinstance(needle, str) else needle
    offs, start = [], 0
    while True:
        i = data.find(nb, start)
        if i < 0:
            break
        offs.append(i)
        start = i + 1
    return offs


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _selftest():
    buf = bytearray(0x200)

    # u16 LE map, factor 0.1: write engineering 250.0 -> raw 2500 = 0x09C4 LE -> C4 09
    m = EcuMap("t", 0x10, rows=2, cols=2, datatype="u16", endian="little", factor=0.1)
    m.write_cell(buf, 0, 0, 250.0)
    assert buf[0x10] == 0xC4 and buf[0x11] == 0x09, buf[0x10:0x12].hex()
    assert abs(m.read(buf)[0][0] - 250.0) < 1e-6

    # same value big-endian -> 09 C4
    mb = EcuMap("tb", 0x20, rows=1, cols=1, datatype="u16", endian="big", factor=0.1)
    mb.write_cell(buf, 0, 0, 250.0)
    assert buf[0x20] == 0x09 and buf[0x21] == 0xC4, buf[0x20:0x22].hex()

    # factor+offset round-trip on s16
    ms = EcuMap("s", 0x30, 1, 1, "s16", "little", factor=0.5, offset=-40.0)
    ms.write_cell(buf, 0, 0, 12.0)            # raw = (12 - -40)/0.5 = 104
    assert ms.read(buf, engineering=False)[0][0] == 104
    assert abs(ms.read(buf)[0][0] - 12.0) < 1e-6

    # clamp: u8 cannot exceed 255
    mc = EcuMap("c", 0x40, 1, 1, "u8", "little", factor=1.0)
    mc.write_cell(buf, 0, 0, 9999)
    assert mc.read(buf, engineering=False)[0][0] == 255

    # f32 LE
    mf = EcuMap("f", 0x50, 1, 1, "f32", "little", factor=1.0)
    mf.write_cell(buf, 0, 0, 3.14159)
    assert abs(mf.read(buf)[0][0] - 3.14159) < 1e-5

    # strided: 2-byte header + 3 u16 cells, stride 8
    st = EcuMap("st", 0x80, rows=2, cols=3, datatype="u16", endian="little",
                factor=1.0, stride_bytes=8, header_bytes=2)
    for r in range(2):
        for c in range(3):
            st.write_cell(buf, r, c, (r + 1) * 100 + c)
    g = st.read(buf)
    assert g[1][2] == 202, g
    # header bytes (offset 0x80, 0x88) remain zero (untouched)
    assert buf[0x80] == 0 and buf[0x88] == 0

    # apply over sub-region
    changed = st.apply(buf, lambda r, c, v: v + 1, rows=range(1, 2))
    assert changed == 3 and st.read(buf)[1][0] == 201

    # axis monotonic check
    ax = Axis("rpm", 0x100, count=4, datatype="u16", endian="little", factor=10.0)
    for i, val in enumerate([800, 1600, 3200, 6400]):
        struct.pack_into("<H", buf, 0x100 + i * 2, val // 10)
    assert ax.read(buf) == [800, 1600, 3200, 6400]
    assert ax.is_monotonic(buf)

    # diff
    mod = bytearray(buf)
    m.write_cell(mod, 0, 0, 300.0)
    d = diff_maps(buf, mod, m)
    assert d and d[0][0:2] == (0, 0) and abs(d[0][3] - 300.0) < 1e-6

    print("ecu_bin.py self-test: ALL PASS")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Bosch/Conti A2L-style bin engine — read/diff CLI")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--addr", type=lambda x: int(x, 0))
    ap.add_argument("--rows", type=int, default=1)
    ap.add_argument("--cols", type=int, default=1)
    ap.add_argument("--dt", default="u16", choices=list(_DT))
    ap.add_argument("--endian", default="little", choices=["little", "big"])
    ap.add_argument("--factor", type=float, default=1.0)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--stride", type=int, default=None)
    ap.add_argument("--header", type=int, default=0)
    args = ap.parse_args()

    if args.selftest:
        _selftest()
    elif args.file and args.addr is not None:
        data = load(args.file)
        m = EcuMap("cli", args.addr, args.rows, args.cols, args.dt, args.endian,
                   args.factor, args.offset, args.stride, args.header)
        for r, row in enumerate(m.read(data)):
            print(f"r{r:2d}: " + " ".join(f"{v:9.3f}" for v in row))
    else:
        ap.print_help()

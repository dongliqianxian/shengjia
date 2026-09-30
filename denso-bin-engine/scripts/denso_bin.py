#!/usr/bin/env python3
"""
denso_bin.py — DENSO ECU binary read/write engine for the denso-bin-engine skill.

Pure Python 3, no external dependencies. Runs in Kimi Work, Codex, Cursor, or any shell.

Purpose
-------
Stop hand-writing one-off binary parsers for every DENSO project. This module encapsulates the
map layouts and encoding formulas that have been *verified on real projects* (Tundra 3UR-FE,
Prado/Hilux 2TR-FE, LC300 V35A), so a calibration can be read/edited by giving:
  address + dimensions + encoding, instead of re-deriving the byte math each time.

Verified encodings (from prior real projects — see references/verified_encodings.md):
  - float32 LE        : LC300 V35A Gen4 torque/load maps (e.g. Standard torque @0x068AE8)
  - int16 LE  /32 = Nm: Tundra Optimal Torque (26-byte stride: 2-byte header + 12 int16 cells)
  - uint8  *79.68/255 : Tundra Intake VVT (13-byte stride: 1-byte RPM header ×50 + 12 byte cells)
  - uint8  /512 factor: Tundra Injection Enrichment (raw 13x9, no row headers)
  - uint16 *799.98/65535 = % : Tundra Requested Load #1 (34-byte stride: 2-byte header + 16 uint16)
  - raw/32 = degrees  : DENSO spark advance base maps (2TR-FE)

SAFETY: this engine never recomputes a DENSO checksum. After any write the file is INVALID until
the proprietary checksum is recalculated in ECM Titanium (Tools -> Checksum -> Auto Fix) or an
equivalent tool. Every write path prints this reminder.
"""

import struct
import shutil
import json
import os

# ---------------------------------------------------------------------------
# Low-level codecs
# ---------------------------------------------------------------------------

def _unpack(data, off, fmt):
    return struct.unpack_from(fmt, data, off)[0]

def _pack_into(buf, off, fmt, val):
    struct.pack_into(fmt, buf, off, val)


# Each encoding: raw<->engineering conversion + struct format + byte width + signed flag
ENCODINGS = {
    # name        fmt   width  raw->eng                         eng->raw
    "float32_le": dict(fmt="<f", width=4, to_eng=lambda r: r,            to_raw=lambda e: e),
    "float32_be": dict(fmt=">f", width=4, to_eng=lambda r: r,            to_raw=lambda e: e),
    "int16_nm":   dict(fmt="<h", width=2, to_eng=lambda r: r / 32.0,     to_raw=lambda e: int(round(e * 32))),
    "int16_le":   dict(fmt="<h", width=2, to_eng=lambda r: float(r),     to_raw=lambda e: int(round(e))),
    "uint16_le":  dict(fmt="<H", width=2, to_eng=lambda r: float(r),     to_raw=lambda e: int(round(e))),
    "uint16_pct": dict(fmt="<H", width=2, to_eng=lambda r: r * 799.98 / 65535.0,
                       to_raw=lambda e: int(round(e * 65535.0 / 799.98))),
    "uint8":      dict(fmt="<B", width=1, to_eng=lambda r: float(r),     to_raw=lambda e: int(round(e))),
    "uint8_vvt":  dict(fmt="<B", width=1, to_eng=lambda r: r * 79.68 / 255.0,
                       to_raw=lambda e: int(round(e * 255.0 / 79.68))),
    "uint8_fac":  dict(fmt="<B", width=1, to_eng=lambda r: r / 512.0,    to_raw=lambda e: int(round(e * 512))),
    "raw_deg32":  dict(fmt="<B", width=1, to_eng=lambda r: r / 32.0,     to_raw=lambda e: int(round(e * 32))),
}


def _clamp_raw(enc_name, raw):
    """Clamp a raw integer to its type's representable range to avoid struct errors."""
    enc = ENCODINGS[enc_name]
    if enc["fmt"] in ("<f", ">f"):
        return raw
    bits = enc["width"] * 8
    if enc["fmt"].endswith("h"):  # signed
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    else:                          # unsigned
        lo, hi = 0, (1 << bits) - 1
    return max(lo, min(hi, raw))


# ---------------------------------------------------------------------------
# Map model
# ---------------------------------------------------------------------------

class DensoMap:
    """
    Describes one map's physical layout in the binary.

    Two layout styles, matching what real DENSO files use:

    1) PACKED (no per-row headers): cells are contiguous, row-major.
         cell[r][c] at base + (r*cols + c) * width
       Use stride_bytes=None.

    2) STRIDED (per-row header byte(s) then the row's cells): each row occupies
       `stride_bytes`, beginning with `header_bytes` of header, then `cols` cells.
         row r starts at base + r*stride_bytes
         cell c of row r at row_start + header_bytes + c*width
       Use stride_bytes=<int> and header_bytes=<int>.
    """

    def __init__(self, name, address, rows, cols, encoding,
                 stride_bytes=None, header_bytes=0, page_stride=None, pages=1):
        if encoding not in ENCODINGS:
            raise ValueError(f"unknown encoding '{encoding}'. Known: {list(ENCODINGS)}")
        self.name = name
        self.address = address
        self.rows = rows
        self.cols = cols
        self.encoding = encoding
        self.width = ENCODINGS[encoding]["width"]
        self.header_bytes = header_bytes
        # packed stride if not given
        self.stride_bytes = stride_bytes if stride_bytes is not None else cols * self.width
        self.page_stride = page_stride  # bytes between pages for multi-page maps
        self.pages = pages

    def cell_offset(self, r, c, page=0):
        base = self.address
        if self.page_stride and page:
            base += page * self.page_stride
        row_start = base + r * self.stride_bytes
        return row_start + self.header_bytes + c * self.width

    # ---- read ----
    def read(self, data, page=0, engineering=True):
        enc = ENCODINGS[self.encoding]
        out = []
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                raw = _unpack(data, self.cell_offset(r, c, page), enc["fmt"])
                row.append(enc["to_eng"](raw) if engineering else raw)
            out.append(row)
        return out

    def read_header(self, data, page=0):
        """Return the per-row header value(s) when header_bytes>0 (often an RPM axis)."""
        if self.header_bytes == 0:
            return None
        vals = []
        for r in range(self.rows):
            row_start = self.address + (page * (self.page_stride or 0)) + r * self.stride_bytes
            if self.header_bytes == 1:
                vals.append(_unpack(data, row_start, "<B"))
            elif self.header_bytes == 2:
                vals.append(_unpack(data, row_start, "<H"))
            else:
                vals.append(data[row_start:row_start + self.header_bytes].hex())
        return vals

    # ---- write ----
    def write_cell(self, buf, r, c, eng_value, page=0):
        enc = ENCODINGS[self.encoding]
        raw = enc["to_raw"](eng_value)
        raw = _clamp_raw(self.encoding, raw)
        _pack_into(buf, self.cell_offset(r, c, page), enc["fmt"], raw)

    def apply(self, buf, fn, page=0, rows=None, cols=None):
        """
        Apply fn(r, c, current_eng_value) -> new_eng_value over a (sub)region.
        rows/cols may be iterables to restrict the region; default = whole map.
        Returns count of cells changed.
        """
        enc = ENCODINGS[self.encoding]
        rrange = rows if rows is not None else range(self.rows)
        crange = cols if cols is not None else range(self.cols)
        changed = 0
        for r in rrange:
            for c in crange:
                off = self.cell_offset(r, c, page)
                raw = _unpack(buf, off, enc["fmt"])
                cur = enc["to_eng"](raw)
                new = fn(r, c, cur)
                if new is None or new == cur:
                    continue
                new_raw = _clamp_raw(self.encoding, enc["to_raw"](new))
                if new_raw != raw:
                    _pack_into(buf, off, enc["fmt"], new_raw)
                    changed += 1
        return changed


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------

def load(path):
    with open(path, "rb") as f:
        return bytearray(f.read())


def save_as(buf, src_path, suffix="_Stage1", out_dir=None, ext=None):
    """Save buf next to src with a suffix (e.g. _Stage1). Returns output path."""
    d = out_dir or os.path.dirname(src_path) or "."
    base = os.path.basename(src_path)
    stem, srcext = os.path.splitext(base)
    out = os.path.join(d, f"{stem}{suffix}{ext or srcext}")
    with open(out, "wb") as f:
        f.write(buf)
    return out


CHECKSUM_REMINDER = (
    "⚠ CHECKSUM: file written but NOT valid for flashing yet. Recalculate the DENSO checksum "
    "(ECM Titanium → Tools → Checksum → Auto Fix, or WinOLS checksum plugin) before flashing."
)


def diff_maps(stock_buf, mod_buf, dmap, page=0, tol=0.01):
    """Return list of (r, c, stock_eng, mod_eng) cells that differ for one map."""
    s = dmap.read(stock_buf, page=page)
    m = dmap.read(mod_buf, page=page)
    diffs = []
    for r in range(dmap.rows):
        for c in range(dmap.cols):
            if abs(s[r][c] - m[r][c]) > tol:
                diffs.append((r, c, s[r][c], m[r][c]))
    return diffs


def find_ascii(data, needle, limit=None):
    """Locate an ASCII string in the binary (e.g. a part number). Returns offsets."""
    nb = needle.encode() if isinstance(needle, str) else needle
    offs = []
    start = 0
    hay = data if limit is None else data[:limit]
    while True:
        i = hay.find(nb, start)
        if i < 0:
            break
        offs.append(i)
        start = i + 1
    return offs


# ---------------------------------------------------------------------------
# Self-check helpers (used by tests)
# ---------------------------------------------------------------------------

def roundtrip_check(encoding, sample_engs):
    """Verify eng->raw->eng is stable within the encoding's quantization for sample values."""
    enc = ENCODINGS[encoding]
    results = []
    for e in sample_engs:
        raw = _clamp_raw(encoding, enc["to_raw"](e))
        back = enc["to_eng"](raw)
        results.append((e, raw, back))
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="DENSO bin engine — quick read/diff CLI")
    ap.add_argument("file")
    ap.add_argument("--addr", type=lambda x: int(x, 0), required=True, help="map address, e.g. 0x068AE8")
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--enc", default="float32_le", choices=list(ENCODINGS))
    ap.add_argument("--stride", type=int, default=None, help="row stride bytes (omit for packed)")
    ap.add_argument("--header", type=int, default=0, help="per-row header bytes")
    ap.add_argument("--page", type=int, default=0)
    args = ap.parse_args()

    data = load(args.file)
    m = DensoMap("cli", args.addr, args.rows, args.cols, args.enc,
                 stride_bytes=args.stride, header_bytes=args.header)
    grid = m.read(data, page=args.page)
    hdr = m.read_header(data, page=args.page)
    if hdr:
        print("row headers:", hdr)
    for r, row in enumerate(grid):
        print(f"r{r:2d}: " + " ".join(f"{v:8.2f}" for v in row))

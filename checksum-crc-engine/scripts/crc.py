#!/usr/bin/env python3
"""
crc.py — checksum/CRC engine for the checksum-crc-engine skill.

Pure Python 3, no dependencies. Computes the DATA-LEVEL checksums you can legitimately recompute
after editing an ECU cal region, and is honest about the ones you cannot (multi-layer program CRCs,
RSA/secure-boot signatures -> use WinOLS plugin / ECM Titanium / the flash tool).

Run `python crc.py --selftest` — every CRC preset is checked against the standard "123456789"
check value, and the sum/block logic is round-tripped.
"""

import os
import shutil


# --------------------------------------------------------------------------- #
# Generic CRC (bitwise, supports reflection) + presets
# --------------------------------------------------------------------------- #

def _reflect(value, width):
    out = 0
    for i in range(width):
        if value & (1 << i):
            out |= 1 << (width - 1 - i)
    return out


class CrcModel:
    """Rocksoft model: width, poly, init, refin, refout, xorout."""

    def __init__(self, width, poly, init, refin, refout, xorout):
        self.width = width
        self.poly = poly
        self.init = init
        self.refin = refin
        self.refout = refout
        self.xorout = xorout
        self.mask = (1 << width) - 1
        self.topbit = 1 << (width - 1)

    def compute(self, data):
        crc = self.init & self.mask
        for byte in data:
            b = _reflect(byte, 8) if self.refin else byte
            crc ^= (b << (self.width - 8)) & self.mask
            for _ in range(8):
                if crc & self.topbit:
                    crc = ((crc << 1) ^ self.poly) & self.mask
                else:
                    crc = (crc << 1) & self.mask
        if self.refout:
            crc = _reflect(crc, self.width)
        return (crc ^ self.xorout) & self.mask


# (width, poly, init, refin, refout, xorout)
CRC_PRESETS = {
    "CRC-32":             CrcModel(32, 0x04C11DB7, 0xFFFFFFFF, True,  True,  0xFFFFFFFF),
    "CRC-32/MPEG-2":      CrcModel(32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0x00000000),
    "CRC-16/CCITT-FALSE": CrcModel(16, 0x1021,     0xFFFF,     False, False, 0x0000),
    "CRC-16/XMODEM":      CrcModel(16, 0x1021,     0x0000,     False, False, 0x0000),
    "CRC-16/MODBUS":      CrcModel(16, 0x8005,     0xFFFF,     True,  True,  0x0000),
    "CRC-16/ARC":         CrcModel(16, 0x8005,     0x0000,     True,  True,  0x0000),
}


def crc(preset_name, data):
    if preset_name not in CRC_PRESETS:
        raise ValueError(f"unknown CRC preset '{preset_name}'. Known: {list(CRC_PRESETS)}")
    return CRC_PRESETS[preset_name].compute(bytes(data))


def identify_crc(data, expected_value):
    """Return the list of preset names whose CRC over `data` equals `expected_value`.

    Use against a known-good (tool-fixed) reference to identify an unknown checksum. Empty list =>
    not a standard CRC (likely proprietary/multi-layer -> use a dedicated tool)."""
    return [name for name, m in CRC_PRESETS.items() if m.compute(bytes(data)) == expected_value]


# --------------------------------------------------------------------------- #
# Simple sum / xor checksums
# --------------------------------------------------------------------------- #

def sum_checksum(data, word=1, endian="little"):
    """Sum of bytes (word=1), 16-bit words (word=2), or 32-bit words (word=4), modulo the word size."""
    data = bytes(data)
    if word == 1:
        return sum(data) & 0xFF
    if word not in (2, 4):
        raise ValueError("word must be 1, 2, or 4")
    mask = (1 << (word * 8)) - 1
    total = 0
    for i in range(0, len(data) - (len(data) % word), word):
        total += int.from_bytes(data[i:i + word], endian)
    return total & mask


def xor8(data):
    x = 0
    for b in bytes(data):
        x ^= b
    return x


# --------------------------------------------------------------------------- #
# ChecksumBlock: verify / repair a stored checksum over defined ranges
# --------------------------------------------------------------------------- #

class ChecksumBlock:
    """
    Describe a data-block checksum so it can be verified and repaired.

    data_ranges : list of (start, end) byte ranges (end-exclusive) the checksum COVERS. The store
                  location should normally be excluded from the covered range (or the checksum would
                  depend on itself). If overlap is unavoidable, the bytes at store_offset are treated
                  as zero during computation.
    store_offset: where the checksum value is written.
    store_size  : 1/2/4 bytes.
    endian      : byte order of the stored value.
    algo        : 'sum8' | 'sum16' | 'sum32' | 'xor8' | 'crc:<PRESET NAME>'
    mode        : 'store_value'      -> store the computed checksum
                  'store_negsum'     -> store two's complement (so region+stored == 0 mod size)
                  'store_complement' -> store one's complement (bitwise NOT)
    """

    def __init__(self, data_ranges, store_offset, store_size, endian, algo, mode="store_value"):
        self.data_ranges = data_ranges
        self.store_offset = store_offset
        self.store_size = store_size
        self.endian = endian
        self.algo = algo
        self.mode = mode

    def _covered_bytes(self, buf):
        out = bytearray()
        for (s, e) in self.data_ranges:
            chunk = bytearray(buf[s:e])
            # zero out the store location if it falls inside this covered range
            if s <= self.store_offset < e:
                for i in range(self.store_size):
                    idx = self.store_offset - s + i
                    if 0 <= idx < len(chunk):
                        chunk[idx] = 0
            out += chunk
        return bytes(out)

    def _raw_checksum(self, data):
        a = self.algo
        if a == "sum8":
            return sum_checksum(data, 1)
        if a == "sum16":
            return sum_checksum(data, 2, self.endian)
        if a == "sum32":
            return sum_checksum(data, 4, self.endian)
        if a == "xor8":
            return xor8(data)
        if a.startswith("crc:"):
            return crc(a[4:], data)
        raise ValueError(f"unknown algo '{a}'")

    def _store_value(self, data):
        mask = (1 << (self.store_size * 8)) - 1
        base = self._raw_checksum(data) & mask
        if self.mode == "store_value":
            return base
        if self.mode == "store_negsum":
            return (-base) & mask
        if self.mode == "store_complement":
            return (~base) & mask
        raise ValueError(f"unknown mode '{self.mode}'")

    def compute(self, buf):
        return self._store_value(self._covered_bytes(buf))

    def read_stored(self, buf):
        b = bytes(buf[self.store_offset:self.store_offset + self.store_size])
        return int.from_bytes(b, self.endian)

    def verify(self, buf):
        """Return (ok, computed, stored)."""
        computed = self.compute(buf)
        stored = self.read_stored(buf)
        return (computed == stored, computed, stored)

    def repair(self, buf):
        """Write the correct checksum into store_offset (buf must be a bytearray). Returns the value."""
        if not isinstance(buf, bytearray):
            raise TypeError("repair needs a bytearray")
        value = self.compute(buf)
        buf[self.store_offset:self.store_offset + self.store_size] = \
            value.to_bytes(self.store_size, self.endian)
        return value


# --------------------------------------------------------------------------- #
# File helpers
# --------------------------------------------------------------------------- #

def load(path):
    with open(path, "rb") as f:
        return bytearray(f.read())


def save(buf, src_path, suffix="_cksum", out_dir=None):
    d = out_dir or os.path.dirname(src_path) or "."
    base = os.path.basename(src_path)
    stem, ext = os.path.splitext(base)
    out = os.path.join(d, f"{stem}{suffix}{ext}")
    with open(out, "wb") as f:
        f.write(buf)
    return out


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _selftest():
    check = b"123456789"
    expected = {
        "CRC-32": 0xCBF43926,
        "CRC-32/MPEG-2": 0x0376E6E7,
        "CRC-16/CCITT-FALSE": 0x29B1,
        "CRC-16/XMODEM": 0x31C3,
        "CRC-16/MODBUS": 0x4B37,
        "CRC-16/ARC": 0xBB3D,
    }
    for name, exp in expected.items():
        got = crc(name, check)
        assert got == exp, f"{name}: got {got:#06x} expected {exp:#06x}"

    # identify_crc round-trip
    assert "CRC-32" in identify_crc(check, 0xCBF43926)
    assert identify_crc(check, 0x12345678) == []

    # sums
    assert sum_checksum(b"\x01\x02\x03", 1) == 6
    assert sum_checksum(b"\x01\x00\xFF\x00", 2, "little") == 0x0100
    assert xor8(b"\x0F\xF0") == 0xFF

    # ChecksumBlock verify/repair, sum32 store_value with store location inside covered range
    buf = bytearray(0x20)
    for i in range(0x1C):
        buf[i] = (i * 7) & 0xFF
    blk = ChecksumBlock([(0x00, 0x20)], store_offset=0x1C, store_size=4,
                        endian="little", algo="sum32", mode="store_value")
    blk.repair(buf)
    ok, comp, stored = blk.verify(buf)
    assert ok and comp == stored, (ok, comp, stored)

    # store_negsum: region (covered, with store zeroed) + stored == 0 mod 2^32
    blk2 = ChecksumBlock([(0x00, 0x20)], store_offset=0x1C, store_size=4,
                         endian="little", algo="sum32", mode="store_negsum")
    blk2.repair(buf)
    ok2, comp2, stored2 = blk2.verify(buf)
    assert ok2, (ok2, comp2, stored2)
    covered_sum = sum_checksum(blk2._covered_bytes(buf), 4, "little")
    assert (covered_sum + stored2) & 0xFFFFFFFF == 0

    # editing a covered byte must break verification (proves it actually covers the data)
    buf[0x05] ^= 0xFF
    assert not blk2.verify(buf)[0]

    print("crc.py self-test: ALL PASS")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ECU checksum/CRC engine")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--crc", help="CRC preset name, e.g. 'CRC-32'")
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--end", type=lambda x: int(x, 0), default=None)
    args = ap.parse_args()

    if args.selftest:
        _selftest()
    elif args.file and args.crc:
        data = load(args.file)
        end = args.end if args.end is not None else len(data)
        print(f"{args.crc} over [{args.start:#x},{end:#x}) = {crc(args.crc, data[args.start:end]):#0x}")
    else:
        ap.print_help()

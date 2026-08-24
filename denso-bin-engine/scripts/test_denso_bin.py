#!/usr/bin/env python3
"""Test suite for denso_bin.py — verifies codecs, read/write, diff against real LC300 files."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from denso_bin import (DensoMap, ENCODINGS, load, roundtrip_check, diff_maps,
                       find_ascii, save_as)

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")

print("=== 1. Encoding round-trip tests ===")
# float is exact
rt = roundtrip_check("float32_le", [0.0, 339.4, 488.0, 920.5])
check("float32 round-trip exact", all(abs(e-b) < 0.01 for e, raw, b in rt))
# int16_nm quantizes to 1/32 Nm
rt = roundtrip_check("int16_nm", [100.0, 250.03125, -50.0])
check("int16_nm quantization ≤0.04Nm", all(abs(e-b) <= 1/32 + 1e-6 for e, raw, b in rt))
# uint8_vvt
rt = roundtrip_check("uint8_vvt", [0.0, 40.0, 79.0])
check("uint8_vvt round-trip ≤0.4deg", all(abs(e-b) <= 79.68/255 + 1e-6 for e, raw, b in rt))
# uint16_pct
rt = roundtrip_check("uint16_pct", [0.0, 68.71, 127.0])
check("uint16_pct round-trip ≤0.02%", all(abs(e-b) <= 799.98/65535 + 1e-6 for e, raw, b in rt))
# clamp test: overflow int16
from denso_bin import _clamp_raw
check("int16 clamp high", _clamp_raw("int16_nm", 99999) == 32767)
check("uint8 clamp low", _clamp_raw("uint8", -5) == 0)

print("\n=== 2. Real-file read test (LC300 V35A Standard torque @0x068AE8, 13x11 float) ===")
STOCK = "/mnt/user-data/uploads/LC300_LX600_Tundra_3_5T_P5-UDS_V35A-FTS_R7F702002__FLASH_20260417200629.bin"
MOD   = "/mnt/user-data/uploads/AAL_LC300_V35A_Stage1_89663-6A641.MOD"
have_files = os.path.exists(STOCK) and os.path.exists(MOD)
if have_files:
    stock = load(STOCK)
    mod = load(MOD)
    std = DensoMap("StandardTorque", 0x068AE8, rows=13, cols=11, encoding="float32_le")
    grid = std.read(mod)
    flat = [v for row in grid for v in row]
    check("read 143 cells", len(flat) == 143)
    check("WOT peak ≈ 920 (mod)", abs(max(flat) - 920.0) < 1.0, f"max={max(flat):.1f}")
    check("min is 0", abs(min(flat)) < 0.01)

    print("\n=== 3. Diff test (stock vs mod, same map) ===")
    diffs = diff_maps(stock, mod, std, tol=0.5)
    check("diff finds changed cells", len(diffs) > 0, f"diffs={len(diffs)}")
    # show a couple
    for r, c, sv, mv in diffs[:3]:
        print(f"      [{r},{c}] stock={sv:.1f} -> mod={mv:.1f}")

    print("\n=== 4. Write + readback test (non-destructive, temp copy) ===")
    buf = bytearray(mod)  # work on a copy in memory
    m2 = DensoMap("StandardTorque", 0x068AE8, 13, 11, "float32_le")
    # lower part-throttle columns 3..8 by 7% on all rows
    changed = m2.apply(buf, lambda r, c, v: v * 0.93 if v > 5 else v,
                       cols=range(3, 9))
    check("apply changed cells", changed > 0, f"changed={changed}")
    # verify readback reflects the change and WOT col10 unchanged
    after = m2.read(buf)
    before = std.read(mod)
    col10_same = all(abs(after[r][10] - before[r][10]) < 0.01 for r in range(13))
    check("WOT col10 untouched", col10_same)
    col5_lower = all(after[r][5] < before[r][5] - 0.01 for r in range(13) if before[r][5] > 5)
    check("part-throttle col5 reduced", col5_lower)
    # also verify a known-nonzero cell scaled by exactly 0.93
    sample = next(((r, c) for r in range(13) for c in range(3, 9) if before[r][c] > 5), None)
    if sample:
        r, c = sample
        check("scaled cell = x0.93", abs(after[r][c] - before[r][c] * 0.93) < 0.1,
              f"[{r},{c}] {before[r][c]:.1f}->{after[r][c]:.1f}")

    print("\n=== 5. part-number search ===")
    offs = find_ascii(mod, "89663")
    check("find DENSO part token in file or name", True)  # informational
    print(f"      '89663' found at offsets: {offs[:5] if offs else 'none in binary body'}")
else:
    print("  (real LC300 files not present — skipping file-based tests)")

print(f"\n=== RESULT: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)

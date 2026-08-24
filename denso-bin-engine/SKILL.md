---
name: denso-bin-engine
description: >-
  Read, edit, and diff DENSO ECU binary calibration files (BIN/ORI/MOD) by address + dimensions +
  encoding, instead of hand-writing a one-off byte parser every time. Encapsulates verified DENSO
  map layouts and encoding formulas (float32, int16/32=Nm, uint8 VVT ×79.68/255, uint8/512 factor,
  uint16 ×799.98/65535 percent, raw/32 degrees) plus packed/strided/multi-page layouts. Use this
  skill WHENEVER the task involves a DENSO .bin/.ori/.mod and you need to read a map's values, apply
  a Stage1/Stage2 edit and write a new bin, diff an ORI against a MOD, locate a part number, or
  decode/encode a map cell. Triggers on: "modify this DENSO bin", "apply Stage1 to this file",
  "read the torque/VVT/load/spark map at 0xXXXX", "diff ORI vs MOD", "what does this raw value mean
  in Nm/degrees", "output the tuned bin". Covers Toyota/Lexus DENSO (2TR-FE, 3UR-FE, V35A, etc.).
  Do not hand-roll struct parsing — use this engine so the byte math is correct and tested.
---

# DENSO Bin Engine

A tested read/write/diff engine for DENSO ECU binaries. The point: stop re-deriving byte offsets
and scaling formulas for every project. Describe a map once (address, rows, cols, encoding, layout)
and the engine handles decoding, region edits, write-back, and diffing.

The encodings here are **verified on real projects** (Tundra 3UR-FE, Prado/Hilux 2TR-FE, LC300
V35A) — see `references/verified_encodings.md`. The test suite (`scripts/test_denso_bin.py`) runs
them against real files; run it after any change to the engine.

## When to reach for this

- Reading any DENSO map (torque, load, VVT, spark, injection, limiter) into engineering units.
- Applying a Stage1/Stage2 plan to a bin and writing `<name>_Stage1.bin`.
- Diffing an original (ORI) against a modified (MOD) file, map by map.
- Decoding a single raw cell value to Nm / degrees / % / factor (or the reverse).
- Locating a part number / string inside the binary.

## The one rule that overrides everything: checksum

This engine **never** recomputes the DENSO proprietary checksum. After any write the file is
**invalid for flashing** until the checksum is recalculated in ECM Titanium
(Tools → Checksum → Auto Fix) or an equivalent tool. Every write path prints this reminder, and you
must surface it to the user in the final report. Never tell a user a written bin is ready to flash.

## Core workflow

### 1. Load
```python
from denso_bin import DensoMap, load, save_as, diff_maps, find_ascii, CHECKSUM_REMINDER
buf = load("AAL_LC300_V35A.bin")          # returns a bytearray (mutable)
```

### 2. Describe the map
Pick the layout style (see `references/verified_encodings.md` for which DENSO maps use which):

```python
# Packed float map (LC300 V35A standard-condition torque, 13 rows x 11 cols):
std = DensoMap("StandardTorque", address=0x068AE8, rows=13, cols=11, encoding="float32_le")

# Strided int16/Nm map with a 2-byte row header (Tundra Optimal Torque, 26-byte stride):
torq = DensoMap("OptTorque", 0x02A676, rows=?, cols=12, encoding="int16_nm",
                stride_bytes=26, header_bytes=2)

# Multi-page map (11 stacked pages, page stride = 143 floats):
mp = DensoMap("StdAllPages", 0x068AE8, 13, 11, "float32_le", page_stride=143*4, pages=11)
```

### 3. Read / inspect
```python
grid = std.read(buf)                 # engineering units, list[rows][cols]
raws = std.read(buf, engineering=False)
hdr  = torq.read_header(buf)         # per-row header (often an RPM axis) when header_bytes>0
```

### 4. Edit a region (the safe way)
`apply(fn, rows=, cols=, page=)` calls `fn(r, c, current_value) -> new_value` over a sub-region and
returns the count of changed cells. Return the value unchanged (or `None`) to skip a cell.

```python
# Soften part-throttle (cols 3-8) by 7%, leave WOT column alone, skip near-zero cells:
changed = std.apply(buf, lambda r, c, v: v * 0.93 if v > 5 else v, cols=range(3, 9))

# Raise a torque-limiter region by a flat +30 Nm only above 2000 rpm rows:
changed = lim.apply(buf, lambda r, c, v: v + 30, rows=range(4, lim.rows))
```

Edit one cell directly when needed: `std.write_cell(buf, r, c, eng_value, page=0)`.

### 5. Save with a suffix
```python
out = save_as(buf, "AAL_LC300_V35A.bin", suffix="_Stage1")   # -> AAL_LC300_V35A_Stage1.bin
print(CHECKSUM_REMINDER)
```

### 6. Diff ORI vs MOD
```python
stock = load("stock.bin"); mod = load("tuned.bin")
for r, c, sv, mv in diff_maps(stock, mod, std, tol=0.5):
    print(f"[{r},{c}] {sv:.1f} -> {mv:.1f}")
```

### 7. Find a part number / string
```python
find_ascii(buf, "89663")     # returns byte offsets where the token appears
```

## Encodings available

| name | bytes | meaning | verified on |
|------|-------|---------|-------------|
| `float32_le` | 4 | IEEE-754 little-endian | LC300 V35A Gen4 torque/load |
| `int16_nm` | 2 | signed /32 = Nm | Tundra Optimal Torque |
| `uint16_pct` | 2 | ×799.98/65535 = % | Tundra Requested Load #1 |
| `uint8_vvt` | 1 | ×79.68/255 = degrees | Tundra Intake VVT |
| `uint8_fac` | 1 | /512 = factor | Tundra Injection Enrichment |
| `raw_deg32` | 1 | /32 = degrees | DENSO spark advance base |
| `int16_le`,`uint16_le`,`uint8`,`float32_be` | — | raw numeric | generic |

> **Endianness — check the core before assuming `_le`.** The verified encodings above come from
> Toyota/Lexus DENSO on Renesas RH850/R7F70x (**little-endian**), which is why they're named `_le`.
> DENSO is *not* uniformly LE: older DENSO and DENSO-adjacent ECUs on **Renesas SH-2/SH-2A
> (SH7055/SH7058) are BIG-endian** — Subaru, some Nissan/Mitsubishi. Confirm the core via
> `multi-vendor-ecu-engine` first; on a BE core use the `_be` encodings (or `bosch-conti-bin-engine`
> with `endian="big"`). Wrong endianness is the #1 cause of "the values look insane".

Add new encodings in `denso_bin.py` `ENCODINGS` (give `to_eng`/`to_raw` lambdas), then add a
round-trip test. See `references/verified_encodings.md` for full layout details and how to confirm
an unknown map's encoding before trusting it.

## Layout styles

- **Packed**: contiguous row-major cells. `stride_bytes=None` (defaults to `cols*width`).
- **Strided**: each row = `header_bytes` header + `cols` cells, row pitch = `stride_bytes`. Use when
  rows carry an embedded axis byte (e.g. RPM header).
- **Multi-page**: stacked identical grids; set `page_stride` (bytes between pages) and `pages`.

## Verifying an unknown map before editing

Never trust an address/encoding you haven't confirmed. Procedure:
1. Read the region with the candidate encoding and sanity-check the engineering values (torque in a
   plausible Nm band, VVT 0–50°, lambda ~0.7–1.0, etc.).
2. Cross-check against a tool screenshot (WinOLS/ECM Titanium) if available — values must match.
3. For strided maps, confirm `read_header()` produces a monotonic axis (RPM should increase).
4. Only then apply edits. If values look wrong, the encoding or stride is wrong — fix it first.

## Safety checklist (always include in the final report)

- ⚠ Checksum NOT recalculated — must Auto-Fix in ECM Titanium before flashing.
- Keep the original (ORI) untouched as the recovery baseline; write to a new suffixed file.
- TCU torque-monitor limits are a separate file — a torque bump here may need TCU sync.
- Clear knock learning (KAM) after flashing so old adaptation doesn't mask the new calibration.
- Verify air-mass modeling error against the Banish tiers (<5% acceptable / <3% ideal / <1% OEM) after any load/torque change; state which tier you hit.

## Running the tests
```bash
python scripts/test_denso_bin.py
```
All cases should pass (encoding round-trips, real-file read, diff, write+readback, multi-page math).

## Install / portability
Pure Python 3, no dependencies. In Claude Desktop place the folder in your skills directory; in
Codex/Cursor copy the folder into the project and reference `SKILL.md` from your agent config —
`scripts/denso_bin.py` can be imported or run from the terminal in any of these tools.

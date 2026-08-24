---
name: bosch-conti-bin-engine
description: >-
  Read, edit, and diff Bosch and Continental/Siemens ECU or TCU binaries using A2L style metadata: address, dimensions, datatype, factor, offset, and endianness. Covers Bosch ME7, ME9, MED9, MED17, MEVD17, MEDG17, EDC15, EDC16, EDC17, MD1, MG1 and Continental or Siemens SIMOS, PCR2.1, SID, EMS, GPEC, SIM2K. Use for Bosch or Conti bin, ori, mod work: reading map values, axes, decoding cells, applying Stage edits, writing a modified binary, or diffing ORI vs MOD with factor and offset scaling. Does not recompute modern ECU checksums; route checksum work to checksum-crc-engine or a WinOLS checksum plugin. Pair with winols-scaling-converter and ecu-map-analysis.
---

# Bosch / Conti Bin Engine

The same idea as `denso-bin-engine`, generalized to the **A2L linear-scaling** world that Bosch and
Continental/Siemens live in. Describe a map once — address, dims, datatype, endianness, factor,
offset, layout — and the engine decodes, edits regions, writes back, and diffs. The byte math is
tested so you don't re-derive it per project.

Run under `ecu-calibration-doctrine`. This is its executor for Bosch/Conti binary work
(doctrine §6 "bin/二进制工作": OEM-file minimal-change diff thinking; checksum is the delivery gate).

## The model: A2L linear scaling
Unlike DENSO's fixed formulas, Bosch/Conti values come from the A2L/DAMOS as:
```
physical = raw * factor + offset          (COMPU_METHOD RAT_FUNC / linear)
```
So a map needs five things, all of which come from the A2L/DAMOS or `winols-scaling-converter`:
**address, dims (rows×cols), datatype (u8/s8/u16/s16/u32/s32/f32), endianness, (factor, offset)**.
The engine does the rest. Axes (X/Y breakpoints) are *separate arrays* the Z-map indexes — describe
them the same way.

> **Endianness**: Bosch/Conti data is **little-endian** (Infineon TriCore TC17xx/27x/29x/3xx,
> C167, ST10 are all LE). Set `endian="big"` only when `multi-vendor-ecu-engine` tells you the file
> is on a big-endian core (Renesas SH on Nissan/Subaru, Freescale PowerPC on some Marelli/Visteon).

## The one rule that overrides everything: checksum
This engine **never** recomputes a checksum. Bosch/Conti integrity is multi-layer — EDC17/MED17/
MEVD17 have several cal CRC/sum blocks plus a program checksum; MD1/MG1 add RSA/secure-boot
signatures that **cannot** be forged. After any write the file is **invalid for flashing** until:
- the **WinOLS checksum plugin** or **ECM Titanium Auto-Fix** fixes the cal/program checksums, and
- the **flash tool's auto-checksum** (KESS3/Autotuner/CMD/PCMflash) handles the rest on write.

Every write path prints this reminder. Hand off to `checksum-crc-engine` for the recomputable
data-block layers and **say plainly** the file is not flash-ready until the tool finishes. Never tell
a user a written Bosch/Conti bin is ready to flash.

## Core workflow
```python
from ecu_bin import EcuMap, Axis, load, save_as, diff_maps, find_ascii, CHECKSUM_REMINDER

buf = load("EDC17_MyCar.ori")

# A torque-limiter map: 16x16 u16 LE, A2L factor 0.1 offset 0 (=> Nm), packed:
torq = EcuMap("DrvTrqLim", address=0x1A4C20, rows=16, cols=16,
              datatype="u16", endian="little", factor=0.1, offset=0.0)

# Its axes (breakpoints), each a separate array:
xax = Axis("rpm_axis",  0x1A4B00, count=16, datatype="u16", endian="little", factor=1.0)
yax = Axis("load_axis", 0x1A4B40, count=16, datatype="u16", endian="little", factor=0.1)

grid = torq.read(buf)                 # engineering units list[rows][cols]
print(xax.read(buf), yax.read(buf))   # decoded breakpoints

# Raise the limiter +12% only in the upper-rpm half, skip near-zero cells:
changed = torq.apply(buf, lambda r, c, v: v*1.12 if v > 50 else v, rows=range(8, 16))

# Diff ORI vs MOD — 前提是你【已经知道】要看哪张表:
mod = load("EDC17_MyCar.mod")
for r, c, sv, mv in diff_maps(buf, mod, torq, tol=0.5):
    print(f"[{r},{c}] {sv:.1f} -> {mv:.1f}")
# 还【不知道】改了哪些表 → 不要在这里手写循环，交 reference-tune-porting/scripts/diff_cluster.py
# （整文件 diff → 聚簇 → 归属到命名 MAP → 候选算子 → 未归属残留）

out = save_as(mod, "EDC17_MyCar.mod", suffix="_Stage1")
print(CHECKSUM_REMINDER)
```

## Datatypes
| name | bytes | signed | note |
|------|-------|--------|------|
| `u8`/`s8` | 1 | u/s | common for %, factors |
| `u16`/`s16` | 2 | u/s | most Bosch maps (LE) |
| `u32`/`s32` | 4 | u/s | counters, large ranges |
| `f32` | 4 | float | newer MG1/MD1, some Conti |

`endian` = `"little"` (default, all Infineon cores) or `"big"` (SH/PowerPC platforms — see
`multi-vendor-ecu-engine`). Scaling is always `phys = raw*factor + offset`; set `factor=1, offset=0`
for raw counts.

## Layout styles (same as denso-bin-engine)
- **Packed** (default): contiguous row-major, `stride_bytes=None`.
- **Strided**: per-row header byte(s) + cells; set `stride_bytes` and `header_bytes`.
- Axes are independent `Axis` arrays, not embedded in the Z-map (the Bosch/A2L norm) — read them
  separately and confirm they're monotonic before trusting the map orientation.

## Where the factor/offset come from
You must supply correct A2L scaling. Sources, in order of trust:
1. The matching **A2L/DAMOS** (use `ecu-calib-finder` to locate one for this ECU/SW).
2. **WinOLS** map properties (it shows factor/offset/datatype) — route conversions to
   `winols-scaling-converter`.
3. Inference from physical plausibility (last resort) — verify against a tool screenshot before edit.

## Verifying an unknown map before editing (doctrine: never trust an unconfirmed address)
1. Read with the candidate datatype/endian/scaling; sanity-check the band (torque tens–hundreds Nm,
   rail pressure ~hundreds–2000+ bar, SOI a few °BTDC, boost in mbar/kPa, lambda ~0.7–1.0).
2. Confirm axes are monotonic (`Axis.read` increasing).
3. Match a WinOLS/INCA screenshot cell-for-cell if available.
4. Wrong-looking values ⇒ wrong datatype/endian/factor/offset ⇒ fix the description, not the data.
5. Only then edit. Place the map first with `ecu-map-analysis` (main control / limiter / correction).

## Safety checklist (always in the final report)
- ⚠ Checksum NOT recalculated — multi-layer + possibly signed; use WinOLS plugin / tool auto-checksum
  before flashing (hand off `checksum-crc-engine`). Never call it flash-ready.
- Keep the ORI untouched as recovery baseline; write to a new suffixed file.
- A torque/limiter bump may need TCU torque-monitor sync (`tcu-shift-calibration`, doctrine §5).
- Verify air-mass model within ±3% after load/torque changes (`air-fuel-model-builder`).
- Clear adaptive/knock learning after flashing.

## Running the tests
```bash
python scripts/ecu_bin.py --selftest
```
All cases should pass (LE/BE round-trips, factor/offset, packed & strided read, apply, diff, axis).
See `references/bosch_conti_platforms.md` for per-platform datatype/endianness/checksum conventions.

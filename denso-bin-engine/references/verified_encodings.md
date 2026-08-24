# Verified DENSO Encodings & Map Layouts

These layouts/formulas were confirmed on real calibration files. Treat them as **starting
hypotheses for the same platform/SW**, and always re-verify on a new file (axes monotonic, values
in a plausible band, match a tool screenshot) before editing.

> Reminder: a map name or pack label is not proof. Confirm by reading values and checking they make
> physical sense.

## LC300 / LX600 / Tundra — V35A-FTS, DENSO Gen4 (R7F702002)

8 MB flash. Torque/load maps are **packed float32 little-endian**, row-major.

| Map | Address | Dims | Encoding | Layout | Notes |
|-----|---------|------|----------|--------|-------|
| Torque request standard condition | 0x068AE8 | 13×11 | `float32_le` | packed, 11 pages (page stride = 143×4) | rows=RPM, cols=demand; WOT peak ~920 Nm (over-declared, clamped by limiters) |
| Torque request Sport condition | 0x0671BC | 13×11 | `float32_le` | packed, 11 pages | page0 == standard; pages 1–10 differ slightly |
| Take-off torque #1 | 0x06216C | 9×41 | `float32_le` | packed | launch torque, low end |
| Take-off torque #2 | 0x06280C | 21×44 | `float32_le` | packed | launch torque, higher resolution |
| Requested engine load #2 (mg/stk) | 0x0C2190 | 22×25 | `float32_le` | packed, 3 pages | pedal→air-mass; cols=demand, rows=RPM |
| Limiter of Max torque #1/#2 | 0x066BAC / 0x066D90 | 9×11 | `float32_le` | packed | flattened to 800 Nm in the Stage1 studied |

Rev limiter constant (single float) was at 0x09E028; boost limiter plateaus near 0x09E33C /
0x09E570 / 0x09E910 (kPa absolute).

## Tundra 5.7 — 3UR-FE, DENSO (89663-0CM51, ~1.0 MB)

These use **strided rows with embedded header bytes** — classic older-DENSO layout.

| Map | Address | Encoding | Stride | Header | Cells |
|-----|---------|----------|--------|--------|-------|
| Optimal Engine Torque #1 | 0x02A676 | `int16_nm` (signed /32 = Nm) | 26 bytes | 2-byte LE int16 | 12 int16 |
| Optimal Engine Torque #2 | 0x02BFCA | `int16_nm` | 26 bytes | 2-byte | 12 int16 |
| Intake VVT | 0x02A27A | `uint8_vvt` (×79.68/255 = °) | 13 bytes | 1-byte (RPM = raw×50) | 12 bytes |
| Injection Enrichment #1 | 0x00A6C0 | `uint8_fac` (/512 = factor) | packed | none | 13×9 byte array |
| Requested Engine Load #1 | 0x02A856 | `uint16_pct` (×799.98/65535 = %) | 34 bytes | 2-byte | 16 uint16 |

So a Tundra torque map is:
```python
DensoMap("OptTorque1", 0x02A676, rows=<n>, cols=12, encoding="int16_nm",
         stride_bytes=26, header_bytes=2)
```
and VVT:
```python
DensoMap("IntakeVVT", 0x02A27A, rows=<n>, cols=12, encoding="uint8_vvt",
         stride_bytes=13, header_bytes=1)
```

## Prado / Hilux 2700 — 2TR-FE, DENSO

| Map | Encoding | Notes |
|-----|----------|-------|
| Spark advance base | `raw_deg32` (raw/32 = degrees) | several tables: low-octane floor, high-octane base, ceiling |
| Optimal/Requested torque (Air% × RPM) | platform-dependent | confirm width on the actual file |
| Requested load / throttle (Air% out, clamp ~68.71%) | platform-dependent | the 68.71 clamp cells must stay locked |
| Calculated Load for Gearbox | platform-dependent | feeds TCU; adjust only to match power changes |

## How to confirm an unknown encoding

1. Pick the candidate (`float32_le` for newer Gen4; strided `int16_nm`/`uint8_*` for older DENSO).
2. Read the region; check the engineering values land in a sane band:
   - torque: tens to a few hundred Nm (forced induction higher)
   - VVT: 0–50°
   - spark: roughly 0–45°
   - lambda/factor: ~0.7–1.3
   - load %: 0–130%
3. For strided maps, `read_header()` should yield a monotonic axis (RPM increasing).
4. If a WinOLS/ECM Titanium screenshot exists, the decoded grid must match it cell-for-cell.
5. Wrong-looking values ⇒ wrong stride/header/encoding. Fix the description, not the data.

## Adding a new encoding
In `denso_bin.py`, add to `ENCODINGS`:
```python
"my_enc": dict(fmt="<H", width=2, to_eng=lambda r: r * SCALE + OFFSET,
               to_raw=lambda e: int(round((e - OFFSET) / SCALE))),
```
then add a `roundtrip_check("my_enc", [...])` assertion to the test suite.

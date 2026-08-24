---
name: multi-vendor-ecu-engine
description: >-
  Identify and decode ECU or TCU binaries from suppliers beyond Bosch and DENSO, including Continental, Siemens VDO, Vitesco, Temic, Marelli, Delphi, PHINIA, TRW, ZF, Mitsubishi Electric, Hitachi, Astemo, Visteon, Hyundai Mobis, Kefico, and uncertain Phoenix files. First determines MCU core, endianness, datatype, and checksum family, then routes byte work to bosch-conti-bin-engine or denso-bin-engine. Use when a file may be Marelli, Delphi, Hitachi, Nissan SH7058, Subaru, SIM2K, DCM3.7, MJD8F, IAW, Hyundai Mobis, 西门子, 马瑞利, 德尔福, 日立, or when decoded values look wrong. Checksum work routes to checksum-crc-engine or WinOLS.
---

# Multi-Vendor ECU Engine (identify → route)

The doctrine's first principle: **PCM 不在乎车标。处理器只有输入和输出。** A binary doesn't have a
"Marelli format" — it has an **MCU core** and an **endianness**, and the same A2L `phys = raw*factor +
offset` math works once those are right. This skill's whole job is to pin those down for the supplier
long tail, then route the byte work to the existing engines. **It owns no separate parser** (that
would create the double-source-of-truth conflict the doctrine warns about).

Run under `ecu-calibration-doctrine`.

## The one table that matters most: core → endianness
Get this wrong and every decoded value is garbage. Identify the core from the MCU part number on the
chip, the flash tool's ID, or the A2L.

| MCU core | Endianness | Seen on |
|----------|-----------|---------|
| Infineon **TriCore** (TC17xx, TC2xx AURIX, TC3xx) | **little** | Conti/Vitesco SIMOS/SID/EMS/GPEC/SIM2K, Delphi MT/DCM, newer Marelli, Hyundai |
| Infineon **C167 / ST10** | **little** | older Marelli IAW, EDC15/16-era |
| **Renesas SH-2/SH-2A** (SH7055, SH7058, SH72xx) | **BIG** | Nissan/Subaru/Mitsubishi (Hitachi & Mitsubishi Electric); **Honda/Keihin K/L-series (SH7058) → BIG** |
| Renesas **V850 / RH850** | usually **little** (configurable) | newer Renesas-based (incl. newest Honda/Keihin); confirm from A2L/vector table |
| Freescale/NXP **PowerPC** (MPC5xx, MPC55xx/57xx) | **BIG** | some Marelli (PPC), Visteon Ford PCM, older diesel |
| Freescale **HC / 68k** | **BIG** | legacy Visteon/Ford, older body/TCU; **older Honda/Keihin OBD1/OBD2a (68HC11) → BIG** |

> If you can't read the core from a sticker/tool, infer it: try little-endian first (most modern
> automotive), and if axes won't come out monotonic and values are insane, try big-endian. A correct
> endian makes axes monotonic and values land in physical bands — that's your confirmation.

## Per-supplier routing

### Continental / Siemens-VDO  → `bosch-conti-bin-engine` (little)
SIMOS (8/12/18/19), PCR2.1, SID (807/208/901…), EMS (3xxx), GPEC (2/3/4), SIM2K. TriCore, LE,
A2L factor/offset. Newer (SIMOS18/19, MD1/MG1-adjacent) are **signed/secure-boot** → checksum via
tool only. See `bosch-conti-bin-engine/references/bosch_conti_platforms.md`.

### Vitesco  → `bosch-conti-bin-engine` (little)
Continental Powertrain spun off as **Vitesco Technologies (2019)** — same platforms/conventions as
Conti powertrain (SIMOS/EMS successors, TriCore, LE). Treat as Continental for decoding; newer parts
are signed.

### Temic  → `bosch-conti-bin-engine` (little), often **TCU**
Legacy Continental (ex-Telefunken). Mostly **transmission/mechatronic** controllers (and older
body/engine). TriCore/ST10, LE. Torque/TCU work → coordinate with `tcu-shift-calibration`.

### Magneti Marelli / Marelli  → mind the core
- **IAW** (4-cyl gasoline Fiat/Alfa), older: **ST10/C167 → little**.
- **MJD / 8F / 9DF** (MultiJet diesel — "Marelli Diesel", Fiat/GM/Suzuki/Jeep): TriCore → **little**.
- Some Marelli run **Freescale PowerPC → BIG**. Confirm the core before decoding — Marelli is the
  classic "could be LE or BE" supplier. Route to `bosch-conti-bin-engine` with the right `endian`.

### Delphi (Delphi Technologies / now PHINIA)  → `bosch-conti-bin-engine`
- **MT** gasoline (MT80/MT86/MT92, GM/Opel/Chevrolet): Infineon TriCore → **little**.
- **DCM** diesel (DCM3.x, DCM6.x, DCM7.x — PSA/Hyundai/Renault/Ford): TriCore/MPC → mostly **little**,
  confirm. A2L factor/offset.

### TRW (now ZF)  → `bosch-conti-bin-engine`, engine + safety
- **TRW EMS** diesel/gasoline (PSA, SsangYong, Great Wall, JLR): TriCore → **little**.
- Also airbag/ABS/EPS modules (not power tuning). Engine torque bump → check TCU/safety torque
  monitor (doctrine §5 联动风险清单).

### Mitsubishi Electric  → endianness depends on core
- Older Mitsubishi/Evo & some Nissan/Mazda/Volvo: **Renesas SH → BIG** (decode big-endian!).
- Newer: Renesas RH850/V850 → usually little. Confirm. Route to `bosch-conti-bin-engine` with the
  decided `endian`.

### Hitachi (Hitachi Astemo)  → frequently **BIG-endian** ⚠
The classic gotcha. Nissan (and some Subaru/Infiniti) ECUs on **Renesas SH7055/SH7058 are
big-endian**. RomRaider/EcuTek/Nistune territory. Set `endian="big"` in `bosch-conti-bin-engine`.
Newer Hitachi on RH850 → usually little; confirm via monotonic axes.

### Visteon  → often **BIG-endian** (PowerPC/Ford)
Ex-Ford PCM supplier (also Mazda/JLR). Older Visteon/Ford run **Freescale PowerPC / HC → BIG**.
Black/Silver/Gold-box Ford = big-endian. Set `endian="big"`. Newer parts may be LE — confirm.

### Hyundai Mobis / Kefico  → `bosch-conti-bin-engine` (little)
Mobis = parent; **Kefico** builds the ECUs. Hyundai/Kia run a mix of:
- **Bosch MEDG17 / EDC17** (Bosch conventions),
- **Continental SIM2K** (SIM2K-2xx/3xx/5xx, TriCore, LE),
- **Kefico CPGDSH / CPEGD** (TriCore, LE).
All TriCore → **little**. Checksum is multi-layer; newer is signed → tool only.

### Keihin / Mikuni / Shindengen (Honda + powersports)  → route by core, then `powersports-ecu`
- **Honda car Keihin**: older 68HC → **BIG**; K/L-series Renesas **SH7058 → BIG**; newest RH850 → little.
  Decode via the core table, then `bosch-conti-bin-engine` (or big-endian handling).
- **Motorcycle/ATV/marine** (Keihin, Mikuni, Shindengen, Synerject, plus Denso/Marelli/Bosch on bikes):
  the byte work is the same core→endian→bin-engine routing, **but the control DOMAIN differs**
  (alpha-N air model, often non-torque-structured TPS-direct, per-gear/per-cylinder maps, sub-throttle,
  bike-specific flashing tools). For that domain reality use **`powersports-ecu`**; this skill only
  pins the endianness. Confirm the core from silicon — bike ECUs vary widely.

### "Phoenix"  → ⚠ identify before assuming
"Phoenix" is not an established mainstream ECU-supplier name in the way the others are. It may be a
platform/internal/tuning-tool label. **Don't assume conventions** — identify the file directly:
1. Read the MCU part number (chip/tool/A2L) → set endianness from the core table above.
2. Find the part-number/ASCII strings (`find_ascii`) and SW/HW IDs → run `ecu-calib-finder` to match
   it to a known supplier/platform.
3. Decode little-endian first; if garbage, big-endian. Monotonic axes + sane bands = correct.
Then route to the appropriate engine. If you still can't identify it, **say what's missing** rather
than guess (doctrine: 信息不足先说明缺什么).

## Workflow
1. **Identify**: core → endianness (table above) + datatype norm (u8/u16 mostly; s8/s16 for
   timing/correction; f32 only on AURIX/newest). Use `ecu-calib-finder` for SW/HW ID + A2L match.
2. **Decode**: hand address + dims + datatype + endian + factor/offset to `bosch-conti-bin-engine`
   (or `denso-bin-engine` for DENSO). Confirm axes monotonic + values in band.
3. **Analyze / edit**: `ecu-map-analysis` to classify the map, then edit via the engine.
4. **Checksum**: `checksum-crc-engine` for recomputable blocks; WinOLS plugin / flash-tool
   auto-checksum for multi-layer/signed. Never call a signed file flash-ready without the tool.

## Boundaries
- This skill decides endianness/datatype/checksum approach and routes — it does not parse bytes
  itself.
- Big-endian suppliers (Hitachi/Nissan-SH, Visteon-PPC, some Marelli/Mitsubishi) are the main reason
  this skill exists; getting endian right is its single highest-value output.
- When unsure of a badge ("Phoenix" or any unlabeled file), identify from the silicon, not the name.

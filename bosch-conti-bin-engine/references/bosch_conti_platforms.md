# Bosch / Continental(Siemens) Platform Conventions

Reference for `bosch-conti-bin-engine`. These are **conventions, not verified addresses** — always
confirm datatype/endianness/scaling against the A2L/DAMOS or a WinOLS screenshot before editing
(doctrine: never trust an unconfirmed map). The engine is badge-agnostic; this table just sets the
right defaults (datatype/endian/checksum approach) so the byte math starts correct.

> First-principle (doctrine #1): the PCM doesn't care about the badge — it has a core (MCU) and an
> endianness. Identify those first; the supplier name only narrows the likely defaults.

## Bosch — gasoline

| Platform | Typical MCU | Endian | Data norm | Checksum reality |
|----------|-------------|--------|-----------|------------------|
| ME7.x | Infineon C167 (16-bit) | little | u8/u16, factor/offset | cal sum blocks — WinOLS plugin handles |
| ME9.x / MED9.1 | Infineon TriCore TC1796 / ST10 | little | u8/u16 | cal CRC + program — WinOLS plugin |
| MED17 / MED17.x | Infineon TriCore TC1766/67/96/97 | little | u8/u16, some s16 | multi-layer cal+program CRC — WinOLS plugin |
| MEVD17 (N-series BMW) | Infineon TriCore TC1797 | little | u8/u16 | multi-layer — WinOLS plugin |
| MEDG17 (Hyundai/Kia gasoline) | Infineon TriCore | little | u8/u16 | multi-layer — WinOLS plugin |
| MG1 (B58/modern VAG/PSA) | Infineon AURIX TC27x/29x | little | u8/u16/**f32** | cal CRC + **RSA/secure boot** — tool only, signature NOT forgeable |

## Bosch — diesel

| Platform | Typical MCU | Endian | Data norm | Checksum reality |
|----------|-------------|--------|-----------|------------------|
| EDC15 | Infineon C167 / ST10 | little | u8/u16 | cal sum — WinOLS plugin |
| EDC16 | Infineon TriCore TC1766/96 / ST10 | little | u8/u16 | cal CRC + program — WinOLS plugin |
| EDC17 / EDC17Cxx/CPxx | Infineon TriCore TC1766/67/96/97 | little | u8/u16, s16 (SOI/correction) | multi-layer cal+program CRC — WinOLS plugin |
| MD1 (CV/modern diesel) | Infineon AURIX TC27x/29x | little | u8/u16/**f32** | cal CRC + **RSA/secure boot** — tool only |

Common diesel maps & plausible bands (for sanity-checking a decode):
- Rail pressure target/limit: hundreds–2500+ bar
- Injected quantity (IQ/mm³): a few–80+ mm³/stroke
- SOI (start of injection): a few °BTDC (s16, can be negative)
- Boost target/limit: mbar or kPa absolute
- Smoke/torque limiter (IQ vs air mass): the classic diesel Stage1 ceiling

## Continental / Siemens-VDO

| Platform | Typical MCU | Endian | Notes |
|----------|-------------|--------|-------|
| SIMOS 8/12/18/19 (VAG gasoline) | Infineon TriCore | little | newer SIMOS18/19 are signed (secure boot) |
| PCR2.1 (VAG diesel) | Infineon TriCore | little | multi-layer checksum |
| SID (PSA/Renault/Volvo diesel) | Infineon TriCore | little | SID807/803/208/901 etc. |
| EMS (Nissan/Renault gasoline) | Infineon TriCore | little | EMS3xxx |
| GPEC (Ford) | Infineon TriCore | little | GPEC2/3/4 |
| SIM2K (Hyundai/Kia, Conti-based) | Infineon TriCore | little | SIM2K-2xx/3xx/5xx — see multi-vendor for Mobis/Kefico routing |

## Practical defaults
- **endian = "little"** for everything above (all Infineon cores). Only switch to big-endian when
  `multi-vendor-ecu-engine` flags a non-Bosch/Conti file on an SH or PowerPC core.
- Most Z-maps are **u8 or u16**; correction/timing maps that go negative are **s8/s16**; only the
  newest AURIX (MG1/MD1) and some Conti carry **f32**.
- Axes are **separate arrays** (use `Axis`), not embedded in the Z-map.

## Checksum routing (critical — see checksum-crc-engine)
- **Data-block cal CRC/sum** that you can locate (range + store offset + algo): recompute via
  `checksum-crc-engine` (`ChecksumBlock.repair`).
- **EDC17/MED17/MEVD17 multi-layer cal+program CRC**: error-prone by hand → **WinOLS checksum plugin
  / ECM Titanium Auto-Fix**.
- **MD1/MG1 RSA / secure boot**: cryptographic, **cannot be recomputed at all** → the OEM/flash tool
  handles it; a bench edit of a signed region without proper tooling will not boot.
- Never declare a Bosch/Conti file flash-ready on a self-computed checksum unless you proved the
  algorithm against an ORI/tool-fixed-MOD pair.

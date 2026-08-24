# Brand & Microcontroller Signatures

Reference for the L1 protocol/microcontroller gate. Use these to identify a file's brand and ECU
family from its content. The scanner (`scripts/scan_library.py`) encodes the same patterns; this file
is for human-readable lookup and for when you parse files inline without the scanner.

> Reminder: a filename or pack label is **not** proof of identity. Always confirm against the binary
> string region and, for definition files, against internal map structure.

## DENSO (Toyota / Lexus / Mazda / Subaru / etc.)
- **Microcontroller IDs** distinguish generations — e.g. `R7F701202` (Gen3), `R7F702002` (Gen4).
  Different `R7F70xxxx` numbers usually mean a different protocol generation → L1 mismatch.
- **Part numbers**: Toyota/Denso style `89663-xxxxx`, or Denso `xxxxxx-xxxx`.
- **Shared platforms** (high-value L3 knowledge, hard to infer from content alone):
  - LC300 / LX600 / Tundra share the DENSO Gen4 V35A platform.
  - Many Hilux / Prado / Fortuner / HiAce diesels share DENSO diesel families.
- Flash sizes are often uniform within a generation (e.g. 8 MB), so **L5 size identity is common and
  weak on its own** — always confirm the MCU.

## Bosch
- **Diesel**: `EDC15`, `EDC16`, `EDC17` (+ suffix like `CP54`, `C54`, `CP04`).
- **Petrol**: `ME7`, `MED9`, `MED17`, `MED17.x`.
- **Newest gen**: `MG1`, `MD1` (e.g. `MG1CS0xx`, `MD1CP0xx`).
- **Part numbers**: `0 281 xxx xxx` (diesel), `0 261 xxxxxx` (petrol).
- **SW/HW**: Bosch files often carry `10SWxxxxxx` / `10HWxxxxxx` style identifiers.
- Note: some Bosch newest-gen files (MD1/MG1) use RSA checksums that cannot be externally recalculated
  — flag this in the usability verdict.

## Continental (and Siemens-derived)
- `SIMOS` families (`SIMOS 8.x`, `SIMOS 10.x`, `SIMOS 12.x`, `SIMOS 18.x`, `SIMOS 19.x`).
- `PCR 2.1` diesel, `EMS` petrol families.
- Conti and Siemens/VDO overlap historically; treat SIMOS as Continental-lineage.

## Siemens / VDO
- `MS` engine management (`MS42`, `MS43`, `MS45`, `MSS` series on some BMW), `VDO` markers.
- Older Siemens DDE/MSV families.

## Delphi
- `DCM` diesel (`DCM3.2`, `DCM3.5`, `DCM6.2`, `DCM7.x`).
- `MT` series petrol.
- Common on PSA, Renault, Hyundai/Kia, GM diesels.

## When brand is ambiguous
If two brands' patterns both hit, prefer the one with more distinct signature hits and the most
specific microcontroller/family token. If still ambiguous, report both and mark L1 as *unverified* —
do not upgrade such a candidate above 🟡 same-family reference without user confirmation.

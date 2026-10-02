# Migration schema and evidence model

## Normalized input fields

| Field | Meaning |
|---|---|
| `name` | Source definition name |
| `address` | Source ECU address when supplied |
| `source_offset` | Byte offset in the matching source BIN |
| `size_bytes` | Z-data byte length |
| `dims` | Rows/columns or source dimensions |
| `datatype` | A2L/normalized datatype |
| `byte_order` | `little`, `big`, or unresolved |
| `factor`, `offset`, `unit` | Display conversion metadata |
| `axes` | Axis metadata; addresses require separate relocation/verification |

For A2L, calculate `source_offset = address - source_base`. For OLS/KP, an extracted address is only
a candidate file offset until it matches the source BIN and WinOLS properties.

## Evidence signals

- Unique exact full-map byte match.
- Matching bytes before/after the map.
- Dominant relocation delta supported by multiple independent maps.
- Exact high-entropy sub-block anchors.
- Candidate order and spacing consistency.
- Approximate byte/u16 correlation and derivative-sign agreement.

Exact matches can be `VERIFIED` when unique and structurally valid. Approximate matches remain
`CANDIDATE_*` by default because similar calibration shapes are common and software updates may split,
transpose, rescale, or replace maps.

Shared A2L `AXIS_PTS` objects are fingerprinted and relocated separately. A MAP is a complete migrated
definition only when its Z object and every external axis are `VERIFIED`. Embedded `STD_AXIS` layouts
and OLS/KP records without exposed axis metadata remain `AXES_PENDING/AXES_UNKNOWN`.

## Output statuses

| Status | Meaning |
|---|---|
| `VERIFIED` | Unique exact relocation with valid boundaries and sufficient structure |
| `CANDIDATE_HIGH` | Strong approximate/delta-supported candidate; manual proof required |
| `CANDIDATE_MEDIUM` | Plausible but weak or close runner-up |
| `AMBIGUOUS` | Multiple candidates cannot be separated |
| `UNRESOLVED` | No defensible candidate |
| `INVALID_SOURCE` | Source definition points outside the matching source BIN |
| `NEEDS_SOURCE_BIN` | No source bytes are available to fingerprint |

## Required report fields

Store source/target SHA-256, parser kind, bases, dominant delta and support count. For each map store
source address/offset, target offset/address, status, confidence, evidence list, best/runner-up scores,
candidate count and all copied definition metadata.

# Calibration File Types

How to recognize and treat each file type the library may contain.

| Ext | Type | What it is | How to treat |
|-----|------|-----------|--------------|
| `.bin` | BIN | Raw ECU memory dump | Read binary; extract brand/IDs; can diff |
| `.ori` | ORI | Original (stock) read, unmodified | Treat as the recovery baseline; never overwrite |
| `.mod` | MOD | Modified file | Diff against its ORI; check checksum impact |
| `.ols` | OLS | WinOLS project (maps, versions, comments) | Definition source; high-value reference |
| `.kp` | KP | WinOLS map pack / project package | Key "same-platform reference" source; labels may be incomplete |
| `.a2l` | A2L | OEM ASAM definition (maps, axes, units, functions) | **Primary search target** — best for direct reuse |
| `.dam`/`.damos` | DAMOS | OEM calibration definition | **Primary search target** |
| `.csv` | CSV | Tool export (e.g. ECM Titanium) with addresses, factors, values | Parse columns for address/factor/value; great for verification |
| `.hex` | HEX | Intel HEX | Convert/parse as binary |
| `.s19` | SREC | Motorola S-record | Convert/parse as binary |
| `.frf` | FRF | VAG flash container | May need unpacking before content analysis |

## Extraction goals per file
For every file, try to capture: format type, **file size in bytes**, brand, microcontroller/ECU
family, SW/HW/Cal IDs, map count/dimensions (for definition files), and whether checksum regions are
present. Missing values are reported as "not extractable" — never invented.

## Definition files vs binaries
- **A2L / DAMOS / KP / OLS** describe *where maps live* — the most reusable across same-protocol files.
- **BIN / ORI / MOD** are the actual data — used for ID extraction, diffing, and as flash sources.
- A directly-usable match ideally pairs a same-protocol **definition** (A2L/DAMOS) with the target
  **binary**.

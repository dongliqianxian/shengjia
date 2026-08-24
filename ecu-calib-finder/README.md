# ecu-calib-finder — Install Guide

A portable skill that searches your local ECU calibration library (`E:\ECU_Tuning_Projects`) for
compatible DAMOS/A2L/KP/OLS/BIN/ORI/MOD/CSV files matching a given ECU file, ranks them by similarity
(L1–L5 rules), and judges whether each is flashable. Covers Bosch, Continental, Siemens/VDO, Delphi,
DENSO.

## Contents
```
ecu-calib-finder/
├── SKILL.md                       # the skill instructions
├── scripts/
│   └── scan_library.py            # recursive scanner + L1–L5 matcher (pure Python 3, no deps)
└── references/
    ├── brand_signatures.md        # how each brand/MCU is identified
    └── file_types.md              # how each file type is treated
```

## Install — Claude Desktop / Cowork
Option A (packaged): install the `ecu-calib-finder.skill` file through the app's skill installer.
Option B (folder): copy the `ecu-calib-finder/` folder into your Claude skills directory.

The skill triggers automatically when you mention your calibration library, ask to find a matching
DAMOS/A2L, ask if a file is flashable, or name an ECU/MCU and ask for a library match.

## Install — Codex
1. Copy the `ecu-calib-finder/` folder into your project (e.g. `./skills/ecu-calib-finder/`).
2. Reference `SKILL.md` from your agent config / `AGENTS.md` so the agent loads it.
3. Codex can run the scanner directly:
   ```
   python skills/ecu-calib-finder/scripts/scan_library.py "E:\ECU_Tuning_Projects" --target <file> --out index.json
   ```

## Install — Cursor
1. Copy the `ecu-calib-finder/` folder into your project root.
2. Add a pointer in `.cursorrules` (or paste the SKILL.md body), e.g.:
   ```
   When the user asks to find compatible ECU calibration files, search their library, check if a
   file is flashable, or do an ORI vs MOD diff, follow skills/ecu-calib-finder/SKILL.md and use
   scripts/scan_library.py to scan E:\ECU_Tuning_Projects.
   ```
3. Cursor's agent can run the scanner via its terminal.

## Quick use
- Direct-scan environments (Desktop with file access / Codex / Cursor):
  ```
  python scripts/scan_library.py "E:\ECU_Tuning_Projects" --target "E:\path\to\target.bin" --out index.json
  ```
  Then ask the agent to read `index.json` and produce the usability report.
- Web (claude.ai): the skill will ask you to export a file list
  (`dir /s /b /a-d E:\ECU_Tuning_Projects > filelist.txt`) and upload it, then upload top candidates.

## Customizing
- Change the library path anywhere you pass it (default in SKILL.md is `E:\ECU_Tuning_Projects`).
- Add brand/MCU patterns in `scripts/scan_library.py` (`BRAND_PATTERNS`) and
  `references/brand_signatures.md`.
- Add your known shared-platform map (e.g. LC300/LX600/Tundra → V35A) to
  `references/brand_signatures.md` to sharpen L3 family matching.

## Safety
A "flashable" verdict always reminds you that checksums must be recalculated before flashing with the
proper tool (ECM Titanium / WinOLS checksum plugin). File size identity (L5) never decides
compatibility on its own — the protocol/MCU gate (L1) governs.

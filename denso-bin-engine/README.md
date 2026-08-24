# denso-bin-engine — Install & Use

A tested engine for reading, editing, and diffing DENSO ECU binaries (BIN/ORI/MOD) by
address + dimensions + encoding — so you stop hand-writing byte parsers per project.

## Contents
```
denso-bin-engine/
├── SKILL.md                          # skill instructions
├── scripts/
│   ├── denso_bin.py                  # the engine (pure Python 3, no deps)
│   └── test_denso_bin.py             # test suite (run after any change)
└── references/
    └── verified_encodings.md         # real, confirmed map layouts per platform
```

## Verified on real files
The test suite runs the engine against actual LC300 V35A files and checks: encoding round-trips,
13×11 float map reads, ORI↔MOD diff, region edit + write-back persistence, strided/multi-page
offset math. 15/15 pass.

```bash
python scripts/test_denso_bin.py
```

## Install
- **Claude Desktop / Cowork**: install the packaged `.skill`, or drop the `denso-bin-engine/`
  folder into your skills directory.
- **Codex**: copy the folder into your project; reference `SKILL.md` from `AGENTS.md`. Import or run
  `scripts/denso_bin.py` from the terminal.
- **Cursor**: copy the folder in; add a pointer in `.cursorrules` to follow `SKILL.md` and use
  `scripts/denso_bin.py` for any DENSO bin read/edit/diff.

## 30-second example
```python
from denso_bin import DensoMap, load, save_as, CHECKSUM_REMINDER
buf = load("AAL_LC300_V35A.bin")
std = DensoMap("StandardTorque", 0x068AE8, rows=13, cols=11, encoding="float32_le")
std.apply(buf, lambda r, c, v: v * 0.93 if v > 5 else v, cols=range(3, 9))  # soften part-throttle
out = save_as(buf, "AAL_LC300_V35A.bin", suffix="_Stage1")
print(out); print(CHECKSUM_REMINDER)
```

## CLI quick-read
```bash
python scripts/denso_bin.py AAL_LC300_V35A.bin --addr 0x068AE8 --rows 13 --cols 11 --enc float32_le
```

## The non-negotiable
Writing a bin does NOT make it flashable. Recalculate the DENSO checksum in ECM Titanium
(Tools → Checksum → Auto Fix) before flashing. The engine reminds you on every write.

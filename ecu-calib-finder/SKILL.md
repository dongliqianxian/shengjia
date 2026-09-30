---
name: ecu-calib-finder
description: >-
  Search a local ECU calibration file library for compatible DAMOS/A2L/KP/OLS/BIN/ORI/MOD/CSV
  files that match a given ECU file by protocol, microcontroller, software/hardware ID, platform
  family, and file size — then rank candidates by similarity and judge whether each is flashable.
  Use this skill WHENEVER the user references their calibration library or asks to find matching/
  compatible tuning files: "find a DAMOS/A2L for this ECU", "what in my library fits this file",
  "same platform", "same protocol", "is this ORI/MOD flashable", "what's missing to flash this",
  or mentions an ECU/microcontroller ID (e.g. R7F702002, EDC17, MED17, SIMOS,
  DCM) and asks if the library has a match. Covers Bosch, Continental, Siemens/VDO, Delphi, and
  DENSO. Trigger even if the user only says "search my library for something that works with this
  ECU" — do not answer from general knowledge instead.
  NOT this skill: comparing an ORI against a MOD to find out which MAPs were changed and by how
  much, or porting those changes onto another file — that is `reference-tune-porting`. This skill
  only judges compatibility and flashability at the FILE level, never at the calibration-semantics
  level.
---

# ECU Calibration File Finder

This skill finds, ranks, and judges the usability of calibration files in a local library
(`E:\ECU_Tuning_Projects` by default) against a given target ECU file.

It works across two kinds of environments and adapts automatically:
- **File-system environments** (Kimi Work with filesystem access, Codex, Cursor) — scan the
  library directly with `scripts/scan_library.py`.
- **Upload-only environments** (web chat without disk access) — cannot read the disk; guide the user to provide a
  file list, then match against that.

The library covers five ECU brands: **Bosch, Continental, Siemens/VDO, Delphi, DENSO**. Because the
user has no fixed naming convention, this skill builds matches from file *content + name + path*,
never from the filename alone.

---

> **与 `rom-similarity-finder` 的分界(互补,不重叠)**:本技能按**标识符**找 ——
> 文件名、协议、MCU、平台、硬件号/软件号。`rom-similarity-finder` 按**文件内容本身**找 ——
> 字节级指纹,不看名字、不看后缀、不需要任何元数据,**改过名或去过容器头的文件照样能配上**。
> 两者结果**互为印证**:都指向同一个文件时置信度最高;只有一边命中时,以内容匹配为准
> (文件名不是证据 —— 本技能自己也这么规定)。

## Step 0 — Detect environment and capability

Before anything, determine whether you can read the library directory.

1. Try to access the library root. Default: `E:\ECU_Tuning_Projects`. (On non-Windows test
   environments, the user may give a different path — use whatever they provide.)
2. If you have a shell or file tools and the path is readable → **Mode A (direct scan)**.
3. If you cannot reach the disk (e.g. web chat without file tools) → **Mode B (list-assisted)**.
4. If unsure, attempt Mode A; on failure, fall back to Mode B and say why.

**Never pretend to have scanned files you did not actually read.** If you didn't read it, say so and
give the user the lowest-effort way to supply the data.

---

## Step 1 — Identify the TARGET file

Whatever the mode, first fully characterize the file the user wants to match against. Extract:

- **File type**: BIN / ORI / MOD / OLS / KP / A2L / DAMOS / CSV (see `references/file_types.md`)
- **File size** in bytes (this powers the L5 pre-filter — record it precisely)
- **Brand + ECU + microcontroller** (e.g. `DENSO Gen4 R7F702002`, `Bosch EDC17CP54`)
- **SW / HW / Calibration ID** — extract from the binary string region; if not extractable, write
  `未能提取 / not extractable` (do not guess)
- **Engine / platform** (e.g. `V35A`, and known shared platforms like `LC300 / LX600 / Tundra`)

For brand-specific extraction patterns (Bosch part numbers, DENSO microcontroller families, etc.),
read `references/brand_signatures.md`.

---

## Step 2 — Build / refresh the library index

### Mode A (direct scan)
Run the scanner and read its JSON output:
```bash
python scripts/scan_library.py "E:\ECU_Tuning_Projects" --target "<path-to-target-file>" --out library_index.json
```
The scanner walks every subdirectory, records each file's path/extension/size, attempts ID and brand
extraction from binary content, and (if `--target` is given) pre-scores every file against the target
using the L1–L5 rules below. Read `library_index.json` and proceed to Step 3.

If Python is unavailable, replicate the logic inline: list files recursively, capture sizes, read
binary string regions for IDs.

### Mode B (list-assisted)
Tell the user you can't read the disk, and give them a ready command to export a list:
```
# Simple list (paths only):
dir /s /b /a-d E:\ECU_Tuning_Projects > filelist.txt

# Better — paths + sizes (PowerShell):
Get-ChildItem -Recurse -File E:\ECU_Tuning_Projects |
  Select-Object FullName,Length | Export-Csv filelist.csv -NoTypeInformation
```
Once they upload the list, pre-filter by name + size, then ask them to upload the actual TOP
candidate files so you can verify by content (L1/L2). Do not declare anything flashable on filename
or size alone.

---

## Step 3 — Apply the five-level matching rules

Match each library file to the target using these levels. **L5 is a fast pre-filter; L1 is the hard
gate.** Size alone never decides usability.

| Level | Dimension | Basis | Role |
|-------|-----------|-------|------|
| **L5** | File size | Byte count **identical** to target | Pre-filter: same size ⇒ likely same flash capacity ⇒ include as candidate |
| **L1** | ECU protocol / microcontroller | MCU ID, ECU hardware family, brand platform code | **Hard gate**: mismatch ⇒ excluded, regardless of size |
| **L2** | SW / HW number | Software/Hardware numbers from file content | High weight |
| **L3** | Calibration / platform family | Cal-ID prefix, engine code, shared-vehicle platform | Medium |
| **L4** | Cal version / year / region | Cal version, model year, market region | Low (tie-break ordering) |

**Critical combination logic:**
- Same size (L5) **but** different protocol (L1 fail) ⇒ **NOT usable**. Explicitly state the size
  match is a capacity coincidence, not compatibility.
- Names and pack labels can be wrong or incomplete — verify L3/L4 against internal map structure and
  neighbouring data, not the filename.
- Never recommend a cross-brand file as "directly usable."

---

## Step 4 — Classify each candidate (three tiers)

- ✅ **Directly usable** — L5 same + L1 + L2 match ⇒ same protocol & SW/HW; DAMOS/A2L can be applied directly.
- 🟡 **Same-family reference** — L1 matches, L2/L3 partial ⇒ useful for map-finding, but addresses/axes must be verified.
- ❌ **Not usable** — L1 mismatch ⇒ different protocol/MCU; exclude and explain (including the "size coincidence" case).

---

## Step 5 — Output report

ALWAYS use this exact structure:

```
【目标文件识别 / Target】
  - 文件 File: [name]   大小 Size: [bytes]
  - 类型 Type: [BIN/ORI/MOD/...]
  - 品牌/ECU/MCU: [e.g. DENSO Gen4 R7F702002]
  - SW/HW/Cal ID: [values, or 未能提取/not extractable]
  - 发动机/平台 Engine/Platform: [e.g. V35A / LC300-LX600-Tundra shared]

【库内检索结果 / Library matches】(ranked by similarity, descending)
  # | File | Type | Size | L5 | Match | Sim% | Verdict
  1 | xxx.a2l | A2L | =target | ✓ | L1+L2 | 98% | ✅ directly usable
  2 | xxx.kp  | KP  | =target | ✓ | L1+L3 | 76% | 🟡 same-family reference
  3 | yyy.bin | BIN | =target | ✓ | L1✗   | 12% | ❌ size coincidence, different protocol

【可用性判定 / Usability】(per top candidate)
  - [file]: usable / not → because [L5+L1+L2 status] → next step needs [DAMOS / checksum / address verification]

【ORI vs MOD 差异报告 / Diff】(only if applicable — see Step 6)

【下一步建议 / Next steps】
  - Can it be flashed, why, and what is still missing
```

---

## Step 6 — ORI vs MOD pair (file-level only, then hand off)

If the user gives both an original (ORI) and a modified (MOD) file, this skill answers only the
**file-level** questions:
- Are they the same protocol / MCU / SW-HW (L1/L2)? Is the MOD derived from this ORI at all?
- Do the changes fall inside checksum-protected regions? Is the MOD flashable as-is?
- Keep the stock ORI untouched as the recovery baseline.

**Which MAPs changed, by how much, whether that is reasonable, and how to port it onto another
file — all of that belongs to `reference-tune-porting`. Hand off; do not attempt a
map-by-map diff here.**

---

## Safety and boundaries (always apply)

- **Checksum is a red line.** Any "flashable" verdict must remind the user that Bosch/DENSO/etc. ECUs
  require checksum recalculation before flashing, using the proper tool (ECM Titanium / WinOLS
  checksum plugin). This skill does not guarantee external checksum recalculation.
- **L5 never decides alone** — size identity is only a pre-filter; L1 protocol match governs.
- **State "not usable" plainly** when L1 fails; no "might work" hedging.
- **No fabrication** — if an ID can't be extracted, say so; don't invent it.
- **Cross-brand files are never "directly usable."**
- **Preserve originals** — advise backing up the ORI before any operation.

---

## Installation note (for the user)

This skill is portable. To use it across tools:
- **Kimi Work**: place the `ecu-calib-finder/` folder in `~/.agents/skills/`.
- **Codex / Cursor**: point the agent at this folder, or copy `SKILL.md` content into your project's
  agent instructions (e.g. `.cursorrules` / `AGENTS.md`), and keep `scripts/` + `references/`
  alongside so the scanner and brand signatures are available.

The scanner (`scripts/scan_library.py`) is plain Python 3 with no external dependencies, so it runs
anywhere those tools provide a shell.

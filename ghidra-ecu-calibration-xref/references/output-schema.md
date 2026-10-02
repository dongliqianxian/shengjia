# Output schema and CLI

## Target syntax

Supply one or more targets:

```text
--target descriptor=0x44EA8:0x44ECF
--target z_data=0x44ED0:0x45132
--target scalar=0x123456
```

Labels must contain letters, digits, underscore, dot, or hyphen. Addresses are interpreted by Ghidra in the current program address space.

## Runner inputs

Required:

- `--ghidra-home`
- `--project-dir`
- `--project`
- `--program`
- at least one `--target`
- `--out-dir`

Optional:

- `--bin` to record SHA-256 and file length;
- `--java-home` when Ghidra does not discover Java;
- `--function` for extra decompilation seeds;
- `--depth 0|1|2` for the call neighborhood;
- `--script-dir` to override the bundled scripts path;
- `--analysis` to allow normal analysis instead of `-noanalysis`.

## Artifacts

### `case_input.json`

Records the run command inputs, firmware hash/size when supplied, targets, Ghidra project/program, and UTC timestamp.

### `xref_evidence.json`

Contains:

- program/language/compiler metadata;
- normalized target ranges;
- direct Ghidra references;
- instruction-text fallback hits;
- ignored internal/control-flow references caused by code flow or data mis-disassembly;
- containing function name and entry address;
- local instruction context for text-only hits.

### `dataflow_context.txt`

Contains bounded instruction windows around each hit. Every instruction includes references and P-code when available. Use it to identify lookup calls, fixed-point arithmetic, sign extension, saturation, clamps, stores, and downstream calls.

### `decompile.txt`

Contains function identity, callers, callees, bounded assembly, and decompiled C for discovered/explicit seed functions. Ghidra names are not OEM symbols.

### `calibration_xref_report.md`

Summarizes automated code status and transformation clues. It intentionally leaves physical semantic naming pending until axes, scaling, and consumers are interpreted.

## Exit behavior

- Nonzero exit: runner/configuration/Ghidra failure; do not interpret partial files as a completed audit.
- Zero exit with `UNRESOLVED`: the run succeeded but no defensible reference was found.
- A project-lock error requires closing the GUI or waiting for the previous headless process; do not delete lock files blindly.

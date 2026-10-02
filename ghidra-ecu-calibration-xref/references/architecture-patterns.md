# Architecture patterns

Use Ghidra references and P-code first. Treat mnemonic patterns only as fallback clues.

| MCU family | Common address construction clues | Common sign/width clues | Notes |
|---|---|---|---|
| V850/RH850 | `mov`, `movhi`, `movea`, GP-relative loads | `sxh`, `zxh`, halfword loads | A descriptor address may appear as an immediate argument to a common lookup function. |
| TriCore/AURIX | `movh.a` + `lea`, address-register loads | `extr`, signed/unsigned loads, shifts | Cached/non-cached aliases and high image bases must be normalized. |
| PowerPC/VLE | `lis`/`addis` + `addi`, SDA-relative loads | `extsh`, `clrlwi`, rotate/mask | File offsets and runtime addresses often differ substantially. |
| SuperH | literal-pool `mov.l`, PC/GBR-relative loads | `exts.w`, `extu.w` | A code reference may target a literal pool which then contains the table address. |
| C166/ST10 | segmented addressing and data-page pointers | word sign/zero extension | Prefer IDA when Ghidra processor/decompiler support is incomplete. |

## Architecture-neutral priority

1. Query `ReferenceManager` for references into the full target range.
2. Inspect instruction references and P-code constants.
3. Resolve containing function and call references.
4. Check literal pools, pointer tables, and descriptor structures.
5. Use instruction-text search only when Ghidra failed to create a reference.

## Import sanity gate

Stop and route to `ghidra-ida-setup` if:

- reset/vector code does not disassemble coherently;
- most absolute pointers land outside mapped blocks;
- function discovery is implausibly sparse or noisy;
- expected firmware strings or IDs cannot be located;
- switching byte order makes axes/code suddenly coherent;
- runtime-address conversion is unknown.

Never compensate for a bad import by inventing address deltas.

## Descriptor versus data

Calibration software may display the first Z cell while code references:

- a descriptor before the axes/data;
- an array of pointers to axes and Z-data;
- a family header shared by several tables;
- an index selecting a descriptor from a pointer table.

Search the complete known structure and record which member is actually referenced.

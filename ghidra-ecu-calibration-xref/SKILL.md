---
name: ghidra-ecu-calibration-xref
description: Trace known ECU or TCU calibration addresses, map descriptors, axes, scalars, and data ranges through Ghidra to identify code readers, lookup/interpolation calls, input sources, signedness and fixed-point transformations, clamps, output stores, downstream consumers, and call chains. Use when a user provides a BIN plus a WinOLS, ECM Titanium, BitEdit, A2L, DAMOS, OLS or raw address and asks who reads it, what the MAP controls, whether its name is correct, whether two MAPs share a function, or requests XREF/decompilation evidence. Supports V850/RH850, TriCore/AURIX, PowerPC/VLE, SuperH and other correctly loaded Ghidra architectures. Route processor/base/memory-map setup to ghidra-ida-setup, unknown-address discovery to blind-map-finder, physical interpretation to ecu-map-analysis, scaling to winols-scaling-converter, and byte edits to the matching BIN engine.
---

# Ghidra ECU Calibration XREF

Turn a known calibration address into an evidence-graded code/data-flow dossier. Prove the address, lookup direction, transformations, and consumers before assigning an OEM MAP name.

Run under `ecu-calibration-doctrine`. This skill analyzes and reports; it does not modify firmware or disable protection logic.

## Route the task

- If processor, image base, memory map, or endianness is uncertain, use `ghidra-ida-setup` first.
- If no address or candidate exists, use `blind-map-finder` first.
- If an A2L/DAMOS/OLS definition already answers the question, parse it first and use this skill only to verify the call path.
- If the user supplies a known map/scalar/descriptor address and asks what reads or controls it, use this skill.
- After XREF evidence is collected, use `ecu-map-analysis` and `winols-scaling-converter` for the physical conclusion.

## Required inputs

Collect or explicitly mark missing:

1. Firmware path and preferably SHA-256.
2. Ghidra project directory, project name, and program name.
3. MCU/language, byte order, image base, and address-space assumptions.
4. One or more target ranges using `label=start[:end]`.
5. Address meaning: file offset, runtime address, descriptor, axis, scalar, or Z-data.
6. Optional map dimensions, dtype, factor, offset, units, and semantic hypothesis.

Never silently equate a WinOLS file offset with a Ghidra runtime address. Record the conversion.

## Core workflow

### 1. Normalize the address family

For a MAP, search more than the first Z cell. Include every known member:

- descriptor start and descriptor range;
- X/Y axis ranges;
- Z-data range;
- nearby pointers or table-family header;
- file-offset and runtime-address forms when both are known.

This prevents a false `NO_XREF` when code references the descriptor but WinOLS displays the first Z cell.

### 2. Run the automated audit

Use `scripts/run_xref_audit.py`. Quote paths containing spaces or non-ASCII characters.

```powershell
python scripts/run_xref_audit.py `
  --ghidra-home "C:\Tools\ghidra" `
  --project-dir "C:\work\ghidra_projects" `
  --project ECU_PROJECT `
  --program ECU.bin `
  --bin "C:\work\ECU.bin" `
  --target descriptor=0x44EA8:0x45133 `
  --target z_data=0x44ED0:0x45132 `
  --out-dir "C:\work\xref_case"
```

The runner performs two bounded passes:

1. address/range XREF plus local P-code data-flow context;
2. decompilation of discovered containing functions and a depth-limited call neighborhood.

Use `--function 0x...` for an additional seed. Use `--depth 0` to decompile only direct reader functions and `--depth 1` for immediate callers/callees.

### 3. Inspect evidence in this order

1. `case_input.json`: verify identity and address assumptions.
2. `xref_evidence.json`: distinguish direct references from instruction-text fallbacks.
3. `dataflow_context.txt`: inspect inputs, lookup call, return transformations, saturation, clamps, stores, and downstream calls.
4. `decompile.txt`: identify upstream sources and downstream consumers.
5. `calibration_xref_report.md`: use as the audit summary, not as an automatic semantic verdict.

Read [references/evidence-contract.md](references/evidence-contract.md) before assigning a function name. Read [references/architecture-patterns.md](references/architecture-patterns.md) only for the active MCU family. Read [references/output-schema.md](references/output-schema.md) when integrating or parsing artifacts.

### 4. Reconstruct the data-flow statement

Write a compact statement supported by the evidence:

```text
input_A + input_B
  -> lookup(table_descriptor)
  -> signed/unsigned conversion
  -> offset/factor/saturation/clamp
  -> RAM store or downstream function
  -> control-domain candidate
```

Separate verified operations from interpretation. For example:

```text
VERIFIED: lookup return subtracts 0x1900 and is sign-extended to 16 bits.
CANDIDATE_HIGH: with confirmed factor 1/32, the result is signed torque in Nm.
UNRESOLVED: exact OEM symbol name is unavailable.
```

### 5. Cross-check physical meaning

Require all applicable checks before raising semantic confidence:

- axes are monotonic and physically plausible;
- dtype, byte order, signedness, factor, and offset are confirmed;
- lookup argument order matches the proposed X/Y axes;
- output range and zero point fit the proposed unit;
- upstream variables fit the proposed inputs;
- downstream consumers fit the proposed control domain;
- sibling tables using the same lookup routine do not contradict the label;
- LOG or a trusted definition supports the name when available.

## Evidence and naming discipline

Use two independent classifications:

- **Code status**: `VERIFIED_XREF`, `VERIFIED_LOOKUP`, `VERIFIED_TRANSFORM`, `VERIFIED_CONSUMER`, `INDIRECT_REFERENCE`, `UNRESOLVED`.
- **Semantic grade**: A, B+, B, or C.

A direct XREF does not automatically make an OEM name A-grade. It can be A-grade code evidence and only B+ semantic evidence.

Never infer function from table shape alone when the code path contradicts it. Never call a data start unreferenced until the descriptor, range, pointer table, and runtime-address form have also been checked.

## Required report content

Report:

1. firmware identity and address-space assumptions;
2. target address family and which member is actually referenced;
3. reader function and lookup/interpolation function;
4. proven X/Y input sources when available;
5. signedness, offset, fixed-point operations, saturation, and clamps;
6. stores, downstream calls, and likely control domain;
7. code status and semantic grade separately;
8. contradictions and unresolved items;
9. the next most efficient verification step;
10. whether the evidence is sufficient for interpretation only or for a calibrated byte edit.

## Boundaries

- Do not patch code, NOP monitors, or alter firmware with this skill.
- Do not claim scaling from an immediate constant alone; confirm the whole conversion chain.
- Do not treat a decompiler variable name as an OEM symbol.
- Do not use a supplier name to guess byte order.
- Do not interpret indirect/no-XREF as unused until pointer and descriptor paths are exhausted.
- Preserve ORI and route any later write to the correct BIN engine.

## Self-test and validation

Run deterministic local tests:

```powershell
python scripts/run_xref_audit.py --selftest
python scripts/build_evidence_report.py --selftest
```

Then run one real Ghidra integration case and verify expected reader, lookup, transform, and consumer evidence. Validate the skill folder with the `skill-creator` `quick_validate.py` script before installation.

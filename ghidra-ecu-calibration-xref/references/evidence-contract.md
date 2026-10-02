# Evidence contract

Keep code certainty separate from physical/semantic certainty.

## Code status

| Status | Minimum evidence |
|---|---|
| `VERIFIED_XREF` | A Ghidra reference targets the supplied address/range and the containing function is known. |
| `VERIFIED_LOOKUP` | The target/descriptor is passed to an identified lookup or interpolation call. |
| `VERIFIED_TRANSFORM` | Post-lookup sign extension, offset, shift, multiply/divide, saturation, or clamp is visible in code/P-code. |
| `VERIFIED_CONSUMER` | The transformed result is traced to a RAM store, call argument, comparison, report, or actuator/model consumer. |
| `INDIRECT_REFERENCE` | Instruction/P-code/pointer evidence exists, but Ghidra has no direct reference to the supplied range. |
| `UNRESOLVED` | No defensible code path has been found. This is not proof that the data is unused. |

## Semantic grade

| Grade | Meaning |
|---|---|
| A | Trusted definition or symbol plus matching code path, scaling, physical range, and preferably LOG/cross-version confirmation. |
| B+ | Lookup direction, transform, and control domain are strongly supported; exact OEM symbol remains unknown. |
| B | Shape/range and partial code context agree, but one important link is missing. |
| C | Screenshot, shape, or family resemblance only. Never use as a byte-write basis. |

## Required separation

Use wording like:

```text
Code evidence: VERIFIED_XREF + VERIFIED_TRANSFORM.
Semantic conclusion: B+ candidate for signed engine torque.
Exact OEM name: UNRESOLVED.
```

Do not upgrade semantic confidence merely because the decompiler produced readable C. Ghidra names such as `FUN_...`, `DAT_...`, and temporary variables are analysis artifacts, not OEM symbols.

## Contradiction handling

Downgrade or reject a semantic label if any of these conflict:

- proposed unit does not fit the signedness/zero point;
- proposed X/Y axes do not match lookup argument order;
- output is consumed by a different control domain;
- the same table is selected only in a special mode;
- a sibling table or trusted definition has incompatible meaning;
- file-offset/runtime-address conversion is uncertain;
- image base, byte order, or processor import is not verified.

## Write boundary

XREF evidence is necessary but not sufficient for editing. Require confirmed address, length, dtype, byte order, factor, offset, axes, units, target cells, bounds, and linked-model consequences before changing a BIN.

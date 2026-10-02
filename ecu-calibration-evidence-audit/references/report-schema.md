# Evidence input and report schema

## Artifact flow

```text
case files -> case_manifest.json
manual evidence + Ghidra xref -> map_evidence_ledger.csv
manifest + ledger -> write_decision.json
manifest + ledger + decision + control_chain.md -> calibration_audit_report.md
```

## Manual evidence JSON

Use either a top-level array or `{ "maps": [...] }`.

```json
{
  "maps": [
    {
      "label": "driver_demand_bank",
      "descriptor_start": "0x3E104",
      "data_start": "0x3E128",
      "dimensions": "1x16",
      "hypothesis": "normalized pedal to driver request",
      "role": "driver_demand",
      "code_status": "VERIFIED_CONSUMER",
      "semantic_grade": "B+",
      "exact_oem_name": "UNRESOLVED",
      "trusted_definition": false,
      "address_confirmed": true,
      "layout_confirmed": true,
      "dtype_confirmed": true,
      "byte_order_confirmed": true,
      "factor_confirmed": true,
      "offset_confirmed": true,
      "axes_confirmed": true,
      "units_confirmed": true,
      "active_variant_confirmed": false,
      "target_cells_confirmed": false,
      "bounds_confirmed": false,
      "linked_model_reviewed": true,
      "log_validated": false,
      "contradictions": "",
      "next_validation": "Correlate active page with gear and mode LOG",
      "notes": "Core family located; page identities unresolved"
    }
  ]
}
```

CSV input may use the same column names. Boolean values accept `true/false`, `yes/no`, `1/0`, or `y/n`.

## Ledger fields

| Field | Meaning |
|---|---|
| `label` | Stable case-local identifier |
| `descriptor_start`, `data_start` | Preserve both; do not silently equate them |
| `dimensions` | Logical table size or scalar/curve type |
| `hypothesis` | Proposed physical behavior |
| `role` | Role from `map-role-taxonomy.md` |
| `code_status` | Independent code-evidence status |
| `semantic_grade` | A/B+/B/C physical confidence |
| `exact_oem_name` | Symbol/definition name or `UNRESOLVED` |
| confirmation booleans | Write-gate evidence |
| `contradictions` | Conflicting address, unit, direction, selector, or consumer evidence |
| `next_validation` | Minimum test that resolves the largest uncertainty |
| `edit_ready` | Deterministic result; never set manually |
| `edit_blockers` | Deterministic missing gates |

## Decision states

| Decision | Meaning |
|---|---|
| `NO_EDIT` | Case-level prerequisite or contradiction blocks editing |
| `INTERPRETATION_ONLY` | Evidence supports explanation but encoding/semantics are incomplete |
| `LOG_FIRST` | Address/encoding are substantially known; activation or vehicle response is not |
| `EDIT_CANDIDATE` | At least one row passed all deterministic gates; platform write/checksum review still remains |

## Control-chain file

Write `control_chain.md` with two explicit sections:

```markdown
## Verified chain

input -> lookup -> transform -> store -> consumer

## Inferred semantics

Explain the physical hypothesis and semantic grade.

## Unresolved links

List missing signal names, selectors, units, or downstream consumers.
```

## Report rules

- Lead with decision and blockers.
- Show firmware identity and address-space assumptions.
- List every MAP role with code status and semantic grade.
- Separate verified chain from inferred semantics.
- State what to keep stock, what may be tested, and what may be edited.
- Provide LOG channels, test conditions, pass/fail criteria, and rollback conditions.
- Do not call an artifact flash-ready without platform-specific integrity confirmation.

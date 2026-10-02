---
name: ecu-calibration-evidence-audit
description: Orchestrate an evidence-graded ECU or TCU calibration audit from BIN/ORI/MOD, A2L/DAMOS/OLS/KP/CSV definitions, screenshots, known MAP addresses, Ghidra artifacts, and vehicle LOGs. Use when a user asks for a complete control-chain analysis, wants to determine whether MAP names or functions are correct, needs Driver Demand/torque/load/air/TCU roles separated, requests a reusable engineering report, or needs an EDIT/LOG-FIRST/NO-EDIT decision before changing a binary. Works across Bosch, DENSO, Continental/Siemens, Delphi, Marelli, Hitachi and other correctly identified platforms by routing discovery, scaling, XREF, map analysis, tuning matrices, LOG review, and BIN writing to the appropriate execution skills.
---

# ECU Calibration Evidence Audit

Build a reproducible case dossier that separates observed file facts, verified code behavior, physical interpretation, tuning intent, and write authorization. Run under `ecu-calibration-doctrine`. This skill coordinates other skills and creates evidence artifacts; it does not guess missing calibration facts or patch a BIN by itself.

## Required deliverables

Create these artifacts in a case-specific output directory:

1. `case_manifest.json` — firmware identity, hashes, metadata, present/missing inputs.
2. `map_evidence_ledger.csv` — one row per MAP/address family with code and semantic grades.
3. `write_decision.json` — `NO_EDIT`, `INTERPRETATION_ONLY`, `LOG_FIRST`, or `EDIT_CANDIDATE` plus blockers.
4. `control_chain.md` — verified and inferred upstream/downstream paths, clearly separated.
5. `calibration_audit_report.md` — user-facing conclusion, modification boundary, LOG plan, and next action.

Do not call a case flash-ready merely because `EDIT_CANDIDATE` is reached. Route byte editing to the correct BIN engine and checksum/signature handling to the platform-approved tool chain.

## Workflow

### 1. Inventory before interpretation

Record only files that actually exist. Never invent an ORI, MOD, Stage1, A2L, LOG, Cal ID, or hardware configuration.

Run:

```powershell
python scripts/collect_case_inventory.py `
  --case-dir "C:\work\case" `
  --meta "cal_id=89663-60W61" `
  --meta "mcu=V850 candidate" `
  --out "C:\work\case-audit\case_manifest.json"
```

Use repeated `--file` for artifacts outside the case directory. Use `--recursive` only for a bounded case folder; do not hash an entire drive or broad workspace.

Stop any ORI/MOD comparison if a confirmed baseline or target file is absent. Continue with interpretation-only work when useful.

### 2. Confirm firmware identity and address assumptions

Record file length, SHA-256, HW/SW/Cal ID, MCU, processor language, byte order, image base, memory map, and whether each displayed address is a file offset or runtime address.

- Route uncertain processor/base/memory mapping to `ghidra-ida-setup`.
- Route supplier/platform identification to `multi-vendor-ecu-engine` or the matching platform skill.
- Never infer byte order from vehicle or ECU supplier alone.

### 3. Extract definitions and normalize target address families

Parse trusted definitions before blind discovery:

- A2L/DAMOS → `damos-a2l-parser`
- OLS/KP → `winols-ols-parser`
- screenshots → `winols-screenshot-analyzer`
- factor/offset/raw conversion → `winols-scaling-converter`

For every MAP, distinguish descriptor, X axis, Y axis, Z data, pointer table, file offset, and runtime address. Treat third-party names as hypotheses until axes, units, scaling, and consumers agree.

If no address candidate exists, route to `blind-map-finder`. If one seed exists and siblings may be present, route to `parallel-map-finder`.

### 4. Prove code use

Route known addresses to `ghidra-ecu-calibration-xref`. Search the full address family, not only the first Z cell. Preserve its output artifacts and manually confirm lookup arguments, transforms, clamps, stores, and consumers before upgrading code status.

Write the path in this form:

```text
input source
  -> lookup(descriptor)
  -> signedness / offset / fixed-point transform
  -> saturation or clamp
  -> RAM store or downstream call
  -> candidate control domain
```

Use `UNRESOLVED`, not “unused,” when no defensible reference is found.

### 5. Classify control role

Use `ecu-map-analysis` for physical semantics. Read [references/map-role-taxonomy.md](references/map-role-taxonomy.md) when separating sensor transfer, Driver Demand, request shaping, limiter, inverse model, forward model, actuator model, monitor, protection, and ECU-TCU interface.

Always reason by direction:

```text
pedal -> request -> arbitration -> inverse model -> air/actuator
air/load -> forward torque -> monitor/report/TCU
```

A torque axis does not make a MAP Driver Demand. A sensor transfer table is not a response table. Similar pages may represent modes, gears, banks, temperature states, protection variants, or monitoring copies.

### 6. Build the evidence ledger and decision

Prepare manual evidence as JSON or CSV using [references/report-schema.md](references/report-schema.md). Merge it with one or more Ghidra `xref_evidence.json` files:

```powershell
python scripts/build_evidence_ledger.py `
  --manifest "C:\work\case-audit\case_manifest.json" `
  --manual "C:\work\case-audit\manual_evidence.json" `
  --xref "C:\work\xref\xref_evidence.json" `
  --out-csv "C:\work\case-audit\map_evidence_ledger.csv" `
  --decision-out "C:\work\case-audit\write_decision.json"
```

Keep two independent grades:

- Code status: `VERIFIED_XREF`, `VERIFIED_LOOKUP`, `VERIFIED_TRANSFORM`, `VERIFIED_CONSUMER`, `INDIRECT_REFERENCE`, `UNRESOLVED`.
- Semantic grade: `A`, `B+`, `B`, `C`.

Only a row with confirmed identity, layout, dtype, byte order, scaling, axes, units, active variant, target cells, bounds, linked-model review, and LOG/trusted-definition support can become an edit candidate.

### 7. Decide the calibration route

- `NO_EDIT`: missing confirmed ORI, contradictions, uncertain address/layout/scaling, or unsafe scope.
- `INTERPRETATION_ONLY`: useful role/control-chain evidence exists, but encoding or semantics remain incomplete.
- `LOG_FIRST`: address and encoding are substantially confirmed, but activation, limiter source, model response, or vehicle behavior still needs LOG proof.
- `EDIT_CANDIDATE`: at least one row satisfies every ledger gate and no case-level blocker remains.

Route a verified Stage plan to `stage1-calibration-matrix`. Route drivability/torque/air/TCU questions to their specific skills. Never use generic percentages as a substitute for platform evidence.

### 8. Close the loop with vehicle data

Route HP Tuners or generic CSV LOGs to `hp-tuners-log-review`. Require channels that test the proposed causal chain, not only peak values. Compare ORI and candidate calibration on the same fuel and similar thermal conditions.

Check at minimum, when supported:

- APP1/APP2 and normalized pedal;
- driver/engine/final request;
- desired/actual throttle, load, MAF, MAP;
- delivered torque and limiter/intervention source;
- commanded/measured lambda, trims, fuel pressure, injector use;
- final spark and knock response;
- gear, TCU request, TCC/clutch slip, temperatures;
- EGT/CAT/component protection state.

### 9. Build the user-facing report

Create `control_chain.md`, then run:

```powershell
python scripts/build_calibration_report.py `
  --manifest "C:\work\case-audit\case_manifest.json" `
  --ledger "C:\work\case-audit\map_evidence_ledger.csv" `
  --decision "C:\work\case-audit\write_decision.json" `
  --control-chain "C:\work\case-audit\control_chain.md" `
  --out "C:\work\case-audit\calibration_audit_report.md"
```

Use the report for project labeling, validation planning, and modification authorization. Do not copy case-specific addresses into another Cal ID.

## Evidence and safety rules

- State observed facts before interpretation.
- Downgrade same-level conflicting evidence and stop writes until a discriminating test is defined.
- Preserve ORI, use a new output filename, record diffs and rollback conditions.
- Keep torque monitoring, knock, thermal, overspeed, clutch, pressure, diagnostic, and emissions protections unless a lawful, specification-backed calibration task explicitly requires a verified threshold change.
- Do not patch code or NOP monitors through this skill.
- Do not claim checksum, signature, or flash readiness unless the specific integrity layers were verified by the approved tool chain.

## References and case study

- Read [references/report-schema.md](references/report-schema.md) before creating manual evidence or integrating script outputs.
- Read [references/map-role-taxonomy.md](references/map-role-taxonomy.md) when a MAP name or control role is disputed.
- Read [references/toyota-denso-60w61-case.md](references/toyota-denso-60w61-case.md) only as a worked reasoning example. Its addresses and scalings are not portable.
- Use [assets/calibration-audit-template.md](assets/calibration-audit-template.md) through the report builder; do not duplicate the template inside case folders by hand.

## Validation

Run deterministic tests:

```powershell
python scripts/collect_case_inventory.py --selftest
python scripts/build_evidence_ledger.py --selftest
python scripts/build_calibration_report.py --selftest
```

Then run one bounded real case, inspect every artifact, and validate the skill folder with `skill-creator/scripts/quick_validate.py`.

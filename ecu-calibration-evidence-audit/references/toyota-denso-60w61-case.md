# Worked case: Toyota/DENSO 89663-60W61

This file demonstrates reasoning structure only. Do not reuse any address, scaling, page identity, or tuning value on another Cal ID without a fresh inventory, definition mapping, XREF audit, and physical validation.

## Case identity

- Engine: Toyota 2TR-FE
- ECU family: Toyota/DENSO Gen2 CAN-bus Type 2
- Cal ID: `89663-60W61`
- Firmware size: `0x180000`
- Firmware SHA-256: `9a11a92026e477f9c88a9e648ffe506bb215d04f35cbb8b8a2672003ea034845`
- Analysis language: `V850:LE:32:default`, image base `0x00000000`

## Reusable lessons

1. Inventory first: the workspace had ORI/definition artifacts but no confirmed Stage1 input for the original analysis. An absent version cannot support a diff claim.
2. Normalize address families: a displayed Z-data address may not be referenced, while the earlier descriptor address is directly referenced.
3. Correct boundaries before declaring `NO_XREF`: changing a pedal candidate from an off-by-two start to the true descriptor exposed multiple direct readers.
4. Separate sensor transfer from demand: two accelerator sensor tables were code-linked transfer/plausibility inputs, not response maps.
5. Reason by direction: RPM × air/load -> torque fits a forward model; torque × RPM -> load fits an inverse model; normalized pedal -> request fits Driver Demand.
6. Treat page banks as selectors, not duplicates: a 20-curve request family had state selection, but exact gear/mode names remained unresolved.
7. Keep code and semantics separate: direct XREF/lookup/transform evidence can be verified while the exact OEM symbol remains B+ or B.
8. Use LOG to close active-page and limiter questions before editing.

## Case-local examples

- A request-curve family at `0x3E104–0x3E653` supported a B+ Driver Demand interpretation, while exact page-to-state names remained unresolved.
- Accelerator descriptors at `0x2F360` and `0x2F3C4` had direct code readers and were classified as sensor transfer/plausibility functions, not response maps.
- Several two-dimensional descriptor families were code-linked to load/torque/air-path models and excluded as Driver Demand even when third-party labels contained “requested” or “torque.”

## Write decision

The correct case-level outcome was `LOG_FIRST`/`NO_EDIT` for a response calibration until the active Driver Demand page, encoding gates, selector states, linked TCU behavior, and baseline vehicle LOG were confirmed.

## Generalizable tuning principle

For a drivability-only objective, alter the smallest verified partial-pedal request region and preserve idle/creep, reverse/low-gear precision, WOT convergence, sensor transfers, forward/inverse models, monitoring, and protection. Improved pedal response does not prove proportional airflow or peak-power gain.

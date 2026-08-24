---
name: ecu-map-analysis
description: >-
  Analyze one ECU or TCU map, table, curve, or scalar and explain what it does. Classifies the role as sensor transfer, air model, fuel model, torque model, actuator output, limiter, protection, correction, trim, or axis; identifies axes and units; resolves scaling context; places the map in the calibration order; and gives an edit verdict with direction, magnitude, ECU response, interactions, and risk. Use for 拆解这张 MAP, 这张表是干嘛的, 主控还是限制器, axes, units, one WinOLS screenshot, one 2D or 3D table, or one scalar. Whole Stage plans route to stage1-calibration-matrix; raw and engineering conversion routes to winols-scaling-converter.
---

# ECU Map Analysis (single-table teardown)

The specialist the doctrine and the Stage1 matrix both call when the task is **one table**, not a
whole plan. Its job is to answer, for a single map, the three doctrine questions: *what is this in
the input→model→output chain, what does it depend on upstream, and what happens if I touch it.*

Always run under `ecu-calibration-doctrine`. This skill is its executor for §6 "ecu-map-analysis":
> 拆解单表时必须定位它在"输入→建模→输出"链中的角色（主控/限制器/修正量），并指出它依赖的上游
> 空气模型是否可信。

## The 6-step teardown (do them in order)

### 1. Classify the role (this决定一切后续判断)
Every map is exactly one of these. Misclassifying it is the #1 way to "fix the symptom, create a
new disease" (doctrine first-principle #4).

| Role | What it is | Examples | Edit philosophy |
|------|-----------|----------|-----------------|
| **Axis / breakpoint** | the RPM/load/temp grid other tables index into | RPM axis, load axis, IAT axis | almost never edit; moving a breakpoint re-indexes every table that shares it |
| **Sensor transfer** | raw sensor → physical (input side) | MAF g/s curve, MAP transfer, injector flow/deadtime, TPS | edit to match real hardware (golden order ②); errors here poison everything downstream |
| **Model** | computes an internal physical estimate | VE, air-mass model, virtual/inverse torque, lambda target | the heart; an error becomes a control error (doctrine #3) |
| **Output / actuator** | a commanded actuator target | spark advance, injector PW, wastegate duty, VVT target, throttle plate | edit to change behavior, bounded by the limiters below |
| **Limiter / protection** | a hard or soft ceiling | torque limiter, boost limit, rev limit, EGT/CAT protect, knock retard authority | raise only after proving the thing it protects can take it |
| **Correction / trim** | a multiplier/adder on top of a base | enrichment factor, IAT/baro spark correction, STFT/LTFT, cold-start | a fix here that should be in the base table is a smell |

> If you cannot confidently name the role, you are not ready to edit. Say so and list what's missing.

### 2. Read the axes and units
- Identify X (usually RPM or air-mass/load) and Y (load, ECT, gear, throttle...). State the units.
- Confirm the axes are **monotonic**. A non-monotonic axis means you mis-identified the table or its
  stride/orientation — stop and fix.
- Note the resolution (cell count) — it tells you how finely you can shape behavior.

### 3. Resolve the scaling — delegate, don't guess
Hand the raw↔engineering conversion to **`winols-scaling-converter`** (factor/offset, datatype,
endianness, signed/unsigned). For DENSO files use **`denso-bin-engine`**; for Bosch/Conti/most other
binaries use **`bosch-conti-bin-engine`** (+ `multi-vendor-ecu-engine` for the right endianness).
This skill consumes engineering values; it does not derive them.

### 4. Place it in the golden order & sanity-check shape
Map it onto the 13-step golden order (doctrine §2) and apply the shape self-checks (doctrine §3):
- **Spark table = the inverse of the VE table, smooth, no cliffs.** A sharp step usually hides
  another problem (weak AE, mis-scaled sensor, axis error) — find the root cause before "smoothing".
- Spark trends **down with load, up with RPM**. Fuel/lambda tables trend toward their WOT anchors.
- A torque/load map that disagrees with the part-load↔WOT transition is masking something.

### 5. Trace upstream dependency (the doctrine-mandatory question)
State **what model this table rests on and whether that model is trustworthy**:
- An output/limiter table is only as good as the air model feeding its load axis. If MAF/MAP is
  pegging or injector duty is maxed, editing the output table is polishing a broken foundation.
- For any load/torque/fuel map, ask: is air-mass modeling within ±3% (Banish)? If not, route the
  user to `air-fuel-model-builder` **first**.

### 6. Verdict (the output)
Produce, with **zero vague language** (doctrine §7):
- **Role + one-line function**: "This is the WOT torque *limiter*, clamps modeled torque vs RPM."
- **Safe edit**: direction, region (RPM×load), magnitude (% or absolute), and *why that magnitude*.
- **ECU response**: what the PCM does with the new values.
- **Interactions**: the relevant items from the doctrine §5 linkage checklist (TCU torque monitor,
  knock learning, EGT/CAT, VE/lambda drift, driver-demand, checksum).
- **Risk boundary**: where it gets dangerous and the leading indicator to watch in a LOG.

## Output format
- Lead with the role classification — it frames everything.
- Use the doctrine HTML color-block grid for the map (绿=推荐改 / 黄=谨慎 / 红=禁激进 / 灰=不建议),
  axes in the same orientation/values as the user's screenshot.
- End with only the doctrine §5 linkage items **relevant to this one map** (don't dump the whole list).

## Hard boundaries
- One map at a time. If the user wants the whole car, hand off to `stage1-calibration-matrix`.
- No raw byte math here — that's the bin engines / `winols-scaling-converter`.
- Never declare an edit "safe to flash": checksum is a separate gate (`checksum-crc-engine`).
- If the map's role or upstream model is unverified, say what's missing rather than guessing
  (doctrine: 信息不足先说明缺什么).

---
name: ghidra-tricore-disasm
description: >-
  Use Ghidra or IDA to find ECU maps without DAMOS or A2L and read control or protection logic such as torque monitor, boost limiters, rev limiters, speed limiters, DTC traps, immobiliser, launch, anti-lag, and MAF clamp behavior. Covers loading TriCore, RH850, SH, or PowerPC binaries at the right base, finding map accesses from data-load patterns, tracing routines that read a known map, identifying limiter comparisons, and bridging WinOLS maps to Ghidra with JSON. Use for 无 DAMOS 找表, 反汇编看限速, 限扭逻辑, trace limiter routines, or mark WinOLS maps in Ghidra. Tool setup routes to ghidra-ida-setup.
---

# Ghidra TriCore Disassembly (find maps & read logic)

The advanced layer above WinOLS. Reach for it for exactly two jobs:
1. **Find a map with no DAMOS/A2L** — by following the code that reads it.
2. **Understand a protection/limiter strategy** — read the actual comparison logic so a Stage edit
   addresses the real clamp, not a symptom (doctrine first-principle #4: "对了但理由错了" is the #1
   trap).

Run under `ecu-calibration-doctrine`. Setup (processor module, base address, memory map) is the
companion skill **`ghidra-ida-setup`** — do that first or the disassembly will be wrong.

## Mental model: code reads maps, you follow the code
A calibration map is just data. The ECU reads it through a lookup routine that:
1. loads a **base pointer** to the table,
2. loads the **axis arrays** (X/Y breakpoints),
3. interpolates between cells.

So to find a map you find its **interpolation/lookup call**, and to understand a limiter you find the
**comparison** that clamps a signal against a calibratable threshold.

## Finding maps without a DAMOS (TriCore)
1. **Anchor on something you know.** A known scalar (rev limiter RPM, a boost target in mbar) read in
   WinOLS gives you a value to search for as bytes; find its address, then **find references** to that
   address (Ghidra: References → Show References to Address). The referencing code is the lookup.
2. **Spot the lookup signature.** TriCore lookups load a table base into an address register and
   index with a data register:
   - `movh.a / lea` to build a 32-bit table address, then `ld.w/ld.h/ld.bu` with an offset, inside a
     loop or paired with an axis-search → that's a map read.
   - A repeated **2-stage pattern** (search axis → fetch cell) is the classic 2-D interpolation.
3. **Walk axes ↔ table.** The two arrays loaded just before the table are almost always the X and Y
   axes. Confirm they're **monotonic** (that's also your decode sanity check in the bin engine).
4. **Cross-check in WinOLS.** Once you have an address, decode it with `bosch-conti-bin-engine`
   (right endian from `multi-vendor-ecu-engine`); values must land in a physical band. A map name is
   never proof — the decoded numbers are (doctrine).

> **When step 1 returns `refs=0` — do not conclude the address is wrong.** The most common cause is
> that the image reaches calibration through a **global base register** (`gp`/`tp`/`ep` on V850/RH850,
> `a0`/`a1`/`a9` on TriCore, `GBR` on SH, `r2`/`r13` on PowerPC) loaded once during startup. Then the
> instruction encodes only a **displacement**, so neither xref search nor an address-literal search can
> ever hit. Recover the base from the reset path (`ghidra-ida-setup` §5), recompute
> `absolute = base + disp`, and re-scan. Measured case: a DENSO V850 image gave `refs=0` on all five
> calibration targets until `mov 0xE010,tp` was found — after which one target showed 11 read sites.
> The full locate-and-prove workflow is `physical-constant-locator`.

## Reading protection / limiter logic
The pattern for a limiter is always: **read signal → compare to (calibratable) threshold → clamp /
set flag / cut**. To find and understand one:
1. Identify the **monitored signal** (torque, boost, RPM, MAF, EGT) — often a known RAM global or a
   sensor-conversion output.
2. Find the **comparison** (`jge/jlt/jeq` against a value loaded from the cal region). The compared-
   against value/threshold is your **limiter cal** — that's what to edit, and editing it is safe only
   if the thing it protects can take it (doctrine: limiters raise last).
3. Note the **action**: spark cut, fuel cut, torque clamp, DTC set, limp flag. This tells you the
   LOG symptom to expect (hand to `hp-tuners-log-review`).
4. **Torque monitor / driver-demand** traps are the common Stage1 blocker: a modeled-vs-allowed
   torque comparison that faults if you raise torque without raising the monitor ceiling — and may
   need TCU sync (`tcu-shift-calibration`, doctrine §5).

> Do not patch code to defeat a safety/monitor unless the user has a legitimate, authorized bench/
> motorsport reason and understands the risk. Prefer recalibrating the threshold tables over NOP-ing
> protection logic.

## WinOLS ↔ Ghidra bridge — use the bundled scripts
Two ready-to-run Ghidra scripts in `scripts/` (Jython 2.7 compatible; add the directory in
Script Manager → Manage Script Directories, or `exec(open(r"...").read())` in Window > Python):

- **`scripts/mark_winols_maps.py`** (JSON → Ghidra): batch-marks maps as data + label + comment.
  Accepts three JSON sources auto-detected: a hand-written list
  `[{"name","address","rows","cols","width"}]`, or the `--json` output of
  `winols-ols-parser/scripts/parse_ols.py` / `damos-a2l-parser/scripts/parse_a2l.py` directly
  (dims/datatype/size_bytes are normalized; A2L float32 maps get FloatDataType).
- **`scripts/export_maps_to_winols.py`** (Ghidra → WinOLS): exports every `map_`-prefixed label
  with its defined-data size to CSV (`name;address_hex;size_bytes`) + JSON (same schema as the
  parsers, so it can be fed back into `mark_winols_maps.py`). Exported addresses are Ghidra
  memory addresses — subtract the image base before using them as bin file offsets.

Both support `python <script> --selftest` outside Ghidra (tests the pure normalization logic).
Existing community scripts (e.g. Med9 Ghidra scripts for MED9.1) automate map marking for
specific platforms — see `ghidra-ida-setup` for resources.

## Output discipline
- Report the **address + how you confirmed it** (xref path, axis monotonicity, decoded band), not
  just an address. An unconfirmed address is a guess.
- For a limiter: state the monitored signal, the threshold cal location, the action, and the LOG
  symptom — then route the edit to `bosch-conti-bin-engine` and validation to `hp-tuners-log-review`.
- This skill finds/explains; the byte edit and checksum happen in the bin engine +
  `checksum-crc-engine`.

## Boundaries
- TriCore-focused but the workflow (xref the value → find the lookup → confirm axes) is core-agnostic;
  for SH/PowerPC/RH850 the instruction mnemonics differ — see `ghidra-ida-setup` for those cores.
- Not a substitute for a DAMOS when one exists — `ecu-calib-finder` first; disassemble only when
  there's no definition or you need the logic.

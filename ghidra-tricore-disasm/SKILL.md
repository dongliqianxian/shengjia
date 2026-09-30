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
> ever hit. Recover the base from the reset path (`ghidra-ida-setup` §5 全局基址寄存器), recompute
> `absolute = base + disp`, and re-scan. Measured case: a DENSO V850 image gave `refs=0` on all five
> calibration targets until `mov 0xE010,tp` was found — after which one target showed 11 read sites.
> The full locate-and-prove workflow is `physical-constant-locator`.

## Hard constraint: decompiler output cannot answer constant/scaling questions

**反编译 ≠ 反汇编。** 这不是偏好问题,是工具的设计行为(doctrine §13.2 逆向纪律):

- 反编译器**按设计丢弃字面量池**(literal pool) —— 刻度常量就住在那里
- **未被引用的常量拿不到符号**
- **PC 相对加载会被渲染成裸符号名**,真实数值不出现在输出里

> **规则**:回答「这个刻度/LSB 是多少」「这个钳制常量是多少」时,
> **必须走反汇编清单或原始字节**(`ConstantSearch` / `Memory` 类工具),
> **不得**引用反编译产物。用反编译回答刻度问题 = 拿一个按设计丢掉了答案的视图去找答案。

刻度结论还要满足 doctrine §13.1:**两条独立证据,至少一条来自固件自身算术**
(`/0x400`、`<<3`、共享钳制常量),只有一条就标 `(est.)` 并写进产出文件本身。

## Preflight: mark data regions BEFORE analysis (防自制假 xref)

把表当代码反汇编会**制造出看起来很真的交叉引用**。

> **实证(5eat)**:一次忽略这点的扫描产生了 **77 个**指向换挡表数组的"引用者",
> 每一个都是扫描在读**它自己错误解码出来的指针**。

**前置步骤(每个镜像做一次,在跑任何 xref 分析之前)**:

1. 从 `blind-map-finder` 取数据区范围表(它的候选分层步骤会导出)
2. 在 Ghidra 里把这些范围标为 data(`Data > Settings`,或脚本批量 `createData`)
3. **再**开始反汇编与 xref 追踪

跳过这一步,盲扫与反汇编这两条腿就不再独立 —— 它们会互相污染,
而污染的结果**看起来是强证据**(两条腿都指向同一个地址,其实是同一个错误的回声)。

> *依据 `ecu-calibration-doctrine/references/re-discipline.md` 第三节「反汇编 vs 反编译」3.1/3.2；实证来源 TomFLV/5eat-tcu-reverse-engineering。*

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
   need TCU sync (`tcu-shift-calibration`, doctrine §5 联动风险清单).

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

## ReVa (Ghidra MCP) — 什么时候能用，什么时候必须回反汇编清单

`cyberkaida/reverse-engineering-assistant` 是 Ghidra 扩展，把 xref / 常量搜索 / 内存读取等
暴露给 Agent（Claude Code / Kimi 等）直接调用，补上确认链里「ghidra 坐实」这一环。**硬要求 Ghidra ≥ 12.0。**

```
盲扫候选  →  解码自检  →  【ghidra xref 坐实】  →  功能判定  →  scaling
                              ← ReVa 补这里
```

### 能用 ReVa 回答的问题

| 问题 | Tool Provider | 作用 |
|---|---|---|
| 谁读了这个地址？ | `CrossReferences` | **把候选从证据 B 级升到 A 级的那一步** |
| 这个刻度常量在哪？ | `ConstantSearch` | 找 `/0x400`、`<<3` 类**固件自身算术证据**（§13.1 两条证据之一） |
| 这几个字节到底是什么？ | `Memory` | 读原始字节，**绕过反编译器的字面量池丢失** |
| 这张表的值流向哪？ | `DataFlow` | 追下游 |
| ORI vs MOD 差异 | `Diff` | 配合 `reference-tune-porting` |

### 必须回到反汇编清单 / 原始字节的问题

> **`Decompiler` 类工具不得用来回答刻度、常量、指令流问题**（见上文硬约束节）。
> 反编译器**按设计**丢弃字面量池 —— 用它找刻度 = 拿一个已经把答案丢掉的视图去找答案。
> ReVa 让调用反编译器变得很容易，**这恰恰放大了这个陷阱**：结果来得快，看起来也很确定。

**判据**：问题里出现「多少」「什么值」「哪个常量」「LSB」「scaling」→ 走 `ConstantSearch` / `Memory`，
不看 `Decompiler` 输出。问题里出现「什么逻辑」「哪个分支」「谁调用谁」→ `Decompiler` 可用。

### 使用纪律

1. **写类工具先全关**（`Comment` / `Bookmark` / `DataType` / `Structure` / `Script` /
   `ImportExport`、以及 `Symbol` 的重命名）。分析阶段不需要写回 Ghidra 数据库，
   写回会让「工具改过的」和「原始事实」混在一起。
2. **preflight 仍然要做** —— ReVa 读的是 Ghidra 数据库，如果数据区没先标成 data，
   它读到的 xref 里就混着**自制假 xref**（见上文 77 个假引用者的实证）。**ReVa 不会替你做这一步。**
3. **ReVa 的输出仍然是证据，不是结论** —— xref 命中把候选升到 A 级，
   但功能判定仍交 `ecu-map-analysis`，刻度仍需 §13.1 的两条独立证据。

> 装配与白名单细节见项目里的 `REVA-SETUP.md`。**未安装时本节不影响其它流程** ——
> 手动在 Ghidra GUI 里做同样的查询，纪律完全一样。

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

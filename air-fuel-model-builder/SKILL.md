---
name: air-fuel-model-builder
description: >-
  Build, correct, and validate the engine air and fuel model before changing output maps. Covers speed-density, VE, MAP, IAT, MAF scaling, alpha-N, injector flow, deadtime, voltage compensation, required fuel mass, pulse width, wideband correction, and STFT/LTFT based VE or MAF rescale toward the Banish air-modeling target. Use when the task is about VE tables, MAF transfer functions, injector characterization, fuel trims, MAF pegging, injector duty, lambda being wrong everywhere, speed-density conversion, 空气模型, VE 表标定, 喷油器流量死区电压补偿. Includes scripts/airfuel.py for speed-density air mass, gas density, injector PW, trim correction, and MAF rescale math.
---

# Air & Fuel Model Builder

The doctrine says it plainly: **空气流量建模是第一优先级，凌驾一切** (first-principle #3). Almost
every fuel and spark number is computed on top of the air model. This skill builds that model,
corrects it against real data, and validates it to the **±3% Banish standard** *before* anyone
touches a torque, boost, or spark output table.

Always run under `ecu-calibration-doctrine`. This is its executor for golden-order steps:
②（组件传递函数）④（燃油建模）⑤（空气建模，花最多时间）⑥（部分负荷，先于 WOT）.

## The chain (never skip a link — doctrine air-fuel chain A)
```
sensor transfer correct?  ->  air model accurate (±3%)?  ->  injector model accurate?  ->  THEN λ target
   (MAF curve /                 (VE or MAF g/s)              (flow + deadtime +
    MAP+IAT)                                                  voltage comp)
```
Any broken link makes every downstream edit rest on a lie. Diagnose left-to-right.

## 0. 建模前先固定测量环境（doctrine §10 — 跳过这步等于打移动靶）

改任何 VE/MAF 数字**之前**，先把环境钉死，否则你测到的误差不是空气模型的误差：

1. **强制开环** — 闭环使能表推到量程极端（开环最小节气门 0% / 闭环最低水温 300°）。
   开环下 **实测 λ 本身就是修正系数**，数学最简；闭环 PID 会与你对抗（大凸轮 / HEGO 挪远时尤甚）。
2. **λ 目标拉平** — 发动机可能走到的区域**全设同一个目标**（建模期常用 λ=1）。目标是动的 = 打移动靶。
3. **从 ≥1500 rpm 起步，跳过怠速** — 怠速是最难控的工况；必要时先锁点火助稳定。
4. **热态稳态** — 暖车完毕、启动加浓退出，水温/油温/进气温稳定，避免其它修正器混进来。
5. **粗调 → 精修** — 建模期用「电锯」不是「砂纸」：整体偏 10% 就整条加 10%，看趋势外推。
   **到了精修阶段还要大改 = 前面错了或车有硬件问题**（doctrine 原则#10），回头查，别在修正层打补丁。
6. **负荷压在 ≤60%** — 过浓久了洗缸伤活塞环，过稀抬排温伤气门/歧管/催化器。
7. **无台架 → 只能缩放不能重塑形状**（doctrine §9）：路试稳态点抽样 + 趋势外推，
   **必须标注哪些 cell 是实测、哪些是外推**。

> **VE 轴断点规划**（常被忽略）：分辨率要花在**效率变化最快的地方**（刚离开怠速那一段），
> 不是均匀铺开。大凸轮只能拉到 55 kPa 真空时，10/20/30/40 kPa 那几列是浪费——
> 挪到 50–100 kPa 之间去。轴规划不好，后期驾驶性问题是插值造成的，改数值治不好。

---

## 1. Pick the air-mass strategy

| Strategy | When | Core idea |
|----------|------|-----------|
| **Speed-density (SD)** | most modern ECUs, FI, big cam | air mass from `VE × displacement × RPM × manifold density`; needs a good VE table + MAP + IAT |
| **MAF (mass air flow)** | factory MAF cars, simpler | air mass measured directly; calibrate the MAF transfer curve (Hz/V → g/s) |
| **Alpha-N** | ITBs / no usable MAP signal | air mass from throttle angle × RPM; least robust, last resort |
| **Blended (MAF+SD)** | many OEM (VW, GM) | MAF trims SD or vice-versa; fix BOTH or trims fight each other |

## 2. Speed-density: the equations (implemented in `scripts/airfuel.py`)
Manifold air density from ideal gas, then trapped air mass per cylinder per cycle:
```
ρ_manifold = (MAP_Pa) / (R_specific_air × IAT_K)        # R_air ≈ 287.05 J/kg·K
m_air_cycle = VE × (V_displacement / n_cyl) × ρ_manifold # kg of air per cyl per intake event
m_air_dot   = m_air_cycle × n_cyl × (RPM/2) / 60         # kg/s (4-stroke: one intake per 2 rev)
```
Required fuel and injector pulse width:
```
m_fuel = m_air_cycle / (AFR_target)          # AFR_target = 14.68 × λ_target for gasoline
PW     = (m_fuel / injector_flow_kg_s) + deadtime(V_batt)
```
> **Load, not boost** (doctrine #6): express the air model on the *load* axis (air mass or % of max
> air mass). Boost is just a restriction metric; cylinder pressure tracks load.

## 3. MAF scaling — the one rule that prevents chasing your tail
When you rescale MAF, **scale MAF transfer, injector flow, and (if modeled) displacement by the same
factor; do NOT touch injector deadtime or its voltage-compensation curve** (those are physical, not
flow-proportional). This is the same rule `winols-scaling-converter` enforces. Route raw↔engineering
of the MAF/injector tables to `winols-scaling-converter` (or the bin engines); do the *physics* here.

## 4. Injector characterization (golden order ②)
Get these into the ECU *before* modeling air, or the air model will absorb injector error:
- **Static flow** (cc/min or g/s at reference pressure) — rescale for actual fuel pressure:
  `flow_actual = flow_ref × sqrt(P_actual / P_ref)`.
- **Deadtime / offset vs battery voltage** — a curve, not a constant. Wrong deadtime corrupts idle
  and low-PW cells most.
- **Small-pulse non-linearity** — the low-PW region where flow stops being linear.
- **Fuel-system sizing (BSFC, Banish Ch.2)** — before picking injectors/pump: `total lb/hr = power_hp ×
  BSFC (~0.5 NA gasoline WOT)`, `per-injector = total / cyl`, size with **20% headroom** (÷0.80),
  pump = total ÷ 0.80. `airfuel.py fuel_system_sizing()` does it (also cc/min, gal/hr, L/hr). Undersized
  injectors peg at 100% duty → forced lean at WOT; keep steady-state duty **< 85%**.

## 5. Correct the model from real data (close the loop, golden order ⑪)
Iterate until the model is right, then stop touching it:
- **From wideband**: `VE_new = VE_old × (λ_measured / λ_target)` cell by cell (the engine helper does
  this). Converge, don't overshoot — change ≤ the measured error each pass.
- **From fuel trims**: fold STFT+LTFT back into VE/MAF (`correction = (1 + (STFT+LTFT)/100)`), then
  the trims should collapse toward **< 3%** (doctrine §5). Trims that stay large = the base model is
  still wrong (or there's an unmetered leak / injector error) — do not just widen trim authority.
- **Validate**: air-modeling error **< 5% acceptable, < 3% ideal, < 1% OEM**. State which you hit.

> **IAT 补偿必须单独建，不得靠改基础 VE 表。** 在 80°F 标好、跑到 50°F 变稀时，
> 要改的是 **IAT 燃油修正曲线**；此时改基础 VE 表 → 温度回升后必然变浓，等于把一个温度点的
> 误差烙进了全温度域。同理适用于所有修正层（ECT / 大气压 / 电压）：**基础表管基础，修正表管修正。**

---

## 6. Part-load before WOT (golden order ⑥ before ⑧)
Get cruise/part-load air & fuel correct and the transition smooth *first*. A WOT table evaluated on a
broken part-load model is meaningless. Only after part-load + idle are solid do you hand WOT air/fuel
demand to `stage1-calibration-matrix`.

## Using the helper
```bash
python scripts/airfuel.py --selftest          # runs the equation checks
```
```python
from airfuel import sd_airmass_dot, required_fuel_mass, injector_pw, ve_correction, maf_rescale
# air mass at 5000 rpm, 200 kPa MAP, 40°C IAT, 2.0 L, 4 cyl, VE 0.95:
mdot = sd_airmass_dot(rpm=5000, map_kpa=200, iat_c=40, disp_l=2.0, n_cyl=4, ve=0.95)
# corrected VE cell from a wideband reading:
ve_new = ve_correction(ve_old=0.92, lambda_meas=0.88, lambda_target=0.85)
```

## Outputs & boundaries
- Output the VE/MAF model with the doctrine HTML color-block grid; flag cells where the model is
  unvalidated (gray) vs confirmed (green).
- End with the doctrine §5 items relevant to air/fuel: VE→λ drift, MAF/MAP量程是否被超(pegging),
  injector duty 是否打满, ±3% air-modeling error, checksum gate.
- This skill produces a trustworthy *model*; it does not write Stage power numbers (that's
  `stage1-calibration-matrix`) and does not do byte I/O (that's the bin engines).
- If sensor data / wideband / trims are missing, say what's needed — never fabricate a VE table.

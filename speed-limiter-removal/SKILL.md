---
name: speed-limiter-removal
description: >
  最高限速定位与解除技能：在 ECU/TCU bin 里定位车速限制器（Vmax）标定并给出解除方法。
  当用户说"解除最高限速/找限速标定/限速在哪/Vmax/电子限速/断油限速/210 250 180 限速/
  提高最高车速/去掉速度限制/speed limiter/top speed/限速多少/限速地址"时，必须调用本技能。
  即使只说"解限速"，也要调用。
  本技能是【限速域定位 + 解除策略 + 安全边界】：知道限速多为标量(高误报)、软切(降扭)vs硬切
  (断油/断火)、常有多阈值，用共享引擎 value_locate.py 给候选，并强调 diff 与 ghidra 才是可靠确认。
  它不做字节读写（bosch-conti-bin-engine）、不做单表拆解（ecu-map-analysis）、
  不重算 checksum（checksum-crc-engine）、TCU 侧限速交（tcu-shift-calibration）。
  运行于 ecu-calibration-doctrine 之下。
---

# 最高限速（Vmax）定位与解除

## 0. 安全边界（先读）

- **乘用车**在赛道/出口/法规允许场景解除最高限速通常是**合法改装**——本技能支持。
- **安全**：把 Vmax 抬到**超过轮胎速度等级**是真实事故风险（爆胎）；也要核对刹车/悬挂/散热能否支撑。
  解除限速前提醒用户确认硬件与轮胎等级。
- 本技能只做**动力侧车速限制器**；若限速由变速箱实现，交 `tcu-shift-calibration`。

## 1. 限速在 ECU 里的形态

| 形态 | 说明 |
|------|------|
| **Vmax 标量/小曲线** | 限速阈值，常 km/h（部分 mph）。常见值 130/155(mph)/180/200/210/230/250 等 |
| **软切（torque/fuel 降）** | 接近限速时**逐渐降扭/减油**，平顺回落——有"起始降扭车速 + 斜率" |
| **硬切（断油/断火）** | 到阈值**猛断油/断火**，顿挫明显 |
| **多阈值** | 常见"警告/预降 + 硬切"两级，甚至**胎速等级/备胎/冬季模式/分市场**多个限速值并存 |
| 车速来源 | 轮速经 ABS→CAN 进 ECU；逻辑是 `车速信号 > Vmax_cal → 动作` |

## 2. 定位（共享引擎，但标量搜索天生高误报）

```bash
# 引擎在本技能 scripts。已知限速值(如 250 km/h)搜候选：
python scripts/value_locate.py --bin ECU.bin --preset vehicle_speed --value 250 --tol 3
# ★金标准★ 有原厂 + 已解限文件时,diff 出变动处并按速度缩放解码"改前→改后"：
python scripts/value_locate.py --bin STOCK.bin --diff DELIMITED.bin --preset vehicle_speed
```

引擎多缩放×双字节序穷举（`km/h 直存 / ÷100 / ÷128 / ÷256 / ×0.0625 / mph`），按可信度排序，
并对**位宽稀有度**加分（u16 巧合远罕于 u8=0xFA，减少误报）。但仍须知道：

> **标量车速搜索天生高误报**——`0xFA`(250) 在 bin 里遍地都是。value_locate 给的是**候选**，
> **不是答案**。可靠确认顺序：
> 1. **diff 对比法**（原厂 vs 已解限文件）—— 最可靠，变动处一目了然。
> 2. **`ghidra-tricore-disasm` 找车速比较逻辑** —— 定位 `车速 > 阈值` 的比较指令，看清是软切还是硬切、
>    阈值 cal 存哪，这才是"知道为什么被限"而非猜地址。
> 3. **A2L/DAMOS 标签**（`damos-a2l-parser`，Bosch 常见 `vmax`/`VMAX`/车速限制类命名，exact 经 A2L）。
> 4. 改后**台架/实测**验证。

## 3. 解除策略

1. **确认是软切还是硬切**（ghidra 看动作）：
   - 硬切：把 Vmax 阈值抬到车辆物理达不到的值（如 300+ km/h）即可。
   - 软切：**还要抬"起始降扭车速"**，否则临近原限速仍被降扭，车速上不去。
2. **处理多阈值**：胎速/备胎/警告/分市场限速可能并存——只改一个不够，逐个核对（value_locate 的
   聚类提示能帮你发现邻近的第二个阈值）。
3. **检查是否 TCU 侧**：部分车靠变速箱锁挡/降扭限速 → 转 `tcu-shift-calibration`。
4. 改完 **checksum 必重算**（`checksum-crc-engine` / WinOLS 插件），否则不可刷。字节改写交
   `bosch-conti-bin-engine`；单表语义交 `ecu-map-analysis`；若属整套动力方案交 `stage1-calibration-matrix`。

## 4. 与其他技能

定位候选(本技能 + value_locate.py) → 逻辑确认(`ghidra-tricore-disasm`)/对比(diff) → 标签(`damos-a2l-parser`)
→ 拆解(`ecu-map-analysis`) → 字节改写(bin 引擎) → checksum(`checksum-crc-engine`)。
TCU 侧限速转 `tcu-shift-calibration`。所有结论受 `ecu-calibration-doctrine` 约束。

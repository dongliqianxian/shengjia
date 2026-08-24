---
name: winols-scaling-converter
description: >
  WinOLS 系数（factor）、偏移（offset）、单位换算分析技能。当用户提到 WinOLS、
  ECM Titanium、BitEdit、Swiftec、ECUEdit、MapDevil 中看到的 raw 值、factor、offset、
  unit、Multiplier、Divisor、scaling、转换公式、原始字节、HEX、Int8/Int16/Float、
  Big-endian / Little-endian、补码、有符号/无符号、表格 Z 值不对、单位看不懂、
  从一个工具换到另一个工具数值变了 等情况时，必须立即调用本技能。
  即使用户只贴出一组奇怪的数字（如 0.0156, 1024, -32768）问"这是什么单位"，也要调用。
  本技能专注于 raw ↔ 工程值的换算，不输出调教方案。
---

# WinOLS / 通用 ECU raw 值与工程量换算

## 角色

熟悉 Bosch / DENSO / Siemens / Delphi / Continental 平台数据存储格式的 ECU 标定工程师，精通 WinOLS / ECM Titanium / BitEdit 的 scaling 设置。

## 核心公式

```
工程量 = (raw × factor) + offset
raw    = (工程量 − offset) / factor
```

工具之间的数值差异，本质都是 **factor / offset / 数据类型 / 字节序** 四件事的差异。

## 输入识别（先问清楚 4 件事）

如果用户没说清，按顺序问：

1. **数据类型**：Int8 / Uint8 / Int16 / Uint16 / Int32 / Float32 / Float64
2. **字节序**：**跟 MCU 核走，不跟车标**（`multi-vendor-ecu-engine` 原则#1）——
   Infineon TriCore / AURIX / C167 / ST10 = **小端**（Bosch EDC17/MED17/MD1/MG1、Conti SIMOS、
   Delphi DCM/MT 全在此列）；Renesas SH（SH7055/7058）/ PowerPC MPC5xx = **大端**
   （日产/斯巴鲁/三菱 Hitachi、Visteon 福特 PCM、部分 Marelli）；RH850/V850 通常小端须确认。
   不确定时先按小端解，轴不单调或值域离谱再换大端——**能让轴单调、值落物理带的那个才是对的**。
3. **是否有符号**：负值是否合法（点火角、扭矩修正可负；负荷、转速一般无符号）
4. **factor / offset**：工具里已经填好，还是要反推

## 常见 Scaling 模板（按物理量速查）

| 物理量 | 典型 factor | 典型 offset | 工程量范围 | 数据类型 |
|--------|------------|-------------|-----------|---------|
| RPM | 1 / 0.25 | 0 | 0–8000 | Uint16 |
| Load (%) | 0.0078125 (1/128) / 0.1 | 0 | 0–200 | Uint8/Uint16 |
| Boost (mbar abs) | 1 / 0.1 | 0 / -1013 | 0–3500 | Uint16 |
| Boost (relative bar) | 0.001 / 0.01 | 0 | -1 ~ +3 | Int16 |
| MAF (kg/h 或 g/s) | 0.1 / 0.25 | 0 | 0–1500 / 0–500 | Uint16 |
| MAP sensor (mbar) | 1 | 0 | 0–3000 | Uint16 |
| Spark Advance (°BTDC) | 0.375 / 0.75 / 0.1 | 0 / -64 | -10 ~ +60 | Int8 |
| Lambda (λ) | 0.0078125 (1/128) / 0.001 | 0 / 0.5 | 0.7–1.5 | Uint16 |
| 1/Lambda | 0.0078125 | 0 | 0.65–1.45 | Uint16 |
| AFR (gasoline) | factor × 14.7 | — | 10–18 | — |
| Injection Quantity (mg/stk) | 0.01 / 0.001 | 0 | 0–80 | Uint16 |
| Rail Pressure (bar) | 1 / 0.1 | 0 | 0–2500 | Uint16 |
| SOI (°BTDC) | 0.023438 / 0.1 | 0 | -10 ~ +30 | Int16 |
| WGDC / Throttle (%) | 0.00390625 (1/256) / 0.1 | 0 | 0–100 | Uint8/Uint16 |
| Torque (Nm) | 0.5 / 1 | 0 / -1000 | -200 ~ +800 | Int16 |
| EGT (°C) | 0.75 / 1 | -40 / 0 | 0–1100 | Uint16 |
| IAT / Coolant (°C) | 0.75 / 1 | -40 / -50 | -40 ~ +150 | Uint8/Int16 |
| Time (μs) | 1 / 4 | 0 | 100–30000 | Uint16 |

## 反推 factor / offset 的方法

当只有 raw 值、没有 scaling 信息时：

### 方法 A：物理量边界反推
1. 推测该 MAP 的物理量（看 X/Y 轴）
2. 找该物理量的典型量程（如 Boost 0~3000 mbar）
3. 看 raw 值最大最小：raw_max / phys_max ≈ factor
4. 验证 offset：phys_min − raw_min × factor

### 方法 B：与已知点对照
1. 找一个已知工况（如怠速 RPM ≈ 800、MAP ≈ 350 mbar）
2. 在表格里找到对应位置的 raw 值
3. 联立两个已知点解 factor / offset

### 方法 C：从对应 sensor 表反推
传感器特性表（如 MAF 频率 → kg/h）的 scaling 通常和主用 MAP 一致。

## 批量换算脚本（整条轴/整张表不要手算）

单点换算可以口算，整条 16 点轴、整张表手算必错。批量操作一律跑 `scripts/scale_convert.py`：

```bash
# raw 列表 -> 工程值
python scripts/scale_convert.py to-phys --factor 0.75 --offset -128 --values "0,64,128,192,255"

# 工程值 -> raw（按数据类型钳位，越界会明确告警）
python scripts/scale_convert.py to-raw --factor 0.0078125 --dtype u16 --values "0.75,0.85,1.0"

# hex 字节串直接解码（--dtype 定宽度/符号，--endian 定字节序）
python scripts/scale_convert.py decode --hex "00 20 00 40 FF 7F" --dtype u16 --endian le --factor 0.001526

# 方法 B 的自动化：两已知点反解 factor/offset，第三个点起自动做校验
python scripts/scale_convert.py solve --points "0:-128 255:63.25 128:-32"

python scripts/scale_convert.py --selftest
```

脚本只做线性 `phys = raw×factor + offset`；查表/分段/多项式换算不适用，按本技能正文
人工处理。solve 反解的结果必须再用第三个已知点或传感器表交叉验证后才可用于写入。

## 跨工具差异排查

| 现象 | 大概率原因 |
|------|----------|
| WinOLS 显示对，ECM Titanium 显示一半 | factor 差 2 倍（Int8 vs Int16） |
| 显示值是真实值的 1/128 或 ×128 | factor 写错了 1/128 模板 |
| 全是大数字（>32000） | 字节序反了（Big/Little endian） |
| 全是负数或跳到 65535 | 有符号 / 无符号识别错 |
| 工程量正确但走势反了 | 表格行列顺序反了，或 1/λ 当成 λ |
| BitEdit 显示对，但写回 ECU 不动 | offset 错，写入值落在 ECU 不识别区 |

## 安全提示

- 不确定 scaling 时**不要写入 ECU**，先纯软件验证
- 同一张 MAP 在不同工具 scaling 设置必须严格一致，否则改完写回会变成另一张表
- 修改 scaling 模板本身（factor/offset 字段）不会修改 ECU 行为，只影响显示；但写入时按当前 scaling 写入字节，错误 scaling = 错误数据

## 输出格式

```
### raw 值识别
- 数据类型推断：…（依据：取值范围、是否有负数、表格大小）
- 字节序推断：…
- 平台特征：Bosch MEDxxx / DENSO / Siemens SIMOSxxx / Delphi DCMxxx

### 推荐 scaling
- factor = …
- offset = …
- 工程量范围 = …
- 验证点：raw=X → 工程量=Y（与已知工况对照）

### 公式
工程量 = (raw × factor) + offset = …

### 单位含义
…（factor / lambda / Nm / mg·stk / mbar / ratio / deg / μs 中的哪个，给出工程含义）

### 风险提示
…（如 scaling 不唯一、需要交叉验证）
```

---
name: oem-strategy-library
description: >
  OEM 特定扭矩/动力总成控制策略知识库。涵盖 Bosch / DENSO / Continental /
  Siemens / Delphi / Marelli / Hitachi / Mitsubishi / Visteon 等主流 ECU
  制造商的扭矩架构、扭矩模型结构、标定 MAP 命名惯例、策略逻辑路径和
  常见限制器层级。
  当用户问"这个 ECU 的扭矩模型结构是什么"、"VAG 的扭矩限制器层级"、
  "丰田的扭矩请求链"、"BMW DME 的扭矩模型"、"Bosch MED17 的扭矩协调逻辑"、
  "这个 MAP 在 Bosch 系统里的命名规律"、"DENSO 和 Bosch 的策略差异"、
  "某车型的常见限扭位置"时，必须调用本技能。
  本技能不解析具体二进制（那交给 bin-engine），不做截图识别
  （winols-screenshot-analyzer），不做单表拆解（ecu-map-analysis）——
  返回 OEM 级策略参考，供其他技能作为上下文使用。
  运行于 ecu-calibration-doctrine 之下，作为其"不同厂商策略差异"的执行层。
---

# OEM 策略知识库（OEM Strategy Library）

**核心事实：所有 ECU 的物理原理相同，但策略架构和 MAP 命名完全不同。**
本技能提供"翻译者"角色——当你面对一个陌生平台时，告诉你"这东西在 Bosch 里
叫 KFZW，在 DENSO 里是 Adv_Base_Max，但都是点火角基础表。"

---

## 1. 按 ECU 品牌的制造商分布

### Bosch

| 车型群 | ECU 系列 | 常见发动机 |
|--------|---------|-----------|
| VAG | MED17 / MG1 / MD1 / EDC17 | EA888 / EA855 / EA839 / TDI 全系 |
| BMW | DME MEVD17 / MG1 / DDE | N20 / B48 / B58 / N55 / S55 / S63 |
| Mercedes | ME9.7 / MED40 / MED17.7 / MG1 | M271 / M276 / M177 / M256 / OM651 |
| PSA / FCA | MED17.4 / EDC17C4x | EP6 / DW10 |
| Ford EU | MED17 / MG1 / EDC17 | EcoBoost 全系 |
| GM EU | MED17 / EDC17 | A14NET / LTG |

### DENSO

| 车型群 | ECU 系列 | 常见发动机 |
|--------|---------|-----------|
| Toyota / Lexus | Gen3 / Gen4 / Gen5 / Gen6 | 2TR-FE / 3UR-FE / V35A / A25A |
| Honda | R7F / SH705x | K20C / L15B / J35Y |
| Subaru (部分) | R7F701 | FA20 / FA24 |
| Suzuki | Gen3/Gen4 | K14C / K15C |

### Continental / Siemens

| 车型群 | ECU 系列 | 备注 |
|--------|---------|------|
| Ford US | PCM / Simos | 3.5 EcoBoost / 5.0 Coyote |
| BMW MSS | MSS54/MSS60/ME9 | 早期 BMW |
| VAG SIMOS | SIMOS 全系 | 早期 VAG |
| GM | E37/E38/E39/E67/E80 | 北美 V8 |

### Delphi

| 车型群 | ECU 系列 | 常见发动机 |
|--------|---------|-----------|
| GM Global | E67/E78/E82/E92/E99 | LS / LT 全系 |
| Volvo | DCM / PCM | B4204 / D5244 |
| Renault | DCM | H4Bt |

### Marelli

| 车型群 | ECU 系列 | 常见发动机 |
|--------|---------|-----------|
| FCA/Stellantis | 6xx/8x/9x/10x | Firefly / GME / Hurricane |
| Ferrari | Marelli | F154 V8/V12 |
| Maserati | Marelli | 各发动机 |

---

## 2. 扭矩模型架构

### 2.1 Bosch VAG 扭矩模型

VAG 的扭矩架构是最复杂的分层模型，也是理解其他平台的参照系。

```
踏板 → 驾驶员需求扭矩 (KF_DV_MMD)
          |
          v
  ┌─ 扭矩协调器 ──────────────────────┐
  │  采集各方扭矩请求，取最小值：       │
  │  - 驾驶员需求                       │
  │  - 变速箱干预（换挡降扭）           │
  │  - EGT/CAT 保护                     │
  │  - 车速/转速限制                    │
  │  - ESP/ASR 干预                     │
  └────────────────────────────────────┘
          | 最终扭矩请求
          v
  ┌─ 扭矩→空气（Inverse Torque Model）─┐
  │  目标扭矩 → 目标空气流量             │
  └────────────────────────────────────┘
          | 目标空气
          v
  ┌─ 空气系统 ──────────────────────────┐
  │  节气门 → WGDC → 增压 → 实际进气    │
  └────────────────────────────────────┘
          | 实际空气
          v
  ┌─ 燃油/点火 ────────────────────────┐
  │  Lambda → 喷油 | Spark → 扭矩      │
  │  → 实际扭矩反馈（Virtual Torque）   │
  └────────────────────────────────────┘
          | 实际扭矩
          v
  ┌─ 变速箱扭矩监控 ──────────────────┐
  │  档位限扭 | 变矩器锁止              │
  └────────────────────────────────────┘
```

**VAG 关键 MAP**：

| VAG 名 | 功能 | 通用名 |
|--------|------|--------|
| KF_DV_MMD | 驾驶员需求扭矩 MAP | Driver Wish |
| KF_UMXW | 发动机最大扭矩 | Max Engine Torque |
| KFTLRS | 扭矩限制器 | Torque Limiter |
| KF_URWMX | WOT 扭矩限制 | WOT Torque Limit |
| KFZW | 基础点火角 | Base Spark |
| KFDL | Lambda 目标 | Lambda Target |
| KF_WG | WGDC 基础 | Wastegate Duty |
| KFLD | 负荷 MAP | Load Model |
| KF_VTGTMX | 增压目标 | Boost Target |
| LDRXN | 转速限制器 | RPM Limiter |
| KF_GTRL | 变速箱限制 | Gearbox Torque |
| KF_EGT | EGT 保护 | EGT Protection |
| KF_BASKF | 爆震基础 | Knock Base |

**VAG 限制器优先级（最终扭矩取最小值）**：

```
1. KF_DV_MMD: 驾驶员需求
2. KF_UMXW: 发动机物理上限
3. KFTLRS: 转速+负荷限制
4. LDRXN: 转速限制
5. KF_URWMX: WOT 限制
6. KF_GTRL: 变速箱档位限制
7. 动态干预 (ESP/ASR)
8. EGT/CAT 热保护
```

**修改铁律**：必须协调 KF_DV_MMD + KF_UMXW + KF_URWMX + KFTLRS + KF_GTRL，
单一 MAP 修改不会生效。

---

### 2.2 DENSO Toyota/Lexus 扭矩模型

与 VAG 不同，DENSO 没有显式分层协调器，通过多张需求/限制 MAP 取最小值。

```
踏板 → Pedal_Map (加速踏板→需求扭矩)
          |
          v
  ┌─ 扭矩请求链 ────────────────────────┐
  │  取以下最小值生效：                   │
  │  1. 驾驶员需求（Pedal→Torque 映射）  │
  │  2. Standard Torque（日常运行标准）   │
  │  3. Optimal Torque（WOT 最优扭矩）    │
  │  4. Max Torque Limit（RPM×ECT限制）   │
  │  5. TCU Torque Limit（档位限制）       │
  └────────────────────────────────────┘
          | 最终请求扭矩
          v
  ┌─ 扭矩→负荷 ──────────────────────────┐
  │  Requested Load #1/#2                │
  └────────────────────────────────────┘
          | 目标负荷
          v
  ┌─ 空气/点火/燃油 ────────────────────┐
  │  VVT Target → Throttle              │
  │  → Lambda → Spark → Torque Output   │
  └────────────────────────────────────┘
```

**DENSO 关键 MAP**：

| DENSO MAP | 功能 |
|-----------|------|
| Pedal_Map #1/#2 | 踏板→扭矩（Normal/Sport 模式） |
| Std_Trq_Map | 标准扭矩（日常驾驶） |
| Opt_Trq_Map | 最优扭矩（WOT 性能） |
| Max_Trq_Limit | 最大扭矩限制（RPM × Coolant Temp） |
| Requested_Load #1 | 目标负荷（主） |
| Requested_Load #2 | 目标负荷（备/过渡） |
| Advance_Base_Max/Min | 基础点火角 MAX/MIN |
| Air_Fuel_Target | Lambda 目标 |
| VVT_Int/Exh_Target | 进/排气 VVT 目标 |

**DENSO 特有策略**：
1. 双重 Pedal Map：Normal 和 Power 模式切换
2. Std vs Opt 双扭矩：正常用 Standard，深踩切换到 Optimal
3. Requested Load 双缓冲：#1/#2 交替防扭矩突变
4. TCU 融合：变速箱限制嵌入 ECU，非 CAN 独立协调

---

### 2.3 BMW DME 扭矩模型

介于 VAG 和 DENSO 之间。

```
踏板 → KF_MMD / W_FAHR（驾驶员需求）
          |
          v
  ┌─ 扭矩协调器 ──────────────────────┐
  │  - 内燃机限制 KF_IE                │
  │  - 变速箱限制 KF_GG_GRX            │
  │  - EGT 保护                        │
  │  - 爆震限制 KF_Klopfer             │
  │  - DSC 干预                        │
  └────────────────────────────────────┘
          | 最终扭矩
          v
  → Inverse Torque Model → 目标负荷
          |
          v
  → Air → Fuel → Spark → Torque
```

| BMW MAP | 功能 |
|---------|------|
| W_FAHR / KF_MMD | 驾驶员需求扭矩 |
| KF_IE | 内燃机扭矩限制 |
| KF_GG_GRX | 各档位变速箱限制 |
| W_MAX_BEK | WOT 限制 |
| KF_ZW | 基础点火 |
| KF_RL | 负荷 MAP |
| KF_LAM | Lambda 目标 |
| KF_WGR | WGDC MAP |
| KF_VTGMX | 增压目标 |

---

### 2.4 Delphi / GM 扭矩模型（E67/E78/E80）

扭矩单位用 SAE Net Torque，部分 MAP 以 lb-ft 为单位。

```
APP → Driver Demand Torque
          |
          v
  ┌─ Torque Management ────────────────┐
  │  Base Torque                       │
  │  Torque Limits (per gear/TESTATE)  │
  │  Abuse Torque (爆震限制)           │
  │  Torque Reduction (换挡/ESP)       │
  │  Cold Torque (低温限制)            │
  └────────────────────────────────────┘
          | 最终扭矩
          v
  → Air → Fuel → Spark
```

| Delphi/GM MAP | 功能 | 单位 |
|--------------|------|------|
| CMD_TORQUE_MAX | 指令最大扭矩 | Nm / lb-ft |
| TORQUE_MAX_GEAR_X | 各档位最大扭矩 | Nm / lb-ft |
| PEDAL_TORQUE_X | 踏板→扭矩 | Nm vs APP% |
| VIT_SPK_BASE | 基础点火角 | deg BTDC |
| FUEL_CMD_LAMBDA | Lambda 目标 | Lambda |
| TC_BOOST_DES | 目标增压 | kPa |

### 2.5 Mercedes-Benz 扭矩模型

介于 Bosch VAG 和简化之间，限制器层级更扁平。

```
Driver Wish → Torque Request
    |
    v
Engine Torque Limits（取最小值）
  ├── 驾驶员需求
  ├── 发动机最大扭矩
  ├── 变速箱档位限制
  ├── EGT 保护
  ├── CAT 保护
  ├── 转速限制
  └── 车速限制
    |
    v
Target Load → Air → Fuel → Spark
```

Mercedes MAP 命名使用直接功能描述而非缩写，如 MaxTorque、PowerLimit、BoostTarget。

---

### 2.6 柴油共轨扭矩/限制链（Bosch EDC17/MD1、Delphi DCM 通用架构）

上面 2.1–2.5 是**汽油**架构（核心是点火/Lambda/负荷）。柴油共轨完全不同：**没有节气门主控、没有点火 MAP，扭矩直接由喷油量（IQ, Injection Quantity）决定**，空气侧（增压/EGR）是配合喷油、而非主控进气量。柴油 Stage 的天花板不是爆震，而是**烟度限制**。

```
踏板 → 驾驶员需求扭矩 (Driver Wish；柴油常直接以 IQ 或扭矩表达)
    │
    ▼ 取最小值（限制链）
┌─ 扭矩/喷油量限制链 ───────────────────────────────┐
│  1. 驾驶员需求                                     │
│  2. 发动机最大扭矩包络 (转速×扭矩上限)             │
│  3. ★烟度限制 Smoke Limiter★ = IQ 上限 vs 实测空气 │
│     质量 —— 柴油特有的核心天花板：空气不够就冒黑烟  │
│  4. 排温/部件保护 (EGT、涡轮超速、DPF 温度)        │
│  5. 变速箱档位扭矩限制                             │
└────────────────────────────────────────────────────┘
    │ 最终目标 IQ
    ▼
┌─ 喷油量分配 ──────────────────────────────────────┐
│  主喷 Main / 预喷 Pilot 1·2 (NVH/噪声) / 后喷 Post │
│  (后喷主要服务 DPF 再生与排温,不产扭矩)           │
└────────────────────────────────────────────────────┘
    │
    ▼
┌─ 喷油实现 ────────────────────────────────────────┐
│  轨压设定/上限 Rail Pressure  |  SOI 喷射定时(°BTDC,│
│  可负)  |  脉宽 = f(IQ, 轨压, 喷油器特性)          │
└────────────────────────────────────────────────────┘
    │ 与空气侧闭环协调
    ▼
┌─ 空气侧(配合喷油) ────────────────────────────────┐
│  目标增压 Boost → VGT 叶片 / WGDC  |  EGR 率(NOx) │
│  |  进气节流阀(主要用于 EGR 调节与停机抖动抑制)    │
└────────────────────────────────────────────────────┘
```

**柴油 Stage 铁律（第一性原理）**：功率来自"更多燃油"，但**燃油不能超过可用空气能干净燃烧的量**（Banish 空气优先原则的柴油版）。所以顺序是 **先抬增压/空气 → 再抬 IQ → 同步放宽烟度限制 → 守住 EGT 与轨压/涡轮边界**。只加 IQ 不加空气 = 冒黑烟 + 排温飙升 + 涡轮/DPF 受损。

**柴油关键 MAP（按功能，exact DAMOS/命名经 `damos-a2l-parser` 或 `winols-ols-parser` 提取，不同 SW 版本标签不同，不得凭空写死标签）**：

| 功能 | 说明 | 单位/量级 |
|------|------|-----------|
| 驾驶员需求 IQ/扭矩 | 踏板→请求 | mg/stroke 或 Nm |
| 发动机最大扭矩包络 | 转速×上限 | Nm |
| 烟度限制（IQ vs 空气质量）| **Stage1 主天花板** | mg/stroke vs mg/cyl 空气 |
| 轨压设定/上限 | Rail Pressure | 数百–2500+ bar |
| SOI 喷射定时 | Start of Injection | 少数 °BTDC，可负（s16）|
| 目标增压/VGT | Boost Target / VGT | mbar/kPa 绝对 |
| EGR 率 | NOx 控制 | % 或 mg |
| 扭矩→IQ 转换 | 扭矩转喷油量 | — |
| 变速箱档位限扭 | TCU 联动 | Nm |

**Bosch vs Delphi 柴油策略差异**：
- **Bosch（EDC17/MD1）**：分层扭矩结构 + DAMOS 命名（KF/MLD/等前缀，具体经 A2L）。烟度限制、扭矩限制器、轨压、SOI 各自独立 MAP，改一个不够，须协调整链。
- **Delphi（DCM）**：扭矩表达更贴近物理喷油量，限制器层级相对扁平；命名惯例与 Bosch 不同（见 `dcm-platform`/`multi-vendor-ecu-engine`）。

**柴油限制器搜索顺序**：
```
1. 驾驶员需求 IQ/扭矩（是否释放）
2. 发动机最大扭矩包络
3. 烟度限制（IQ vs 空气）—— 最关键，抬 IQ 必同步
4. 轨压上限（喷更多需要压力支撑）
5. 目标增压/VGT（先给空气）
6. EGT/涡轮/DPF 保护（守住上限）
7. 变速箱档位限扭（TCU 联动）
```

> 本库只描述 EGR/DPF/SCR 相关 MAP 在扭矩/空气链中的**位置与作用**用于诊断与标定。见 `ecu-calibration-doctrine`。

---

## 3. 跨平台 MAP 功能对照表

### 3.1 核心 MAP 对照

| 功能 | Bosch VAG | Denso Toyota | BMW DME | Delphi GM |
|------|----------|-------------|---------|----------|
| 踏板→扭矩 | KF_DV_MMD | Pedal_Map #1/#2 | KF_MMD / W_FAHR | PEDAL_TORQUE_X |
| WOT 扭矩限制 | KF_URWMX | Opt_Trq_Map / MaxTrqLimit | W_MAX_BEK | CMD_TORQUE_MAX |
| 档位扭矩限制 | KF_GTRL | TCU Trq per Gear | KF_GG_GRX | TORQUE_MAX_GEAR_X |
| 扭矩限制器 | KFTLRS | Standard Torque Map | LDR_E | 扭矩管理综合 |
| 增压目标 | KF_VTGTMX | Boost Target | KF_VTGMX | TC_BOOST_DES |
| 负荷 MAP | KFLD | Requested Load #1/#2 | KF_RL | LOAD_EST |
| 点火基础角 | KFZW | Advance_Base_Max/Min | KF_ZW | VIT_SPK_BASE |
| Lambda 目标 | KFDL | Air_Fuel_Target | KF_LAM | FUEL_CMD_LAMBDA |
| WGDC | KF_WG | WGDC Base | KF_WGR | TC_WASTEGATE_DC |
| VVT 进气 | NWINW | VVT_Int_Target | VVT Einlass | CMP_INTAKE_POS |

### 3.2 保护 MAP 对照

| 功能 | Bosch VAG | Denso Toyota | BMW DME |
|------|----------|-------------|---------|
| 转速限制 | LDRXN | RPM Limit | DREHZAHL_BEGRENZ |
| EGT 保护 | KF_EGT | Exh_Temp_Limit | ABGAS_TEMP |
| CAT 保护 | KF_CAT | CAT_Temp_Model | KAT_TEMP |
| 爆震基础 | KF_BASKF | Knock_Threshold | KF_KLOPFER |
| 爆震退火 | KF_BKR | Knock_Retard_Max | Klopf Ruecknahme |
| NVH 抑制 | KF_NVHRB | NVH_Trq_Notch | NVH_Momente |

---

## 4. 限制器搜索顺序（按品牌）

### VAG (Bosch) 限制器搜索顺序

```
1. KF_DV_MMD — 驾驶员需求（先确认是否释放）
2. KF_UMXW — 扭矩上限标量
3. KFTLRS — 扭矩限制 MAP（最重要的限制器）
4. KF_URWMX — WOT 扭矩限制
5. LDRXN — 转速限制（含各档位不同限速）
6. KF_GTRL — 变速箱限制
7. KF_ETLRS — 外部扭矩限制（ESP/ACC）
8. KF_EGT — EGT 热保护
9. KF_CAT — CAT 温度保护
```

### Denso Toyota 限制器搜索顺序

```
1. Pedal_Map — 踏板请求范围是否释放
2. Std_Trq_Map — 标准扭矩是否足够
3. Opt_Trq_Map — WOT 最优扭矩（这是目标值）
4. Max_Trq_Limit — 最大扭矩上限（RPM×ECT）
5. TCU_Trq_Limit — 变速箱档位限制（常见限扭点）
6. Adv_Base_Max — 点火最大角度限制
7. Air_Fuel_Target — Lambda 目标
8. WGDC / Boost_Limit — 实际增压
```

### BMW DME 限制器搜索顺序

```
1. KF_MMD / W_FAHR — 驾驶员需求
2. KF_IE — 内燃机扭矩限制
3. KF_GG_GRX — 变速箱限制
4. LDR_E — 扭矩限制 MAP
5. W_MAX_BEK — WOT 全负荷限制
6. KF_VTGMX — 增压目标
7. KF_LAM — Lambda 目标
8. KF_ZW — 点火角限制
```

---

## 5. TCU 扭矩策略差异

| 车型群 | 变速箱限制方式 | 说明 |
|--------|-------------|------|
| VAG DSG | ECU 的 KF_GTRL + TCU 独立限制 | 双限制：ECU 预限 + TCU 通过 CAN 动态请求 |
| Toyota AT | ECU 内档位扭矩 MAP | 一体化，TCU 嵌入 ECU，无需 CAN 协商 |
| BMW ZF8HP | DME 的 KF_GG_GRX + EGS | 通过 PT-CAN 通信协调 |
| GM 6L80/8L90 | TCM 独立控制 | 通过 CAN 请求降扭 |

---

## 6. 平台切换注意事项

| 切换方向 | 最大差异 | 最易误判的点 |
|---------|---------|------------|
| VAG → Denso | VAG 用扭矩作核心，Denso 用负荷 | 改 Pedal Map 不改 Std/Opt Trq 和 Load 无效 |
| Denso → VAG | VAG 限制器层级复杂 | 改 KF_URWMX 不改 KFTLRS = 无效 |
| BMW → VAG | BMW 限制器更少 | 以为 VAG 也只改 2-3 张表 |
| Delphi → Bosch | Delphi 扭矩更接近物理值 | GM 可能用 lb-ft (1 lb-ft = 1.35582 Nm) |
| 柴油 → 汽油 | 柴油通过喷油量控扭矩 | 无点火 MAP，有 SOI 和轨压 |

---

## 7. 与其他技能的关系

```
oem-strategy-library（命名/架构参考层）
├── 上位方法论：扭矩结构"为什么这样" → torque-model-builder
│     （本库只给各厂"叫什么名、什么顺序"；结构与因果由 torque-model-builder 出）
├── 提供 ECU 扭矩架构 → stage1-calibration-matrix
├── MAP 命名对照 → winols-screenshot-analyzer
├── 策略差异 → ecu-map-analysis
├── 限制器层级 → ecu-calibration-doctrine
├── 测量通道 → hp-tuners-log-review
├── MAP 功能分类 → damos-a2l-parser
├── 柴油平台专属识别/访问/checksum → edc17-platform / md1-platform / dcm-platform
│     （本库 §2.6 只给柴油扭矩链的通用架构，不给平台访问现实）
└── 所有结论经过 ecu-calibration-doctrine
```

**本库的证据等级**：命名惯例是**线索，不是证据**。同一前缀在不同 SW 版本含义可能不同，
任何按命名认定的表都必须经**值域 + 轴 + 联动**验证（`ecu-map-analysis`），
exact 标签一律经 `damos-a2l-parser` / `winols-ols-parser` 提取，不得凭本库写死地址或标签。

## 8. 输出纪律

- 跨平台对照必须注明来源
- 每个策略描述后注明例外情况
- 限制器搜索顺序给出优先级和取最小值/乘法的判断
- 不提供具体修改数值（那是 stage1-calibration-matrix 的工作）
- 修改建议附带 TCU 联动提醒

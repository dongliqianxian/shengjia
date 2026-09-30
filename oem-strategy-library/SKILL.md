---
name: oem-strategy-library
description: >
  OEM 动力总成控制架构参考技能。用于比较 torque-based spark-ignition、直接执行器控制、柴油共轨、
  混合动力和不同供应商的软件策略；解释驾驶员需求、扭矩仲裁、正逆扭矩模型、空气/燃油/点火执行、
  保护与 TCU 握手的常见层级。点名 Bosch、DENSO、Continental、Delphi、Marelli、Hitachi 或某车型时，
  先给架构候选和证据等级，再要求匹配 HW/SW/Cal ID 的 A2L/DAMOS/OLS 或代码引用确认具体 MAP 名。
  Bosch ME7 的 KFPED/KFMIRL/LDRXN/KFMIOP、共享轴或扭矩监控问题读取 ME7 专项参考。
  本技能不把跨软件版本的标签表当事实，不解析二进制、不做截图识别、不输出修改数值。
---

# OEM Strategy Library

## 定位

本技能回答“这类控制器通常如何组织控制链”，不回答“这个地址一定是哪张表”。运行时遵守
`ecu-calibration-doctrine/references/evidence-and-consistency-contract.md`。

证据标签：

- `VERIFIED`：来自同一 HW/SW/Cal ID 的定义、代码引用或逐单元格验证。
- `FAMILY-CONVENTION`：同软件族常见命名，只能用于搜索。
- `INFERRED`：由形态、范围或相邻关系推断。
- `UNKNOWN`：缺少证据。

供应商品牌、车型和相似名称都不能把 `FAMILY-CONVENTION` 升级为 `VERIFIED`。

## 1. 先判控制架构

### 1.1 Torque-based 火花点火系统

常见通用链：

```text
驾驶员/巡航/稳定系统/TCU 请求
  -> 扭矩仲裁与限制
  -> 逆扭矩模型（目标扭矩 -> 目标充量/负荷）
  -> 节气门/增压/VVT 等空气执行
  -> 燃油与点火效率
  -> 正扭矩模型（实际充量/效率 -> 估计扭矩）
  -> 功能监控与 TCU 广播
```

不要假定所有限制都简单取最小值：请求、限制、加法补偿、效率、优先级和状态机可能混合存在。具体仲裁
必须由定义或代码确认。

### 1.2 直接执行器/非扭矩结构

老式 ECU、部分 powersports 和小型发动机可能以 TPS/RPM 直接索引燃油、点火、节气门或副节气门表。
电子节气门（RBW）本身不能证明存在完整正逆扭矩模型。用请求信号、扭矩广播、模型表和代码链确认。

### 1.3 柴油共轨

常见通用链：

```text
驾驶员请求（Nm 或 IQ）
  -> 扭矩/IQ/烟度/温度/传动限制
  -> 扭矩到喷油量转换
  -> 轨压、喷射时刻、主/预/后喷实现
  <-> 空气质量、增压/VGT、EGR 和热模型协调
```

现代柴油并非“没有扭矩模型、扭矩直接等于 IQ”。喷油量是主要快执行量之一，但扭矩估算、空气限制、
烟度、轨压、温度、涡轮和 TCU 仍形成闭环。也不要机械执行“先抬 boost 再加 IQ”：涡轮能量来自燃烧，
应在烟度、空燃、背压、涡轮转速和 EGT 约束下小步耦合迭代。

### 1.4 混合动力/多动力源

发动机扭矩只是系统扭矩的一部分。电机、SOC、逆变器、变速箱、制动能量回收和热管理可成为最终限制。
没有整车扭矩协调信号时，不把发动机侧限制器当作整车唯一瓶颈。

## 2. MAP 命名纪律

### 2.1 Bosch 标签

Bosch 标签只在具体软件族内有意义。以下仅是部分 ME7 文档/定义中常见的搜索线索：

| 标签候选 | 常见含义 | 纪律 |
|---|---|---|
| `KFMIRL` | 请求扭矩到相对负荷的转换 | 必须用轴、单位和同版本定义确认 |
| `KFMIOP` | 相对负荷到最优扭矩/效率侧模型 | 不是“喷油脉谱” |
| `LDRXN` | 常见为最大允许/请求负荷随转速 | 不是通用转速限制器 |
| `KFZW*` | 点火角相关表族 | 后缀、状态和轴决定具体角色 |
| `KFLDHBN` | 部分 ME7 的增压/压力保护限制 | 不可外推到 MED17/MG1/MD1 |

涉及 Bosch ME7 的 KFPED、KFMIRL、LDRXN、KFMIOP、KFZWOP/2、扭矩干预、`rlsol/mibas/mimax`、
ESP/ASR 减扭品质异常，或点火角振荡（`zwgru/zwbas/zwsol/zwout` 鉴别）
时，必须读取 [references/bosch-me7-torque-chain.md](references/bosch-me7-torque-chain.md)。该参考是
`FAMILY-CONVENTION` 路由，不把论坛经验或跨版本名称升级成当前文件的 `VERIFIED` 事实。

MED17、EDC17、MG1、MD1、OEM 自定义层和不同编译版本的标签可完全不同。不要创造看似 Bosch 风格的名称。

### 2.2 DENSO、Delphi、Continental 等

这些平台常使用项目内部描述名、符号名或工具自动标签，没有跨车型稳定的 `Pedal_Map`、`Std_Trq_Map`
之类标准名称。描述名只能作为功能候选，必须用地址、轴、单位、调用关系和同 Cal ID 定义确认。

### 2.3 名称冲突裁决

1. 同 Cal ID 的定义/代码引用优先。
2. 名称与轴/单位/值域冲突时，先判地址映射、record layout 或定义版本错误。
3. 同名多表可代表 Bank、模式、温度、档位、监控镜像或冗余；不得默认全部一起改。
4. 没有定义时输出功能候选，不制造 OEM 风格标签。

## 3. 平台分析流程

1. 识别 ECU/TCU、MCU、HW/SW/Cal ID、燃料和发动机控制类型。
2. 选择输入：优先 A2L/DAMOS/OLS/KP，其次 ORI/MOD diff，再次代码 xref，最后才用形态。
3. 把已证实对象映射到：请求、仲裁、模型、执行、反馈、监控、保护。
4. 对每个限制写明组合方式：min/max、乘法、加法、状态选择或未知。
5. 标注 ECU↔TCU 信号定义，区分 indicated/actual/requested/allowed torque。
6. 将具体表交 `ecu-map-analysis`，数值矩阵交 `stage1-calibration-matrix`，字节工作交 bin 引擎。

## 4. 输出格式

```text
平台识别：ECU / MCU / HW-SW-Cal ID / 证据来源
架构类型：torque-based / direct-actuator / diesel common-rail / hybrid / unknown

控制链：
输入 -> 请求 -> 仲裁 -> 模型 -> 执行 -> 反馈 -> 监控/保护

对象清单：
名称或地址 | 角色 | 轴/单位 | 组合方式 | 证据等级 | 下一项验证

边界：
- 已证实什么
- 仍是候选什么
- 哪些信息缺失时不得写入
```

## 5. 硬边界

- 不用品牌推断字节序；交 `multi-vendor-ecu-engine` 按 MCU/record layout 确认。
- 不输出跨平台“必改 MAP 名单”或固定修改百分比。
- 不把保护模型当普通限制器；默认保留，只有部件规格和实测支持时才评估标定。
- 不输出道路车辆排放控制删除、关码或检测规避流程。

## 6. 维护验证

修改或同步本技能后运行：

```bash
python scripts/validate_me7_semantics.py <oem-strategy-library目录>
```

它检查 LDRXN 未被误写成转速限制器、ME7 专项参考可发现，以及关键控制链和停止状态仍然存在。

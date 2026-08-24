---
name: md1-platform
description: >
  Bosch MD1 柴油 ECU 平台深度技能（现代共轨，Infineon AURIX TriCore，RSA secure-boot）。
  当用户点名 MD1、或给出 MD1CP/MD1CS 型号（如 MD1CP001、MD1CP004、MD1CP014、MD1CS001）、
  或问"这个 MD1 怎么读/能不能写/checksum/RSA 签名/secure boot/f32/AURIX/是什么子型号/
  MD1 和 EDC17 有什么区别/MD1 一阶能做吗"时，必须调用本技能。
  （注：MD1=柴油，MG1=汽油，同为 AURIX；汽油走 bosch-conti-bin-engine + oem-strategy-library。）
  本技能是【平台策略/访问/识别层】：给出 MD1 子型号识别、AURIX 硬件、f32 数据特点、
  访问难点、以及关键的 RSA secure-boot 现实（cal CRC 可算但引导签名不可伪造，只能工具链处理）。
  它不做字节读写（bosch-conti-bin-engine）、不做单表拆解（ecu-map-analysis）、不重算 checksum
  （checksum-crc-engine）、不出扭矩架构（oem-strategy-library §2.6 柴油共轨）。
  运行于 ecu-calibration-doctrine 之下。
  边界：只做诊断/标定支持。
---

# Bosch MD1 平台（现代柴油共轨 / AURIX / RSA secure-boot）

MD1 是 EDC17 的下一代柴油 ECU，AURIX 平台。**与 EDC17 最大的区别不在标定逻辑，而在"读得到、
写得进、签得回"这三关**——尤其是密码学签名。柴油扭矩链见 `oem-strategy-library` §2.6 柴油共轨扭矩链，
字节解码带宽见 `bosch_conti_platforms.md`，本技能不重复。

## 1. 子型号识别

| 变体 | 定位 |
|------|------|
| MD1CP0xx | 乘用/轻商柴油主力（如 CP001/CP004/CP014） |
| MD1CS0xx | 部分变体 |
| （汽油 MG1CS0xx / MG1CP0xx） | 不属本技能，汽油走 bosch-conti + oem-strategy-library |

- 识别来源：字符串区 `MD1CPxxx` 标记、Bosch 号 `0 281 xxx xxx`、`10SWxxxxxx/10HWxxxxxx`。
  库匹配交 `ecu-calib-finder`。文件名不是证据。

## 2. 硬件与数据（与 EDC17 的关键差异）

| 项 | MD1 | 对比 EDC17 |
|----|-----|-----------|
| MCU 核 | Infineon **AURIX TC27x/TC29x**（TriCore 1.6.x） | EDC17 是 TC176x/179x |
| 字节序 | **little-endian** | 同 |
| 数据类型 | **大量 f32（浮点）** + u8/u16 | EDC17 以 u8/u16 为主 |
| Flash | 更大 | — |

> f32 是 MD1 解码的高频坑：同一功能在 EDC17 是 u16×factor，在 MD1 可能是 f32 直存。用
> `bosch-conti-bin-engine` 的 `f32` 编码,不要套 EDC17 的整型 factor。反汇编内存映射交 `ghidra-ida-setup`
> （AURIX 常见基址 `0x8000_0000`+）。

## 3. 访问与 checksum 现实 —— MD1 的真正门槛

这是 MD1 区别于 EDC17 的**核心**，必须对用户讲清楚，不能含糊：

| 层 | 能否本地处理 | 现实 |
|----|-------------|------|
| **数据块 cal CRC/sum** | ✅ 可算 | 定位到范围/算法可用 `checksum-crc-engine` 重算 |
| **程序区多层 checksum** | ⚠ 易错 | 走 WinOLS 插件 / 刷写工具 auto-checksum |
| **Bootloader RSA / ECC 签名（secure boot）** | ❌ **绝不可伪造** | 密码学签名。改动被签名覆盖的区域后，**无正确工具链就无法签回、刷了不启动** |

- **读取**：MD1 OBD 读常被锁；全 dump 依赖特定工具的 bench/boot 方案（随工具与固件版本变，历史上有过
  基于 bootrom 的方案，但因件而异）。**读不出就谈不上改**——遇到读保护先解决读取。
- **写入**：cal 区改动若落在签名覆盖范围，必须由**支持 MD1 的刷写工具链**完成签名/校验；本地重算
  只能覆盖非密码学的 CRC/sum 层。
- 诚实纪律（与 `checksum-crc-engine` 一致）：**MD1 文件不得凭本地重算 checksum 就宣称可刷**——
  未经正确工具链签名的签名区改动会 brick。

## 4. 柴油标定入口

标定逻辑与 EDC17 同源（见 `oem-strategy-library` §2.6 柴油限制链：烟度限制→增压→IQ→轨压→
EGT/涡轮/DPF 保护）。差异在**表可能是 f32、地址布局不同、且交付受签名约束**。分工同 EDC17：
- 定义/地址：`damos-a2l-parser`（A2L）/`winols-ols-parser`（.ols）/`ghidra-tricore-disasm`（无定义）
- 单表拆解：`ecu-map-analysis`；整套方案：`stage1-calibration-matrix`
- 字节读写（**f32**）：`bosch-conti-bin-engine`；带宽自检：`bosch_conti_platforms.md`

## 5. 边界

只做 MD1 的诊断、标定、动力标定。合法诊断、真部件故障定位、赛用/台架研究照常支持。
故障码定位用 `dtc-locator`。

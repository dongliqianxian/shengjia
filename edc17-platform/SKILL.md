---
name: edc17-platform
description: >
  Bosch EDC17 柴油 ECU 平台深度技能（乘用/轻商柴油共轨，Infineon TriCore）。
  当用户点名 EDC17、或给出 EDC17C/CP/P 后缀型号（如 EDC17C46、EDC17CP14、EDC17CP44、
  EDC17C54、EDC17U01）、或问"这个 EDC17 怎么读/怎么写/checksum 怎么算/是什么子型号/
  内存基址多少/在哪找烟度限制轨压 SOI 增压/EDC17 一阶怎么做/EDC17 读保护"时，必须调用本技能。
  本技能是【平台策略/访问/识别层】：给出 EDC17 的子型号识别、MCU、内存与基址、读写访问方式
  （OBD/bench/BSL 与读保护）、checksum 现实（多层 CRC，非 RSA）、柴油 MAP 定位入口与 Stage 边界。
  它不做字节读写（交 bosch-conti-bin-engine）、不做单表拆解（ecu-map-analysis）、
  不重算 checksum（checksum-crc-engine）、不出扭矩架构细节（oem-strategy-library 的柴油共轨节）。
  运行于 ecu-calibration-doctrine 之下。
  边界：只做诊断/标定支持。
---

# Bosch EDC17 平台（柴油共轨 / Infineon TriCore）

EDC17 是最主流的乘用/轻商柴油 ECU，2006 年起大规模装车。本技能只讲**这个平台专属**的东西；
柴油扭矩链见 `oem-strategy-library` §2.6 柴油共轨扭矩链，字节解码带宽见
`bosch-conti-bin-engine/references/bosch_conti_platforms.md`，本技能不重复。

## 1. 子型号识别（先定到具体变体）

| 变体 | 定位 | 典型平台 |
|------|------|----------|
| EDC17C** | 乘用车（Car），最常见 | 大众 TDI、宝马 Dxx、通用/PSA 柴油 |
| EDC17CP** | 乘用车特定引脚/配置 | VAG EA189/EA288、PSA DW10 等 |
| EDC17U** | 通用（Universal） | 多平台商用/轻卡 |
| EDC17P** | 特定项目 | 少见 |

- 后缀数字（C46/C54/C64/CP04/CP14/CP20/CP44/CP54…）对应**具体车型项目**，决定内存布局与 MAP 位置。
- 识别来源：文件字符串区的 `EDC17Cxx` 标记、Bosch 号 `0 281 xxx xxx`、`10SWxxxxxx/10HWxxxxxx`
  软/硬件号；识别流程与库匹配交 `ecu-calib-finder`（`brand_signatures.md` 有签名）。
- **文件名/包名不是证据**——必须对文件内字符串区与内部结构核对。

## 2. 硬件与内存

| 项 | 值 | 备注 |
|----|----|----|
| MCU 核 | Infineon **TriCore**（TC1766/67/96/97，后期 TC1782/93） | **little-endian** |
| 字节序 | 小端 | 所有 EDC17 一致；解码错乱先怀疑地址/数据类型，不是字节序 |
| Flash | 内部 + 外部 SPI（部分） | 典型 2–4 MB |
| 数据类型 | u8 / u16 为主，s16 用于 SOI/修正（可负） | f32 极少（那是 MD1）|

> 镜像基址与内存映射（做反汇编时必须先定）交 `ghidra-ida-setup`：TriCore EDC17 常见代码区
> 基址 `0x8000_0000`（cached）/`0xA000_0000`（non-cached），bench 全 dump 偏移 0 = flash 基。
> 本技能不重复内存映射细节。

## 3. 读写访问方式（EDC17 的现实）

| 方式 | 能力 | 说明 |
|------|------|------|
| **OBD**（KESS/KTAG/CMD 等） | 读/写 cal，部分需 bootmode | 很多 EDC17 OBD 读受限或需解锁；后期部分带 OBD 侧保护 |
| **BSL / Bootmode** | 全读全写 | 拉 boot 引脚进 BSL，绕过 OBD 限制，最稳的全 dump 方式 |
| **BDM / JTAG（bench）** | 全读全写 | 台架直连；老 EDC17 常用 |
| 读保护 | 部分变体有 | 遇 NOREAD/读保护，WinOLS 侧见 `winols-noread-remover`（仅明文水印） |

关键点：EDC17 的写入门槛主要在**读取/解锁**，不在 checksum；拿到完整 ORI 后 cal 层可 bench 编辑。

## 4. Checksum 现实（EDC17 是多层 CRC，不是 RSA）

- EDC17 用**多层 cal CRC/sum 块 + 程序区 checksum**；**不是** MD1/MG1 的 RSA secure-boot（那不可伪造）。
- 因此 EDC17 改完**可以**重算 checksum 并刷回——但多层手算易漏块，一律走 **WinOLS checksum 插件 /
  ECM Titanium Auto-Fix**，或用 `checksum-crc-engine` 处理你能定位的单块。
- 交付纪律（doctrine 黄金顺序⑬）：未重算 checksum 的文件不得宣称可刷。

## 5. 柴油标定入口（EDC17 上你会改什么）

按 `oem-strategy-library` §2.6 的柴油限制链,EDC17 一阶主战场：

1. **烟度限制（IQ vs 空气质量）** —— Stage1 主天花板，抬 IQ 必同步抬空气
2. **目标增压 / VGT 叶片占空** —— 先给空气
3. **喷油量限制 / 扭矩限制器** —— 放行更多 IQ
4. **轨压上限** —— 支撑更大喷油
5. **EGT / 涡轮超速 / DPF 温度保护** —— 守边界，不是拿来拆
6. **驾驶员需求 / 扭矩→IQ** —— 踏板响应与整链一致

- 每张表的**地址/命名/换算**：有 A2L/DAMOS 用 `damos-a2l-parser`，有 WinOLS 工程用 `winols-ols-parser`，
  无定义靠 `ghidra-tricore-disasm` 找表。
- 每张表**是什么/能不能改/改多少**：`ecu-map-analysis`。
- **整套 Stage 修改矩阵**（必改/可选/LOG 验证/风险边界）：`stage1-calibration-matrix`。
- **字节读写与 diff**：`bosch-conti-bin-engine`（小端、A2L factor/offset）。
- **合理带宽自检**（轨压/IQ/SOI/增压量级）：`bosch_conti_platforms.md` 柴油段。

## 6. 边界

本技能服务于 EDC17 的**诊断、标定、动力标定**。合法诊断、真部件故障定位、赛用/台架研究照常支持。故障码在 bin 中定位用 `dtc-locator`。

---
name: dcm-platform
description: >
  Delphi（现 PHINIA）DCM 柴油 ECU 平台深度技能（乘用柴油共轨，含 DCM3.7 / 6.2 / 6.2AP / 7.1AP）。
  当用户点名 DCM、或给出 DCM3.x/6.x/7.x 型号（如 DCM3.7、DCM6.2、DCM6.2AP、DCM7.1AP）、
  或问"这个 DCM 怎么读/字节序/checksum/是什么代/Delphi 命名/DCM7.1AP 怎么搞/雷诺现代起亚 PSA
  柴油 ECU"时，必须调用本技能。
  本技能是【平台策略/识别层】：给出 DCM 谱系与代际、MCU/字节序核定（经 multi-vendor-ecu-engine）、
  Delphi 命名惯例、checksum 现实。
  重要：DCM7.1AP 公开资料稀疏，本技能对其具体参数按谱系【合理推断 + 明确标注置信度】，
  必须以用户实际文件/工具核对，不得当成已证实事实照搬。
  它不做字节读写（bosch-conti-bin-engine，经 multi-vendor 核定字节序后）、不做单表拆解
  （ecu-map-analysis）、不重算 checksum（checksum-crc-engine）、不出扭矩架构（oem-strategy-library §2.6）。
  运行于 ecu-calibration-doctrine 之下。
  边界：只做诊断/标定支持。
---

# Delphi / PHINIA DCM 平台（柴油共轨）

DCM 是 Delphi（现 PHINIA）的柴油共轨 ECU 家族，广见于**雷诺-日产（dCi）、现代/起亚、PSA、
部分 GM/双龙/马恒达**柴油。柴油扭矩链见 `oem-strategy-library` §2.6（Delphi 侧扭矩表达更贴近
物理喷油量、限制器层级相对扁平）。本技能只讲平台专属识别/访问,不重复扭矩逻辑与字节带宽。

## 置信度纪律（本技能强制）

DCM（尤其新代 7.1AP）公开逆向资料远少于 Bosch。下表每项标注：
**✅ 可firmly判定 / 🔷 按谱系合理推断(须核对) / ❓ 未知(须实测)**。
**禁止把 🔷/❓ 当 ✅ 输出**——这与 `checksum-crc-engine` 的状态分级、doctrine 的诚实纪律一致。

## 1. 谱系与代际

| 代 | 定位 | 置信度 |
|----|------|--------|
| DCM3.2 / 3.4 / 3.5 / 3.7 | 老一代共轨（2000s–2010s） | ✅ 家族归属 |
| DCM6.1 / 6.2 / 6.2AP | 中生代，装车量大 | ✅ 家族归属 |
| DCM7.1 / 7.1AP | 新代 | 🔷 归属 Delphi 新代柴油，细节资料稀疏 |
| 后缀 **AP** | 变体/项目标识 | 🔷 视为子变体，不改变核心平台判断 |

- 识别来源：字符串区 `DCMx.x` 标记、Delphi 部件号、车型侧软件号。签名参考 `brand_signatures.md`
  的 Delphi 段；库匹配交 `ecu-calib-finder`。**文件名不是证据**。

## 2. MCU 与字节序（先过这关，否则解码全是垃圾）

| 项 | 判定 | 置信度 |
|----|------|--------|
| 字节序 | **little-endian**（若为 Infineon TriCore/AURIX 核） | ✅ 由核决定（`multi-vendor-ecu-engine` 原则#1：字节序跟核不跟车标） |
| DCM6.2 MCU | Infineon TriCore 级 | 🔷 合理推断，须核对 |
| DCM7.1AP MCU | 疑似 AURIX 级 TriCore | 🔷 推断，须实测确认 |

> 流程铁律：**先经 `multi-vendor-ecu-engine` 按 MCU 核定字节序**再解码。TriCore/AURIX→小端；
> 若某 DCM 变体意外是别的核，以实测轴单调 + 值落物理带为准（`multi-vendor-ecu-engine` 的判据）。
> 反汇编内存映射交 `ghidra-ida-setup`。

## 3. Checksum 现实

| 层 | 处理 | 置信度 |
|----|------|--------|
| 数据块 cal CRC/sum | 定位后 `checksum-crc-engine` 可算 | 🔷 结构因代而异 |
| 多层/程序校验 | 走 WinOLS 插件 / 工具 auto-checksum | 🔷 |
| 新代是否 secure-boot 签名 | ❓ 未知，须实测 | ❓ |

- **诚实纪律**：不确定 DCM 某代是否含密码学签名前，**不得宣称本地重算即可刷**。按
  `checksum-crc-engine` 流程：有 ORI+工具修好的 MOD 就 diff 出校验位置反推；反推不出＝专有/签名，转工具。
- 每确认一个 DCM 型号的校验结构，回填 `checksum-crc-engine/references/known_checksum_layouts.md`
  （升级为 ✅实战确认）。

## 4. Delphi 命名与标定入口

- Delphi 命名惯例**与 Bosch DAMOS 不同**（非 KF/MLD 前缀体系）；跨平台功能对照见
  `oem-strategy-library` §3。exact 标签/地址一律经 `damos-a2l-parser`（A2L）/`winols-ols-parser`（.ols）/
  `ghidra-tricore-disasm`（无定义）提取，不得凭空写死 Delphi 标签。
- 柴油 Stage 主战场同 §2.6：烟度限制→增压/VGT→IQ/扭矩限制→轨压→EGT/DPF 保护。
- 分工：单表 `ecu-map-analysis`；整套 `stage1-calibration-matrix`；字节读写 `bosch-conti-bin-engine`
  （小端，A2L factor/offset，经 multi-vendor 核定后）。

## 5. 边界

只做 DCM 的诊断、标定、动力标定。合法诊断、真部件故障定位、赛用/台架研究照常支持。
故障码定位用 `dtc-locator`。

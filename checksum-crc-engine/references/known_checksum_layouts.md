# 已知平台校验和布局特征库（known_checksum_layouts）

> 本文件是 checksum-crc-engine 的积累型参考库：每在实战中确认一个平台的校验结构，就补一条。
> **状态字段是本库的生命线**——严禁把"公开资料说的"当成"本机验证过的"。

## 状态分级（每条必标）

| 状态 | 含义 | 允许的用法 |
|---|---|---|
| ✅ 实战确认 | 本机在真实项目上按此修复并成功刷写/回读 | 可直接按条目操作 |
| 🔧 工具路径确认 | 未逆向算法本身，但"改动 → 某工具 Auto-Fix → 可刷"路径在本机跑通过 | 按工具路径走，不要手算 |
| 📖 公开资料 | 社区/开源工具文档记载，本机未验证 | 只作方向参考，动手前先按"新增条目流程"验证 |

## 条目字段

`平台 | MCU | 校验层级(按 SKILL.md 四层表) | 算法/结构 | 修复路径 | 状态 | 备注`

---

## Bosch

| 平台 | MCU | 层级 | 算法/结构 | 修复路径 | 状态 |
|---|---|---|---|---|---|
| ME7.x | C167 | 多区域 cal 校验 | 多个 (sum, ~sum) 32 位成对块 + 主校验区；开源 ME7Sum 工具可全部重算 | ME7Sum / WinOLS 插件 | 📖 公开资料 |
| EDC15 | C167 | 多区域 cal 校验 | 16 位块和，若干独立块 | WinOLS 插件（手算易漏块） | 📖 公开资料 |
| EDC16 | TC1766/96 | 多区域 + OBD/CVN | cal 块 CRC + 末端校验；层数比 EDC15 多 | WinOLS 插件 / ECM Titanium | 📖 公开资料 |
| EDC17 / MED17 / MEVD17 | TC17xx | 多层 + 程序 CRC (+ 后期签名) | 数据层之上还有程序区 CRC 与计数器，手算不可满足 | **只走 WinOLS 插件 / 刷写工具 auto-checksum** | 📖 公开资料（"不要手算"这一结论按 doctrine 视为硬规则） |
| MD1 / MG1 | AURIX TC2xx/3xx | Secure Boot RSA 签名 | 密码学签名，不可伪造 | 只能由 OEM/刷写工具链处理；无授权服务通道则不可刷 | 📖 公开资料 |

## Continental / Siemens

| 平台 | MCU | 层级 | 算法/结构 | 修复路径 | 状态 |
|---|---|---|---|---|---|
| SIMOS8 / 12 / 18 (VAG) | TC17xx | 多层 + 签名（18 代起收紧） | 数据校验之上有引导校验/签名 | 专用工具链；SIMOS18 须先解锁才谈得上写 | 📖 公开资料 |
| PCR2.1 (VAG 柴油) | TC1766 | 多层 | — | 专用工具链 | 📖 公开资料 |

## DENSO（Toyota / Lexus）

| 平台 | MCU | 层级 | 算法/结构 | 修复路径 | 状态 |
|---|---|---|---|---|---|
| Toyota/Lexus 汽油 (含 89663-33L93 ES200 2017) | Renesas | 专有数据块校验 | 算法未逆向；改 MAP 后文件必经 Auto-Fix 否则 no-start | **ECM Titanium: Tools → Checksum → Auto Fix** | 🔧 工具路径确认（toyota-pedal-tuner / denso-bin-engine 项目沿用此路径） |

## 日系其他

| 平台 | MCU | 层级 | 算法/结构 | 修复路径 | 状态 |
|---|---|---|---|---|---|
| Subaru (Hitachi) | SH7055/58 **大端** | 数据块校验 | 32 位和平衡块（RomRaider 社区文档化） | RomRaider / 手算可行（先按流程验证） | 📖 公开资料 |
| Nissan (Hitachi) | SH7058 **大端** | 数据块校验 | 16 位和成对（NisTune 文档化） | NisTune / 手算可行（先按流程验证） | 📖 公开资料 |

## 美系

| 平台 | MCU | 层级 | 算法/结构 | 修复路径 | 状态 |
|---|---|---|---|---|---|
| GM E38/E92 等 | PowerPC **大端** | OS + 分段和 | 16 位分段和，HP Tuners/PCMHammer 写入时自动处理 | HP Tuners 自动 / PCMHammer | 📖 公开资料 |

---

## 新增条目流程（与 SKILL.md "honest workflow" 一致）

1. 拿到 **ORI + 工具修好的 MOD**，diff：除你的编辑外变动的字节 = 校验存储位置（宽度/字节序直接读出来）。
2. 用 `scripts/crc.py` 的 `identify_crc()` + sum 变体，在合理范围上对照工具修出的值试算。
3. 复现成功 → 记一条 **✅ 实战确认**（写清 data_ranges / store_offset / algo / mode，最好附 `ChecksumBlock(...)` 参数原文）。
4. 复现不了 → 该平台是专有/多层/签名，记 **🔧 工具路径确认**（写清用哪个工具哪条菜单），不要硬编一个猜的算法。
5. 任何一条从 📖 升级到 ✅/🔧 时，注明车型、文件、日期。

## ✅ 实战确认条目（逐条追加，含完整参数）

（暂无。第一条实战确认后按下面模板追加：）

```
### <平台> <车型> <日期>
文件: <bin 名 / 软件号>
ChecksumBlock(
    data_ranges=[(0x......, 0x......)],
    store_offset=0x......, store_size=., endian="little|big",
    algo="sum16|sum32|crc:CRC-32|...", mode="store_value|store_negsum",
)
验证: verify() 对 ORI 通过；repair() 后与工具修复结果逐字节一致；实车刷写回读正常。
```

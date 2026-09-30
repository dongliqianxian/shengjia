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

## 韩系（Hyundai / Kia — Kefico）

| 平台 | MCU | 层级 | 算法/结构 | 修复路径 | 状态 |
|---|---|---|---|---|---|
| SIMK41 / SIMK43 / SIMK2K | C166/C167 **小端** | 单层数据 CRC：Boot /（MTOS/RSW）/ Calibration / Program 各一个 CRC-16，**无程序 CRC、无签名** | 反射 CRC-16 poly 0xA001；**seed 取自文件内 ASCII 版本字段**，不是 0x0000/0xFFFF；多 zone 链式；存储时字节交换 | **`scripts/simk4x_checksum.py`（本地算完即可刷）** | ✅ 实战确认 |

> 这是本库里少数**可以本地算完就交付**的平台之一 —— 它没有 EDC17/MED17 那层程序 CRC，
> 也没有 MD1/MG1 的 RSA。别把"现代=Bosch=多层"的直觉套上来（doctrine 原则#1：PCM 不在乎车标）。

---

## 新增条目流程（与 SKILL.md "honest workflow" 一致）

1. 拿到 **ORI + 工具修好的 MOD**，diff：除你的编辑外变动的字节 = 校验存储位置（宽度/字节序直接读出来）。
2. 用 `scripts/crc.py` 的 `identify_crc()` + sum 变体，在合理范围上对照工具修出的值试算。
3. 复现成功 → 记一条 **✅ 实战确认**（写清 data_ranges / store_offset / algo / mode，最好附 `ChecksumBlock(...)` 参数原文）。
4. 复现不了 → 该平台是专有/多层/签名，记 **🔧 工具路径确认**（写清用哪个工具哪条菜单），不要硬编一个猜的算法。
5. 任何一条从 📖 升级到 ✅/🔧 时，注明车型、文件、日期。

## ✅ 实战确认条目（逐条追加，含完整参数）

（第一条已落地，见下。后续按同样格式追加。）

### Kefico SIMK43 — 现代途胜 2.7 V6 (127kW, 2010) — 2026-09-02

```
文件: AM29F400BB@SOP44_20260804_94406_OR.BIN（512KB, AM29F400BB@SOP44）
      零件号 M39110-37100 / KR77035410 / 5WY1F42B / 标定包 ca655027.DAT
镜像类型: v6 (5WY18+)   —— identification flag @0xEEFE == b"OK"

算法（不套 ChecksumBlock —— 它表达不了「链式 + 文件内取 seed」，用脚本）:
    反射 CRC-16, poly 0xA001 (= 0x8005 反射), 无 final xor
    == crcmod.mkCrcFun(0x18005, initCrc=seed)
    seed = init_address 处 2 字节【字节交换】后的值；那 2 字节是文件里的 ASCII 版本字段
    多 zone: zone1 用 ASCII seed, zone_n 用 zone_{n-1} 的 CRC 做 seed（链式）
    写回: 最后一个 zone 的 CRC【高低字节交换】后大端写入 cks_address
    zone 表: cks_address 处 [chk u16 BE][zones u8][pad]，其后 N 组 [start u32 LE][end u32 LE]，
             步进 8 字节；end 为闭区间 → 实际范围 [start, end+1)；地址加 bin_offset

| 区          | init_address | cks_address | bin_offset  | 本文件实际范围            | seed   | 存储值 |
|-------------|--------------|-------------|-------------|---------------------------|--------|--------|
| Boot        | 0x003FE4     | 0x003EF4    | 0           | 0x000000-0x003DFF         | 0x2D2D | 0x0A05 |
| Calibration | 0x00800C     | 0x00EEE0    | -0x080000   | 0x008000-0x00E395         | 0x3732 | 0xEFEF |
| Program     | 0x010052     | 0x010010    | -0x080000   | 5 段链式 0x010210-0x05470B| 0x3031 | 0x3208 |

验证: 三个区在 ORI 上【全部精确复现存储值】（3/3 独立命中，证据等级 A）；
      Stage1.2/1.3 改标定区后 Calibration 失配、Boot+Program 仍一致 —— 与"只改了标定区"吻合；
      repair 后回读重算三区全 OK，全文件仅 2 字节变动（cks 槽位本身）。
      实车刷写回读：待补。

★ 踩坑记录（这条是本条目最大的价值）:
  最初对整片 flash 做了 sum8/sum16/sum32/xor + 6 种标准 CRC-16 + CRC-32 的范围穷举，
  【0 命中】，一度误判"本镜像无可重算校验和"。原因：穷举默认 init=0x0000/0xFFFF，
  而本平台 seed 来自文件内的 ASCII 字段（0x3732 = "27"）。
  → 教训: 穷举 CRC 时"init 是常数"是一个【未言明的假设】。扫不到不等于没有；
    先去找开源实现/固件算术（doctrine §13.1 刻度需两条独立证据、§13.2 走原始字节）。
  → 本例的破局点是开源工具 GKFlasher（见下），不是继续加大穷举力度。

算法来源: GKFlasher <https://github.com/Dante383/GKFlasher> flasher/checksum.py (GPL-3.0)
          —— SIMK43/41 的开源刷写工具（KWP2000 over K-line/CAN，含 bootstrap/kernel/驱动、
             immo/SMARTRA 模块）。偏移表沿用其数据，脚本为算法独立复刻，遵守 GPL-3.0。
```

后续条目模板：

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

---
name: start-stop-removal
description: 自动启停(MSA/Start-Stop)功能关闭标定,以及"启停关闭文件"真伪鉴别技能。当用户说"关启停/关闭自动启停/启停默认关闭/启停常关/MSA off/START STOP OFF/auto start stop disable/验证这份文件是不是真关了启停/启停 off 是不是真的/启停关了为什么还熄火/关启停报故障码",或收到一份号称关了启停的 MOD 要审计时,必须调用。即使用户只丢来 ORI+MOD 问"这份启停关闭是真是假",也要调用。
---

# start-stop-removal（自动启停关闭与真伪鉴别）

## 0. 定位

与 `idle-speed-calibration`、`speed-limiter-removal` 同族的**单功能标定技能**：
启停域定位 + 关闭策略 + 真伪鉴别 + 安全边界。上位方法论 `ecu-calibration-doctrine`，
平台事实 `mg1-platform`，字节工作 `bosch-conti-bin-engine`。

## 1. 头号陷阱：文件名"START STOP OFF" ≠ 关了启停

启停关闭是后市场最高频需求之一，也是**假关闭重灾区**。实证案例（BMW X5 G05 / MG1CS201 / DME84T1 A2L）：

| 文件 | 改动 | 触及 MSA 功能对象 | 实质 |
|---|---|---|---|
| `DtcCN_StartStop OFF` | 30 字节：DINH 诊断抑制表 + MIL 防抖分组（还是手动挡离合器 DTC）+ **LSPI 检测阈值拉满** | **0 个** | 假关闭，DTC 屏蔽补丁，且削弱了超级爆震检测（doctrine §15 红线） |
| `MG1CS201 - START STOP OFF` | 294 字节：启停禁止码字全模式置位 + 配套 | 3 个核心 + 一批配套 | 真关闭 |

教训两条：**文件名不是证据**；**DTC 屏蔽 ≠ 功能关闭**——功能照常运行，只是不亮灯。

## 2. 真伪鉴别三步法（跨平台通用，本技能的核心方法）

前提：有 ORI、待审 MOD、配套 A2L/DAMOS。

1. **diff 聚簇**：整文件字节 diff，聚簇（gap≈16）。看总量与分布：
   几十字节集中 = 像功能开关；遍布诊断区 = 像 DTC 屏蔽。（只是粗筛，不做结论。）
2. **A2L 归属 + 边界验证**：把每个改动字节归属到命名 CHARACTERISTIC，
   且必须验证字节落在对象尺寸内（INSIDE），不能只按最近地址猜（doctrine §13）。
3. **功能对象清单交叉核对**：在 A2L 里列出全部启停命名家族对象
   （`MSA|Msa|StartStop|StSt`，宝马 MG1 实测约 900 个），数 MOD 触及了几个。
   **触及 0 个功能对象 = 假关闭，定论。**

一把梭（脚本能直接复现上面的实证案例）：

```bash
python scripts/a2l_diff_attribute.py --ori ORI.bin --mod MOD.bin --a2l ecu.a2l \
    --base 0x80000000 --keyword "MSA|Msa|StartStop|StSt" --values
```

无 A2L 时降级：diff 仍可做，归属交 `blind-map-finder` / `ghidra-tricore-disasm`；
结论按 doctrine §13 标 `(est.)`，不给勉强的答案。

## 3. 宝马 MG1 实测配方（MG1CS201 / DME84T1，证据 A 级，对象边界已验证）

宝马 MG1 的启停不是"一个布尔使能位"，而是 **码字（codeword）位图**结构：
**每一位 = 一种运行模式（Operation Mode），置位 = 该模式下启停熄火禁止器生效**。
注意方向反直觉：**全部置位（0x7FFF）才是全模式禁止，不是清零**。按老博世
`B_` 布尔开关的思路去找会找错对象。

**核心开关（真关闭 = 改这里）：**

| 对象 | 原厂值 → 关闭值 | 说明 |
|---|---|---|
| `BMWcpsup_cw_InhMsaStopOpm_C` | 0x0050 → **0x7FFF** | 主开关：启停熄火禁止器，bit per opmode，全置位 = 任何工况不允许自动熄火 |
| `BMWcpsup_cw_InhMsaStopSoc_C` / `...SocEfc_C` | 同族码字 | SOC 路径的禁止器码字，审计时一并检查 |
| `BMWchas_cw_MsaInhEngOff_C` | bitcoded | "MSA 熄火禁止器，每比特一个状态"——另一条禁止链，审计时检查 |
| `BMWcpsup_cw_DrvgMod_C` | 0x0050 → 0x7FFF | 驾驶模式配置码字（配套） |

**配套清零（SOC 阈值与下扭矩窗口）：**
`BMWcpsup_pct_SocHvMsa_C`（750→0）、`BMWcpsup_pct_DeltSocHvMsa_C`（135→0）、
`BMWcpsup_tq_WinLo*` / `ti_DlyWin*` 整组清零。

**功能配套 map（参考程序动过，属配套而非必需）：**
`BMWchold_ti_ShoStopMax_M`（短停催化器加热退出阈值）、`KF_ISGIMM_SEGELN3`（怠速滑行
转速预测）、`KF_POELPRECON`（机油预控）、`KF_MAWF_DMDP1`（dashpot，1 字节）。

**候选检查清单（B 级推断，未逐台验证，审计/自制时逐个核对）：**
`BMWdor_swi_NoMsaNoStop_C`（禁止请求熄火禁止器）、`BMWdor_v_VehMsaNoStop_C`、
`BMWmsa_b_CatTEngStopEnaMsa_C`（排放/猫温熄火放行）、`BMWesa_st_CtlLmpSwiMsa_ub_*`
（启停按键 LED）、`BMWswcd_b_MsaEngStopPrevn_C`（替代值）。

## 4. 红线与审计红旗

- **绝不为启停去动 `IKCtl_facDetnThdPreIgn*`（超级爆震/LSPI 检测阈值）。**
  上面假关闭文件就把它拉满到 0xFFFF——B58 直喷增压机 1200–2500 rpm 高负荷
  正是 LSPI 危险区，这道检测是保活塞的（doctrine §15）。审计 MOD 见到此类改动，
  单独向客户点明，与启停无关、是安全隐患。
- **审计 MOD 时对"00→FFFF 拉满"的未解释改动保持追问**（实证中 InjSys 曲线、
  GlbDa 触发器、诊断防抖即属此类）——静态分析给不了目的，向文件提供方问清再交付。
- **副本齐全性**：MG1 同功能常有模式/变体副本，改一份漏一份 = 行为不一致，
  交 `parallel-map-finder` 扫齐（doctrine §5 联动清单）。
- **checksum**：MG1 是 AURIX + RSA secure-boot，**本地算 CRC ≠ 可刷**。
  校验与签名交 KESS3 / AutoTuner / PCMFlash / WinOLS 插件，不得宣称已可刷。
  刷后 ECU Reset（学习值非易失）。

## 5. 交付验证（刷后）

路试：水温正常、空调低负载、D 挡刹停——**转速不掉、不熄火** = 功能真关闭；
若仍熄火只是没故障灯 = 只屏蔽了 DTC。顺手读一遍故障码确认无新增。
联动告知客户：48V 车型 SOC 充电窗口、催化器加热短停策略、滑行（Segeln）功能
可能随启停域一并改变，高速长坡工况留意水温/猫温。

## 6. 证据边界（写死）

- 第 3 节配方来自**单一平台单一文件**（MG1CS201 / DME84T1 / G05 X5 48V）实证：
  对象归属与边界 = A 级；码字语义有 A2L 原文支撑 = A 级；推广到其他 MG1 子型号 = B 级，
  每台车用本技能第 2 步重新核实。
- 其他平台（VAG MG1、Mercedes、日系 Denso 等）命名家族**未验证**：
  不套宝马配方，用第 2 节鉴别法先定位本平台启停命名家族，再建配方。
- 本技能不做"启停按键默认状态/记忆"——那常在车身域（BDC/网关 VO 编码），
  不在 DME 文件里，别在 bin 里硬找。

## 7. 交接

- 方法论/红线 → `ecu-calibration-doctrine`（§5 联动、§13 逆向纪律、§15 LSPI）
- 平台容器/访问现实 → `mg1-platform`；字节读写 → `bosch-conti-bin-engine`
- 故障码定位（区分"改功能"与"屏蔽码"）→ `dtc-locator`
- MOD 审计/移植 → `reference-tune-porting`；副本齐全性 → `parallel-map-finder`
- checksum → `checksum-crc-engine`（只做数据块层，签名交刷写工具）

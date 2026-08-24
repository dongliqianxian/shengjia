---
name: tcu-platform-library
description: >
  变速箱 TCU【平台控制特性知识库】——按变速箱类型(液力AT/湿式DCT/干式DCT/CVT/AMT)与具体平台
  (ZF 8HP/6HP、Aisin、GM 6L-10L、大众 DSG DQ/DL、奔驰 7G/9G、Jatco CVT)给出【控制架构差异、
  自适应机制、访问/checksum 现实、常见顿挫/滑摩根因、TCU-ECU 扭矩握手】。
  当用户点名某变速箱平台或问"这个变速箱怎么控制/湿式 DCT 和液力 AT 控制逻辑差别/DQ250 离合器接合点
  自适应/ZF 8HP 换挡压力自学习/为什么清了自适应要重新学/这个 TCU 怎么读怎么写/checksum/什么控制器/
  8HP 是 Bosch 还是 ZF 自己的/DSG 顿挫爬行异响根因/CVT 钢带打滑/换挡冲击到底是油压还是扭矩握手"时,
  必须调用本技能。
  本技能是【平台参考层】,不讲"怎么调五大系统"(那是 tcu-shift-calibration 的方法论)——它讲
  "你面对的这个平台在控制上到底是什么现实"。
  它不做字节读写(bosch-conti-bin-engine,经 multi-vendor-ecu-engine 核定字节序)、不重算 checksum
  (checksum-crc-engine)、不出发动机侧扭矩链(oem-strategy-library)、不做具体修改矩阵
  (tcu-shift-calibration)。**7.1AP 式资料稀疏项强制置信度分级,不把推断当事实。**
  运行于 ecu-calibration-doctrine 之下。
---

# TCU 平台控制特性知识库

## 0. 定位与分工

```
tcu-shift-calibration     方法论/执行:五大系统(换挡点/TCC/油压/档位限扭/扭矩握手)"怎么调"
tcu-platform-library ←本  参考层:各平台"控制架构现实"——为什么这个平台这样控、顿挫根因、访问/checksum
oem-strategy-library      发动机侧扭矩链(TCU 用到的 ECU 扭矩信号在那)
bosch-conti-bin-engine    字节读写(经 multi-vendor-ecu-engine 核定字节序)
checksum-crc-engine       写后校验和
```

本技能回答的是"**这个平台在控制上是什么现实**",不是"改哪张表"。数值/修改矩阵一律回 `tcu-shift-calibration`。

**置信度纪律(同 dcm-platform):** 平台控制架构是成熟公开知识,直接给;但具体子型号的 MCU/访问/
checksum 细节因年款/供应商批次而异——标 ✅可判 / 🔷推断须以实际文件核对 / ❓未知,**不把推断当事实**。

---

## 1. 先分类型:五种变速箱的控制逻辑根本不同

这是本技能的第一性原理——**顿挫/滑摩的根因分析,必须先落到变速箱类型**,因为控制机构完全不同:

| 类型 | 传扭机构 | 换挡如何完成 | 特有控制量 | 顿挫/滑摩根因方向 |
|---|---|---|---|---|
| **液力 AT** | 液力变矩器 + 行星齿轮 + 离合器组 | 油压控制多组离合器/制动器交替接合(overlap) | TCC 锁止、离合器 overlap 时序、主/换挡油压 | 换挡 overlap 时序错/油压不足→滑摩;油压过大/时间短→顿挫;TCC 抖振 |
| **湿式 DCT** | 两组湿式多片离合器(K1 奇数档/K2 偶数档) | 两离合器**交接**(一个松一个合),预选下一档 | 离合器**接合点(touch point)自适应**、充油(fill)时序、交接重叠 | 接合点漂移→换挡冲击/迟滞;充油不准→闯挡;交接重叠错→顿挫 |
| **干式 DCT** | 两组干式离合器(无油冷) | 同湿式但无油压伺服,靠电机推杆 | 接合点自适应、离合器温度模型、**爬行(creep)控制** | 过热保护介入→无力;接合点漂移→低速抖/爬行顿挫;半联动磨损 |
| **CVT** | 钢带/链 + 两锥轮(可变半径) | 无"挡",连续变速比;夹紧力液压控制 | 钢带**夹紧力**、速比控制、TCC | 夹紧力不足→**钢带打滑(灾难,不可逆)**;速比响应慢→迟滞 |
| **AMT** | 单离合器 + 自动执行机构 | 电机换挡+离合,本质是自动化手动 | 离合器接合曲线、换挡断扭时序 | 断扭时间长→换挡顿挫感强(本质局限) |

**关键推论:** 同一个"换挡顿挫"症状,液力 AT 查 overlap/油压,湿式 DCT 查接合点自适应+充油,CVT 根本
没有换挡(查夹紧力/速比)。**先分类型再谈根因,是本技能存在的核心价值。**

---

## 2. 自适应机制(与 closed-loop-control 同源:改了硬件/标定要清值)

DCT/AT 都有大量**自学习**把机械个体差异(离合器磨损、摩擦系数漂移)补进控制:

| 平台类型 | 自适应内容 | 存哪 | 何时必须清 |
|---|---|---|---|
| 湿式/干式 DCT | 离合器**接合点(touch/kiss point)**、充油量、离合器扭矩传递系数 | data flash(掉电不丢) | 换离合器/离合器模块、改换挡压力标定后 |
| 液力 AT | 换挡压力自适应(shift adaptation,按换挡时间误差学习)、离合器容积 | data flash | 换阀体/离合器片、改油压标定后 |
| CVT | 夹紧力自适应、速比学习 | data flash | 换钢带/油、改夹紧力后 |

**纪律(呼应 `closed-loop-control` §5 清值纪律):** 改了油压/接合点标定或换了机械件,**残留自适应是针对
旧状态的,会和新标定打架** → 顿挫/闯挡。必须走诊断仪清自适应(basic settings / adaptation reset),
**data flash 断电≠清除**。清后需按厂家流程走一段自适应学习(如 DSG 的静态/动态学习循环)。

---

## 3. 具体平台现实(控制器 / 访问 / checksum)

> 字节序一律经 `multi-vendor-ecu-engine` 按 MCU 核定(TriCore/无关车标=小端);checksum 一律 `checksum-crc-engine`。

### 3.1 ZF 8HP(8HP45/50/51/55/70/75/76/90/95) — 液力 8AT,后驱主力
- 控制器:多为 **Bosch 供 ZF** 的 TCU(TriCore),软件 ZF 自研。BMW(GA8HP)、奥迪、JLR、克莱斯勒(850RE/8HP)、众多后驱。✅
- 控制特性:换挡快、TCC 早锁(几乎全域锁止提效率)、多离合器 overlap 精细。🔷 高代次(8HP76/95)扭矩余量更大。
- 访问/checksum:🔷 多为 OBD/bench 可读写 + 多层校验,走 WinOLS 插件/工具 auto-checksum;具体子型号年款须核对。
- 常见坑:扭矩识别上限没同步(ECU 放扭 TCU 按原厂算)→ 换挡降扭不足顿挫(→ `tcu-shift-calibration` 系统5)。

### 3.2 ZF 6HP(6HP19/21/26/28/32) — 上一代液力 6AT
- 控制器:Bosch/ZF TCU。BMW(GA6HP)、奥迪、JLR 老款。✅
- 常见坑:阀体磨损→油压漂移→换挡滞后;Mechatronic 单元(集成阀体+TCU)是常见故障点。🔷

### 3.3 大众 DSG/S-tronic
| 型号 | 类型 | 控制器 | 扭矩级 | 现实 |
|---|---|---|---|---|
| DQ250 | **湿式** 6DCT | Temic/Conti Mechatronic | ~350Nm(极限≈380 熔断点) | ✅ 最成熟,湿式接合点+充油自适应关键 |
| DQ200 | **干式** 7DCT | Temic Mechatronic | ~250Nm | ✅ 干式过热/接合点漂移是顽疾,不建议大扭矩 |
| DQ381/DQ380 | 湿式 7DCT | 新 Mechatronic | ~420Nm | 🔷 接替 DQ250/200 |
| DQ500 | 湿式 7DCT | Temic | ~550Nm | ✅ 横置大扭矩 |
| DL382 | 湿式 7DCT(S-tronic 纵置) | — | ~500+Nm | 🔷 纵置奥迪 |
| DL501 | 湿式 7DCT(纵置) | Temic | ~600Nm | ✅ 老款 RS 纵置 |
- checksum:🔷 Mechatronic 校验+可能读保护,走工具;子批次差异大须核对。

### 3.4 Aisin
| 型号 | 类型 | 用于 | 现实 |
|---|---|---|---|
| TG-81SC / AWF8 (8AT) | 液力 8AT | 沃尔沃/奥迪横置/宝马 mini 等 | 🔷 |
| AWR6B45 (6AT) | 液力 6AT | 见 tcu-shift 速查 | ✅ |
| AWF21/TF-80SC | 液力 6AT | 沃尔沃/福特/路虎横置 | 🔷 |
- 控制器多为 Aisin/Denso 或 OEM 定制,字节序经 multi-vendor 核定。

### 3.5 GM 6L/8L/10L(6L80/6L90/8L90/10L80/10L90) — 液力,美系皮卡/后驱
- 控制器:GM 自研(部分 Delphi/Continental)。✅ 常与 ECM 集成扭矩管理。
- 特性:阀体是常见升级件(6L80 换挡阀体后可上 1000+Nm,见 tcu-shift 速查)。TCC 策略对爆震敏感。
- 访问:HP Tuners 支持较好(北美生态)。🔷

### 3.6 奔驰 7G/9G-Tronic(722.9 / 725) 与 Jatco CVT
- 722.6(5AT)/722.9(7G,液力)/725(9G,液力):奔驰自研 + 电子选换挡。🔷 9G 挡位多、锁止早。
- Jatco CVT(JF016E/JF017E 等,日产/雷诺三菱):**钢带夹紧力是物理上限,不建议扭矩升级**(见 tcu-shift 安全底线)。❓ 具体控制表结构须实际文件核对。

---

## 4. 顿挫/滑摩根因速查(按类型深挖 — 本技能相对 tcu-shift 的增量深度)

| 症状 | 液力 AT | 湿式 DCT | 干式 DCT | CVT |
|---|---|---|---|---|
| 换挡冲击/顿挫 | overlap 时序/换挡油压过大或时间过短 | 接合点漂移、充油过量、交接重叠错 | 接合点漂移、推杆磨损 | (无挡)速比突变/夹紧力阶跃 |
| 换挡滑摩/迟滞 | 油压不足、离合器磨损 | 充油不足、接合点偏高 | 半联动过长、过热降容 | 夹紧力不足→**钢带打滑** |
| 低速抖动/爬行顿挫 | TCC 抖振(锁止太早) | 起步离合器接合曲线 | **爬行控制**+离合器温度 | 起步夹紧力/TCC |
| 换挡无力软绵 | 扭矩握手降扭过多 | 同 + 预选档错误 | 过热保护介入 | 速比响应慢 |
| 清了自适应后变差 | 需重新学换挡压力自适应 | 需重学接合点(走学习循环) | 同 | 需重学夹紧力 |

**用法:** 先按 §1 定类型 → 用本表落根因方向 → 回 `tcu-shift-calibration` 找对应系统的表与改法 →
油压/TCC 表在 bin 里的定位可用 `tcu_map_locate.py`(见 tcu-shift-calibration/scripts)。

---

## 5. 与其他技能的关系

```
tcu-platform-library
├── 怎么调五大系统(换挡点/TCC/油压/档位限扭/握手) → tcu-shift-calibration
├── TCU bin 里定位换挡点/油压表 → tcu-shift-calibration/scripts/tcu_map_locate.py
├── 字节读写(核定字节序后) → multi-vendor-ecu-engine + bosch-conti-bin-engine
├── 写后 checksum → checksum-crc-engine
├── ECU 侧扭矩链/扭矩信号 → oem-strategy-library
├── 自适应清值纪律同源 → closed-loop-control §5
└── 所有结论经过 ecu-calibration-doctrine
```

## 6. 输出纪律
- 先定变速箱**类型**(液力/湿式DCT/干式DCT/CVT/AMT)再谈根因——类型不同控制机构完全不同。
- 具体子型号的 MCU/访问/checksum 标 ✅/🔷/❓,推断须以实际文件核对,不当事实。
- 数值/修改矩阵不由本技能出(回 `tcu-shift-calibration`);本技能出**平台控制现实与根因方向**。
- 改硬件/油压标定后必提示清自适应(否则残留学习值打架)。
- CVT 扭矩升级红线、商用车/安全相关照 doctrine 与 tcu-shift 安全底线。

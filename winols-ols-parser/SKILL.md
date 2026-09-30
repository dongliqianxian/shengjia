---
name: winols-ols-parser
description: >
  解析 WinOLS 工程文件（.ols / .kp）这种「专有二进制」格式，提取里面已建好的
  标定 MAP 定义树——每张图的命名、地址（文件偏移）、维度、raw 值域、以及尽力
  探测的 factor/offset。等效于把师傅工程里「已套好的 DAMOS」抠出来。
  当用户说"帮我看这个 .ols / .kp 文件"、"WinOLS 工程里都有哪些 map"、"从 ols
  里提取 map 命名和地址"、"师傅给的 ols 文件解析一下"、"这个工程定义了哪些表"、
  "ols 里 map 树导出来"、"把 ols 的 map 地址对到 bin 上"时，必须调用本技能。
  与 damos-a2l-parser 互补：那个解 A2L/DAMOS「文本定义」，本技能解 WinOLS
  「二进制工程」。本技能只提取定义元数据，不直接读写 bin 字节（交 bin-engine），
  不做 raw↔工程换算（交 winols-scaling-converter）。
  运行于 ecu-calibration-doctrine 之下，输出直接喂给 ecu-map-analysis 与
  stage1-calibration-matrix。
---

# WinOLS 工程文件 (.ols / .kp) 解析器

师傅给你的 `.ols` / `.kp` 里，往往已经把这台 ECU 的 MAP 都命名、定址、定好了
换算——这是一份「已套好的 DAMOS」。但 WinOLS 是专有二进制格式，普通 strings /
文本工具抓不到。本技能逆向这套结构，把定义树提取出来，让你**不开 WinOLS 也能
拿到命名+地址+维度**，并直接对到 bin 上分析。

> 定位：`damos-a2l-parser` 解 **A2L/DAMOS 文本定义文件**；本技能解 **WinOLS 二进制
> 工程（.ols 全工程 / .kp 纯定义包）**。两者都只产出「定义元数据」，字节读写交
> bin-engine，换算确认交 `winols-scaling-converter`。

---

## 1. 文件格式要点（逆向得来）

| 元素 | 存储方式 |
|---|---|
| 头部元数据 | 明文，**u32 长度前缀 + ASCII**（车型/ECU/软件号/内存段） |
| map 命名 | 同样的「u32 长度前缀串」，常带单位如 `(Nm)` `(hPa)` `(deg BTDC)` `(% Air)` `(Lambda)` |
| map 地址 | 命名之后窗口内的 u32（**= bin 文件偏移**），起始/结束成对出现 |
| 维度 | 由 `结束-起始` 字节数推出（除以数据宽度得格数） |
| factor/offset | 记录里的 IEEE double（**尽力而为，必须再确认**） |
| 功能组/文件夹 | `Air control` / `Engine torque` / `Injection system` / `Spark Advance` / `Volumetric system` 等组名 |

关键事实：
- **地址 = bin 文件偏移**（已在 JLR MED17.8.3 / D1783 上验证）；其它平台先用 `--bin` 交叉核对。
- **0x1000 对齐的 u32 是工程字段常量（大小/计数/段基址），不是 map 地址**——脚本已跳过。
- **工程主体若被压缩**，则提不到命名 → 直接用 WinOLS 打开（本技能仅处理未压缩的定义树）。

---

## 2. 用脚本提取（scripts/parse_ols.py）

```bash
# 基本：列出工程里的全部命名 MAP（命名/地址/维度）
python scripts/parse_ols.py PROJECT.ols

# 推荐：带原始 bin 交叉核对——每个地址处解出 raw 值域，离谱即说明地址可疑
python scripts/parse_ols.py PROJECT.ols --bin ORIGINAL.bin

# 手动指定标定地址区（自动推断不准时）
python scripts/parse_ols.py PROJECT.ols --range 0x180000-0x1FFFFF

# 导出 JSON（喂给后续脚本/技能）
python scripts/parse_ols.py PROJECT.ols --bin ORIGINAL.bin --json maps.json
```

脚本做的事：
1. 抽全部长度前缀串 → 头部元数据 + 候选 map 名。
2. 每个 map 名后找「size 合理(2..4096B)的相邻非对齐地址对」= 起始/结束 → 维度。
3. 给了 `--bin` 就在该地址解 raw min/max，**当作地址正确性的自检**（值域离谱=地址错）。
4. 尽力探测 factor/offset 候选（标注「需确认」）。

输出每张图：`命名 | 地址 | 字节数 | raw 值域`，并打印标定区范围与提醒。

---

## 3. 解析流程（doctrine 下）

```
Step 1  识别 .ols(全工程，常>bin) 还是 .kp(纯定义包，常几十KB)
Step 2  跑 parse_ols.py，带 --bin 交叉核对
Step 3  逐图核验地址可信度：
        ├─ raw 值域是否物理合理(λ~0.7-1.0×4096, 扭矩几十~几百, 点火3-45°…)
        ├─ size/维度是否像规整矩阵(16×16, 16×14, 8×8…)
        └─ 与 ORI↔MOD diff 的改动区是否吻合(若有改件)
Step 4  factor/offset → winols-scaling-converter 确认后才转工程值
Step 5  结构化 MAP 定义 → ecu-map-analysis(单表) / stage1-calibration-matrix(方案)
Step 6  字节读写 → bosch-conti-bin-engine / denso-bin-engine
```

---

## 4. 与其他技能的关系

```
winols-ols-parser  (.ols/.kp → 定义元数据)
├── 结构化 MAP 定义 → ecu-map-analysis / stage1-calibration-matrix
├── 地址+维度       → bosch-conti-bin-engine / denso-bin-engine（读写字节）
├── factor/offset   → winols-scaling-converter（换算确认）
├── 没命名/被压缩    → 改用 WinOLS，或 ghidra-tricore-disasm 无 DAMOS 找表
├── 找匹配工程文件   → ecu-calib-finder
└── 一切结论        → ecu-calibration-doctrine 约束
```

与 `damos-a2l-parser` 并列：A2L/DAMOS 文本走那个，WinOLS 二进制工程走本技能；
二者输出同构（命名/地址/维度/换算），下游技能一视同仁。

---

## 5. 输出纪律（与 doctrine 一致）

- **地址=文件偏移**，必须声明并尽量用 `--bin` 自检；raw 值域离谱即判地址错，不得照用。
- **factor/offset 为尽力而为**，写"需经 winols-scaling-converter / WinOLS 属性确认"，
  绝不用未验证的换算直接出工程值。
- 命名来自师傅工程，**可能命名有误或重复**（同名多张=多个条件/Bank，属正常）；
  不确定时按 `ecu-map-analysis` 用值域+轴+联动反查角色，不盲信名字。

  > **factor 对 ≠ 名字对。** 两个实证（Bosch MG1CS002 / P1793，都是工程里 factor 完全正确、
  > 名字完全错误）：
  >
  > | OLS 里的名字 | 真名（A2L 确证） | 实际功能 |
  > |---|---|---|
  > | `Engine load limiter #1/#2/#3 (% Air)` | `IKCtl_ratLoadRngAdpnM1/M2/M3_GT` | **爆震自适应的负荷分区边界**，完全不在限制路径上 |
  > | `Torque request during take off condition #1`（10 份） | `MonDrvDemA_tqCluAceped**_M` | **Level-2 功能监控层**的驾驶员需求镜像 |
  >
  > 第二个错名尤其危险：它决定了抬扭时必须**同步的是监控层**，按错名理解会漏掉这一步 → 跛行。
  >
  > **没有 A2L 时的廉价反证法**：按这个名字解释，**原厂车还开得动吗**？
  > 上例三条"负荷限制器"峰值只有 80/107/133 %rl，而原厂正常负荷就到 186.7 %rl ——
  > 真是限制器的话原厂车根本走不了，名字必错。
- 工程被压缩→提不到→**明说"该工程 map 树被压缩，请用 WinOLS 打开"**，不编造。
- 任何据此写盘的文件，checksum 一律经 `checksum-crc-engine` / WinOLS 插件重算。

# ECM Titanium 驱动：字段语义、证据与输入格式

> 以下结论全部来自 2026-09 BMW 530Li G38 / B48B20 / Bosch MG1CS201（数据集 `R4C9J7V5BONLY1`）实测，
> 每条写了怎么证的。换平台时当**待复核的假设**，让本技能的自检重新证一遍。

## 目录
1. 一行 map 条目的每个字段
2. `[N]` 的真实含义
3. 标题栏 Allowed Values 能信到什么程度
4. 单位、乱码与分段刻度
5. 输入格式：`tree.tsv` / `truth.json`
6. `ecm30.db`：能读什么、不能读什么
7. ECM 怎么给 bin 找驱动
8. 截图怎么拍最省事

## 1. 字段

`Limiter of Maximum torque #7 (Nm) - 0x72DA32 [1] - 6x6`

- **名字**：来自 ECM 的通用命名目录 `MAPS_TABLE`（1,487 条，多语言）。目录里同一族只有 `#1/#2`，
  更大的编号是 ECM 按驱动内实例顺序**运行时生成**的。
  证据：同一份 bin，旧 `.ols` 注释记作 `#8/#15/#16/#17` 的四张表，现驱动显示为 `#2/#7/#24/#25`。
  → 引用一张表永远用「族名 + 地址」。
- **地址**：驱动匹配的那个文件里的**裸文件偏移**。证据：12/12 张 map 视图截图，按该地址读 raw ×factor
  与显示值逐格一致；WinOLS 工程里同一张表的起始偏移与 ECM 地址相同。
- **R×C**：行 × 列，**列是内层快轴**。证据：222 张表在列方向的二阶差分平滑度 35 : 1 占优；
  截图网格的行列与字节顺序一致。A2L 里写 X = 列、`FNC_VALUES … ROW_DIR`（ROW_DIR = X 在内层）。
- **分类**：`MAPS_CATEG` 共 17 类——Air Control(10) / Engine Torque(11) / Injection System(12) / Rail(13) /
  Turbo System(14) / Limiters(15) / Spark Advance(16) / Volumetric System(17) / **Breakpoints(18)** /
  Deactivations(19) / VVT(20) / Launch Control(21) / Upshifting(22) / Downshifting(23) / Timing(24) /
  Special features(25) / Fuel pump(26)。`Breakpoints` 就是轴——驱动里有这一类时，轴地址 ECM 直接给了。

## 2. `[N]`

- 标题栏 `(map 1/6)` 证实 `[6]` = 该组共 6 张。
- **连续**的例子：`Torque request during take off condition - Sport #2 [4]` 4 份各 240 B 背靠背，
  第 4 份结束处正好是下一个 ECM 对象 `ECO #1`；`Spark advance base map #4 [2]` 2×140 B 结束处正好是 `#5`。
- **不连续**的例子：
  - `Intake #4 0x71EBE6 [6] 20x20 UWORD`（800 B/份）与下一个对象 `Intake #5 0x71EF88` 只隔 930 B；
  - `Optimal Spark Advance #1 0x754F90 [10]` 与 `#2 0x7554D0` 按任何步长都撞车；
  - `Exhaust #2 0x71CC66 [13]` 第 2 份位置实测是填充；
  - `Limiter of Maximum torque #10 0x72DEDC [4]` 第 3 份会压在已证实的轴数组 `0x72DF24` 上。
- **做法**：只在"放得下 + 不压已知轴 + 不是填充"时逐份输出；其余进补拍清单——在 ECM 里翻到
  `map 2/N`，标题栏会显示那一份的地址。

## 3. Allowed Values

- **能当符号提示**：`Spark advance base map #5` 标 `-64.00 .. 63.50 deg BTDC` ⇒ s8 × 0.5，截图逐格证实。
- **不能当 factor**：`Limiter of Maximum torque #24` 和 `#28` 标 `0.00 .. 65535.00 Nm`（暗示 ×1），
  但格子显示 471.30 / 476.30 这种 0.1 步长，raw 4713 → 实际 ×0.1。
- A2L 的上下限应取数据类型的物理范围——ECM 自己的 Allowed Values 就是这么算的（×0.1 的 UWORD → 0..6553.5）。

## 4. 单位、乱码与分段刻度

- `(癥)` 是 °C 的编码乱码（所有温度表都这样）→ 抄成 `degC`。
- **单位会标错**：`Limiter of Turbo pressure #12` 的轴标 `kg/h`，显示值 0..8.13，量级不像质量流量。
- **同一单位、不同内存段刻度不同**（按 64 KB 段分组补拍就是为此）：
  - 车速：`0x7A1Fxx` 段 raw / 128（180 / 200 / 250 / 300 全是整数），其它段 raw × 0.01（70.0 / 105.0 / 127.5）
  - 转速：`0x79B3xx` 段 raw × 0.25，其它段 raw × 1
- **同一族也可能不统一**：点火族一组 s8 × 0.5（截图证实），另一组 raw 挤在 99..217，s8 读会回绕，
  至少 `u8×0.25` / `u8×0.5−64` 两种自洽解读——字节层面分不出，必须该组一张截图。

## 5. 输入格式

### `tree.tsv`（驱动树抄录，UTF-8，Tab 分隔）

```
#CAT	Engine Torque	49
Engine Torque	Limiter of Maximum torque #1	Nm	0x7004C2	1	12	1
Engine Torque	Limiter of Maximum torque #7	Nm	0x72DA32	1	6	6
Air Control	Minimum limit of the target charging factor ...	factor	0x743182	?	?	?
```

列：分类 / 名字（不含单位括号）/ 单位 / 地址 / N / R / C [/ 备注]。看不清的格子写 `?`（这一行会进缺口清单）。
`#CAT` 行写 ECM 分类标题上显示的数量，用来对账；也可以用 `ecm_tree.py --expect "A=49,B=25"` 传。

### `truth.json`（map 视图截图读数）

```json
[{"addr": "0x72DA32", "rows": 6, "cols": 6, "allowed": [0, 6553.5],
  "start_cell": 0, "cells": [470, 475, 480, 485, 490, 500], "decimals": 2,
  "x": [800, 2000, 3300, 4500, 5800, 7000], "xunit": "RPM",
  "y": [0, 200, 400, 600, 800, 1000], "yunit": "hPa"}]
```

- `cells`：从 `start_cell`（行优先的格子序号）开始的一段**连续**显示值，照抄显示的小数。挑有变化的一行，
  整行全相同时 factor 和 offset 分不开。
- `x` / `y`：列轴 / 行轴显示值；R×1 曲线把断点放 `y`，1×C 放 `x`。轴单位写 `--` 且值是 1..n 时，
  那是 ECM 自己编的序号，不是轴。

## 6. `ecm30.db`

路径 `C:\ECMTitaniumLauncher3.0\ECMBackup\ecm30.db`，**未加密 SQLite 3**（实测 63.5 MB）。一律只读打开（`file:…?mode=ro`）。

| 表 | 内容 | 用途 |
|---|---|---|
| `DRIVERS`（208,424 行） | `NOMEFILE`(8 字符驱动名) / `START_MD5` / `END_MD5` / `OFFSET_MD5` / `MD5` / 品牌车型发动机马力 / `CKS` `CKS_FAM` / `KESS` `BDM` 工具支持 | 找驱动 |
| `MAPS_TABLE`（1,487 条 ENU） | `MAP_ID` / `MAP_DESC` / `MAP_TYPE`(= 分类 ID) / `MAP_PARENT`(族父) | 短名、族、分类交叉验证 |
| `MAPS_CATEG` | 分类树 | 大类分组 |
| `PROJECTS`（你的历史） | 项目名 / `DRV_NAME` / `ORI_MD5`(整文件) | "这个文件以前用哪个驱动打开过" |

实测对 B48 驱动的 222 个显示名 **100% 覆盖**（41 精确 + 181 经族父），解析出的分类与 ECM 树逐条吻合。

**读不到的**：`.DRT` 驱动本体（地址 / 维度 / 公式 / 轴真正存放处）——ECM 主程序经 `AlienDriveLetter.dll`
运行时挂虚拟盘，驱动在受保护环境里。`.ECMPROJ` 是 ZIP，内含项目名、源 bin 路径、`Z<驱动名>.DRT`
引用、驱动描述和 bin 副本，**没有 map 清单**（两份实测：map 名命中 0 次）。

## 7. ECM 怎么给 bin 找驱动

`DRIVERS` 的匹配键：对文件的 `[START_MD5, END_MD5)` 算 MD5，与该行 `MD5` **精确**比较。
全库 47,726 个不同窗口（BMW 2,252 个），窗口按 ECU 家族共用。`ecm_db.py --match-bin` 复现。

- **正对照（A）**：`520_C9AF.ori` 精确命中 `Z520_C9AF.DRT`，窗口 `0x7004C2–0x79CB98`。
- **窗口不一定在程序区**：MG1CS201 这族的窗口从第一张 map（0x7004C2）起覆盖整个标定区，驱动锁死到具体标定内容；
  528 个驱动共用这个起点。早先「窗口落在程序区、同软件不同标定命中同一驱动」的推断是错的，在此更正。
- **填充区碰撞**：窗口若是 00/FF 填充，谁都命中，毫无意义。实测一份 2 MB bench 文件的 10 个「命中」全是 100 % 填充窗口——
  判命中前先看窗口填充率。
- **驱动可以手选 / 强制**：用户项目 #1057 所用驱动的窗口超出了那份文件本身，任何 64 KB 对齐的加载基址都复现不了 MD5。
  所以 `PROJECTS.DRV_NAME` 只说明「以前用它打开过」，不是 MD5 命中的证据。

本机库可能是旧快照（下载标志全为 0），**查不到不等于 ECM 没有驱动**。

## 8. 截图怎么拍最省事

- **驱动树**：窗口最大化并拉宽名字列；每个分类都展开；每张与上一张重叠一行；分类标题的 `[数量]` 截进去。
- **map 视图**：标题栏必须完整（地址、`map k/N`、Allowed Values）；两个轴都要露出来；至少一整行数据。
- **先跑后拍**：先用驱动树 + bin 跑一遍，让 `shotlist.py` 告诉你拍哪几张，而不是随手截。
  实测 12 张随手截的截图覆盖了 7 个量纲家族；按分组点名拍，同样张数能多覆盖一倍以上的对象。

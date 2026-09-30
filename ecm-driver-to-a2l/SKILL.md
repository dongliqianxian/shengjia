---
name: ecm-driver-to-a2l
description: 把【ECM Titanium 驱动里的 map 清单】（驱动树截图 / 手抄清单 / ECM 本机数据库目录）+【原厂 bin】+【少量 map 视图截图】做成一份逐项可复核、按 ECM 分类树分组（大类 = ECM 分类，小类 = 同族）的 A2L，WinOLS / INCA 可直接导入；同时出 HTML 清单和"还差哪几张截图"的最小补拍清单。当用户说"根据 ECM 截图做 A2L / 把 ECM 驱动做成 A2L / ECM 驱动转 A2L / 用 ECM 树生成 A2L / ECM 截图里的 map 在 bin 里找出来 / 按 ECM 分类出定义文件 / ECM 地址导成 WinOLS 定义 / 这张 ECM 截图的轴在哪 / 截图找轴 / ECM 是怎么找驱动的 / 这个 bin 用哪个 ECM 驱动"时必须调用。即使用户只丢来几张 ECM 驱动树截图加一个 bin，也要调用。与 bin-to-a2l 的分界：那个管【没有任何厂商定义】的自描述裸 bin；本技能管【已有 ECM / WinOLS 厂商定义】的情况，不论平台是否自描述。只读 A2L → damos-a2l-parser；只读 .ols → winols-ols-parser。运行于 ecu-calibration-doctrine 之下。
---

# ECM 驱动 → A2L

ECM Titanium 的驱动树已经替你做完了一半：**地址、维度、分类、名字**。
A2L 需要的另一半——**数据宽度、符号、factor / offset、轴**——驱动树里没有。
本技能把前一半当输入，用 bin 和**少量** map 视图截图把后一半**逐项证出来**，写成 A2L，
并点名还缺哪几张截图。证不出来的对象不猜：标 C 级、写索引轴、进补拍清单。

## 0. 分流

| 手上有 | 用哪个 |
|---|---|
| ECM 驱动树截图 / ECM 地址清单 + bin | **本技能** |
| WinOLS 工程里的 map 列表 + bin | `winols-ols-parser` 抽列表 → 转成本技能的 `tree.tsv` → 本技能 |
| 只有裸 bin、没有任何定义 | `bin-to-a2l`（自描述平台）/ `blind-map-finder`（非自描述） |
| 现成 A2L / DAMOS | `damos-a2l-parser` |
| 同 ECU 族、**另一个数据集**的真 A2L | 先跑 `scripts/a2l_anchor_probe.py` 判能不能借名字——多数借不了地址 |

## 1. ECM 一行里每个字段的真实含义

`Limiter of Maximum torque #7 (Nm) - 0x72DA32 [1] - 6x6`

| 字段 | 实测含义 | 坑 |
|---|---|---|
| `0x72DA32` | 驱动所匹配文件里的**裸文件偏移** | 只对 MD5 命中的那个软件版本成立（§7） |
| `[N]` | 同组共 N 张，标题栏 `(map k/N)` 证实 | **不保证连续**，逐份验证 |
| `6x6` | **行×列，列是内层快轴** | A2L 里 X = 列、`ROW_DIR` |
| `#7` | ECM 按驱动内**实例顺序运行时生成** | 换驱动版本编号就变；引用一律「族名 + 地址」 |
| `(Nm)` | 驱动作者手填 | 会标错；乱码 `癥` = °C |
| 标题栏 Allowed Values | 驱动声明的范围 | **只当符号提示**（−64.00..63.50 ⇒ s8），不能当 factor 证据 |

每一条的证据和反例：`references/ecm-field-semantics.md`。

## 2. 向用户要什么（按杠杆排序）

1. **原厂全片读取 bin**——必须是 ECM 打开的同一个文件。
2. **驱动树截图**：窗口拉宽到长名不截断；**每个分类都展开**（含 Rail、Breakpoints）；
   相邻两张**重叠一行**；分类标题的 `[数量]` 要截进去——它是抄录完整性的免费校验和。
3. **map 视图截图，每个"量纲家族"一张**，不是随便截：标题栏（地址、`map k/N`、Allowed Values）
   + 两个轴 + 至少一整行数据。先用前两样跑一遍，`shotlist.py` 会**点名**截哪几张。
4. 可选：本机 `C:\ECMTitaniumLauncher3.0\ECMBackup\ecm30.db`（MAP_ID / 族 / 分类交叉验证，判断 bin 属于哪个驱动）。

**不用要** ECM 项目备份 `.ECMPROJ`：实测只有项目元数据 + 驱动文件名 + bin 副本，**没有 map 清单**。

## 3. 流水线

```bash
S=~/.agents/skills/ecm-driver-to-a2l/scripts
python $S/ecm_tree.py  --tree tree.tsv --bin ORI.bin --expect "Engine Torque=49,Rail=1" --out tree.json
python $S/closure.py   --truth truth.json --bin ORI.bin --out closure.json
python $S/resolve.py   --tree tree.json --closure closure.json --bin ORI.bin --db ecm30.db --out objects.json
python $S/emit_a2l.py  --objects objects.json --closure closure.json --bin ORI.bin \
                       --out CAR.a2l --html CAR.html --project CAR --range 0x700100-0x7FF7DF
python $S/shotlist.py  --objects objects.json --tree tree.json --out shotlist.md
python $S/ecm_db.py    --match-bin ORI.bin --brand BMW          # 这个 bin 属于哪个 ECM 驱动
```

每个脚本带 `--selftest`（含负向对照）。`tree.tsv` / `truth.json` 格式见 `references/ecm-field-semantics.md` §5。
字节序默认小端（AURIX / TriCore）；大端核先经 `multi-vendor-ecu-engine` 核定再加 `--endian be`。

| 步 | 做什么 | 判据 |
|---|---|---|
| ① 抄录校验 | 分类计数对账；奇地址；整组几何 | 只有 `addr + N·R·C·w == 下一个地址` 且 w 唯一才算宽度证据——k<N 时 u8×2 份与 u16×1 份字节数相同 |
| ② 截图闭合 | 扫常用 factor 表，找残差 < 半个显示位的 (宽度, 符号, factor, offset) | 不用最小二乘：ECM 显示已四舍五入，斜率会偏；多解时先看 Allowed 的符号，再看整表平滑度 |
| ②′ 找轴 | 容差匹配数值序列，**先往数据后面找** | MG1CS201 实测 13 条存储轴全在数据之后：距表尾 +36～+6454 B（距表首 +72～+7254 B）；等距且没存 → `FIX_AXIS_PAR_DIST`；ECM 自己显示的 `--` 1..n 不是轴 |
| ③ 裁决 | 宽度 6 道关：证明 → 自身字节 → 同名族兄弟 → 标量圆整投票 → 布局 → 先验（只数已证明的宽度）。刻度：截图家族 → 名字物理带 `ecm_bands.tsv` → 单位候选。然后副本、MAP_ID | 数据压过布局；推断出来的宽度不参与任何投票，防止一个错传染整类；字节角度量 raw 挤在中段 = 编码多解，**标出来，不挑一个聪明的解释**。每条规则的来历见 `references/case-mg1cs201-b48.md` |
| ④ 写 A2L | 分组、命名、格式、限值 | §5 §6；自检 7 项 |
| ⑤ 补拍清单 | 按 (分类, 单位, 宽度, 多解, 64 KB 段) 分组，一组一张 | 贪心：先覆盖最多的 |

## 4. 证据等级（严格口径）

| 级 | 条件 |
|---|---|
| **A** | 该对象**自己**的截图逐格闭合（显示 ÷ raw 零离散） |
| **B** | 同分类、同单位、同宽度的家族里有 A 级闭合，本对象用同一 factor 全部落在物理带内且读数连续 |
| **C** | 只有单位候选表 + 物理带（+ 取整救援），或编码多解 |

家族外推**不是**已验证，记成 A 是过度声明。实测 MG1CS201 点火族就**不统一**：同分类同单位里，
一组是 s8×0.5（截图证实），另一组 raw 挤在 99..217，至少三种自洽解读。

## 5. 命名与分组

- **标识符**：`<ECM 显示名>__<短名>`，副本加 `_cK`。A2L 标识符**不能含括号**，括号放描述里。
- **LONG_IDENTIFIER**：`<ECM 显示名> (<短名>) [copy k/N] - <中文功能> | R×C 类型 ×factor 单位 | 范围 | evidence G`
- **短名**默认取 ECM 数据库 `MAP_ID`（可核对）。有**可信**博世名时用 `--short names.json` 覆盖——
  来源只能是同数据集 A2L，或 `a2l_anchor_probe.py` 判为空间一致的真实局部段。**不自拟博世符号**。
- **分组**：大类 = ECM 分类（`ROOT GROUP`），小类 = 同族（去掉 `#N` 的名字，附 `MAP_PARENT`）`SUB_GROUP`。

## 6. 显示、限值与语法

- `FORMAT`：物理量 `"%10.2"`，纯索引轴 `"%6.0"`；`COEFFS_LINEAR` 全精度（1/4096 截成 0.00 整表就废）。
  λ(1/4096)、压比(1/16384)、升程(0.001 mm) 的显示会四舍五入，raw 精度不受影响——交付时提一句。
- **上下限 = 数据类型能表示的物理范围**（等于 ECM 的 Allowed Values），不能用当前数据的 min/max：
  否则一个 350 Nm 的 1×1 限值上限就是 350.000001，任何校验上下限的工具都拒绝你抬它。
- `FIX_AXIS_PAR_LIST` 是 `/begin … /end` 块，不是单行关键字。

## 7. ECM 怎么给一个 bin 找驱动（用户常问）

`ecm30.db` 是**未加密 SQLite**。`DRIVERS` 每行带字节窗口 `[START_MD5, END_MD5)` 和该窗口的 `MD5`；
ECM 对文件这段算 MD5 去**精确**查表（本机正对照：`520_C9AF.ori` → `Z520_C9AF.DRT`）。所以驱动**锁死**，
驱动里写裸文件偏移没问题。`ecm_db.py --match-bin` 复现这个查找。三个坑：窗口可以覆盖整个标定区
（MG1CS201 族从第一张 map 起算，改过标定就不再命中）；00/FF 填充窗口谁都命中，毫无意义；
项目历史里的驱动可能是手选的，不是命中证据。本机库可能只是旧快照：查不到 ≠ ECM 没有驱动。

**同类驱动套到新 bin 上**不是改驱动，是跨文件地址移植——交 `reference-tune-porting`
（分段常数漂移 + 独立物理锚点验证），本技能产出的 A2L 可作为移植的源定义。

## 8. 交付必须贴自检原文

`emit_a2l.py` 七项：①标识符合法且唯一 ②地址在范围内 ③对象不重叠 ④**截图逐格回放**
⑤轴统计（stored / 等距 / 列表 / 未解析）⑥显示格式与 COEFFS 精度 ⑦限值包住数据。
再加 `shotlist.md` 的补拍张数。"已验证"三个字不算交付。

## 9. 做不到的事

- **读 `.DRT` 驱动本体**：ECM 运行时挂受保护虚拟盘；`ecm30.db` 只有目录和匹配键。
- **非截图对象的轴**：没有控制组达标前不自动写——写索引轴、计入 UNRESOLVED、进补拍清单。
- **多份副本哪份生效**：静态给不了，要 LOG。
- **跨数据集借地址**：先 `a2l_anchor_probe.py`。实测 MG1CS201 `R2B22002B → R4C9J7V5B` 不可移植。
- **checksum**：本技能不改 bin；改 bin 交 `checksum-crc-engine`。

## 10. 交接

上位 `ecu-calibration-doctrine`；平台层 `mg1-platform` / `md1-platform` / `edc17-platform`；
字节序 `multi-vendor-ecu-engine`；单表功能 `ecu-map-analysis`；副本齐全性 `parallel-map-finder`；
拿这份 A2L 做方案 `stage1-calibration-matrix`。完整案例与数字：`references/case-mg1cs201-b48.md`。

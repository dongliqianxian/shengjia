---
name: rom-similarity-finder
description: >-
  给一个【陌生 bin】,在本地标定库里找出【字节级相似】的已有文件,好借用现成的定义
  (A2L/DAMOS/OLS 工程/已知表地址),而不是从零盲扫。当用户说"这个文件我库里有没有类似的/
  有没有见过这个 ECU/这份 bin 和我以前那个是不是同一个/帮我在文件库里找相似的/
  这两个文件像不像/是不是同一软件版本/我有没有这个车的定义/能不能复用之前的工程/
  找同族文件/找兄弟文件/这个 dump 里包不包含那个标定区"时,必须调用本技能。
  即使用户只丢来一个 bin 说"看看我库里有没有",也要调用。
  本技能是【检索层】,产出【排序候选 + 精确字节比对确认】。算法: 字节级滚动哈希 shingle +
  单排列 MinHash + containment,**位置无关**——裸 bin 与从 .ols 抽出的带容器头文件仍能匹配,
  固定步长开窗做不到这点。
  【与 ecu-calib-finder 的分界,不重叠】: 那个按【文件名/协议/MCU/平台元数据】找兼容文件,
  靠的是标识符;本技能按【文件内容本身】找,不看名字、不看后缀、不需要任何元数据,
  改过名字的文件照样能配上。两者互补,通常先跑本技能。
  与 blind-map-finder 的关系: **本技能排在它前面**——库里有相似文件就借定义,
  盲扫是最后手段。字节读写交 bin 引擎、功能判定交 ecu-map-analysis、
  .ols 内部 ROM 抽取交 winols-ols-parser、checksum 交 checksum-crc-engine。
  运行于 ecu-calibration-doctrine 之下。
---

# ROM 相似度检索(rom-similarity-finder)

## 0. 本技能的定位

**检索层。** 它回答一个问题:**「这份陌生 bin,我以前见过像的吗?」**

这是拿到未知文件后**应该问的第一个问题**,却经常被跳过。跳过的代价是:明明库里躺着同平台
同软件版本的文件(带 A2L、带做好的 OLS 工程、带已证实的表地址),却从零开始盲扫。

> **在标定顺序里的位置**:doctrine §2 黄金顺序 ③「尽量从一个已知良好的标定出发(改动小 = 风险小)」。
> 本技能就是**把 ③ 变成可执行的检索动作**。

**排在 `blind-map-finder` 之前。盲扫是最后手段,不是第一手段。**

---

## 1. 先分流:找相似文件有两条路,别走错

| 你手上有什么 | 用哪个 | 依据 |
|---|---|---|
| 文件名 / 硬件号 / 软件号 / Cal ID / 协议 / MCU 型号 | `ecu-calib-finder` | **标识符**匹配 |
| **只有文件内容本身**(名字没意义、后缀乱、来源不明) | **本技能** | **字节内容**匹配 |
| 已知一张表,想在**同一个 bin 内**找副本 | `parallel-map-finder` | 同文件内种子驱动 |
| 库里确实没有相似文件 | `blind-map-finder` | 形态盲扫 |

**通常先跑本技能**:它不需要任何元数据,改过名、去过头、切过块的文件照样能配上。
`ecu-calib-finder` 的结果与本技能的结果**互为印证** —— 两边都指向同一个文件时置信度最高。

---

## 2. 算法(以及为什么必须是这个算法)

```
文件字节 ──► W=64 字节窗口,滑过【每一个字节位置】,Karp-Rabin 滚动哈希 O(1) 更新
         ──► splitmix64 雪崩混合
         ──► bucket = h mod 128,每桶只留【最小】值
         ──► 指纹 = 128 × uint64 = 1KB/文件  (+ distinct shingle 基数)
```

### 2.1 为什么必须每个字节位置都开窗 —— 本算法唯一不可替代的性质

同一份 ROM,**裸 `.bin`** 与**从 `.ols` 里抽出来的内部 ROM**(前面多了几十字节容器头),
字节内容一样但**偏移不同**。

- shingle 在**每个**字节位置取 → 两边窗口内容仍然重叠 → **与对齐无关**
- 固定步长开窗(4KB / 256B)**做不到**:几十字节的偏移就让匹配率掉到零

你的实际场景全是这个:bench 读出来的、OLS 里抽出来的、别人给的 dump,**头部长度经常不一样**。

> **实测**(`--selftest`):偏移 1 / 76 / 512 / 4096 字节后,含入度仍为
> **1.000 / 1.000 / 1.000 / 0.997**。

### 2.2 为什么不能只看 Jaccard —— containment 治什么病

**小 ROM 完整包含在大 dump 里**(bench 读全片 flash vs 别人只给标定区)时,
Jaccard 会被尺寸差异拉低(≈ |A|/|B|),但那其实是**完全匹配**。

```
Jaccard      = |A∩B| / |A∪B|        ← 尺寸差异会压低
containment  = |A∩B| / min(|A|,|B|)  ← 专治"小的完整包在大的里"
最终分       = max(两者)
```

> **实测**:80KB 块取自 400KB 文件 → **Jaccard 仅 0.242,含入度 1.000**。
> **只看 Jaccard 会直接漏掉这个命中。**

### 2.3 数值判据(已用真实库校准)

**实测**:Subaru Legacy GT `al81a` 族,45 个文件建索引,用 `al81a_4812-Example1` 查询 ——

| 排名 | 粗筛 Jaccard | 二阶段精确 | 实际是什么 |
|---|---|---|---|
| 1 | 0.953 | **99.01%** | 同基底 ROM 的另一个调校(Example2) |
| 2 | 0.914 | 98.27% | **这两个改装件的原厂底 ROM** ← 目标用例 |
| 3 | 0.891 | 98.39% | 相邻软件版本 `al80a_4801` |
| 4 | 0.664 | — | 更远的版本 `al82a_4822` |
| 5 | **0.016** | — | 不同族 `al780_4252`,**正确排除** |

**#4 → #5 从 0.664 断崖到 0.016,恰好切在「同族 / 异族」边界上。** 由此固化:

| 分数区间 | 含义 | 动作 |
|---|---|---|
| **≥ 0.90** | 同基底 ROM(同版本不同调校,或改装件 vs 其原厂底) | 定义**大概率**可直接套 |
| **0.60 – 0.90** | 同族相邻软件版本 | 定义**多数**可套,**逐表验证** |
| **0.05 – 0.60** | 同平台较远版本 | 结构参考,**地址不可直接照搬** |
| **< 0.05** | 不同族 | 转 `blind-map-finder` 盲扫 |

> 注意 #2 与 #3:**粗筛把 0.914 排在 0.891 前面,但精确比对是 98.27% < 98.39%,顺序反了。**
> 这正是**必须走第二阶段**的理由 —— 粗筛只决定「先看哪几个」,不决定排名。
>
> 区间为 doctrine 证据 **B 级 高概率推断**(单一平台族校准所得)。换平台建议重新校准一次。

---

## 3. 两阶段纪律(doctrine §13 逆向纪律)

> **MinHash 是粗筛。它给的是候选,不是结论。**

| 阶段 | 做什么 | 精度 |
|---|---|---|
| **① 粗筛** | 指纹比对,全库排序 | K=128 → 标准误 ≈ **8%** |
| **② 确认** | 重新打开 top-N、**按字节搜索对齐**、重叠区**逐字节精确比对** | 精确到字节 |

**只有第 ② 阶段的数字可以写进结论。** 第 ① 阶段的分数只用来决定「先看哪几个」。

这与 `blind-map-finder`「永远说候选」、doctrine §13.3「指针命中是确认过滤器,不是第五类候选」
是同一条纪律:**筛选与确认必须分开**。

```bash
python scripts/rom_similarity.py query --bin UNKNOWN.bin --top 20 --verify 5
```
`--verify 5` 就是对前 5 名自动跑第 ② 阶段,输出 `对齐 offset=+76  99.87% 相同  重叠 1024K`。

---

## 4. 用法

```bash
# 建索引(增量:mtime 没变的跳过,可反复跑;多个目录可加进同一个库)
python scripts/rom_similarity.py index --lib E:/ECU_Tuning_Projects
python scripts/rom_similarity.py index --lib DIR --workers 8        # 大库用多进程
python scripts/rom_similarity.py index --lib DIR --ext bin,ori,mod,rom --max-mb 64

# 查询
python scripts/rom_similarity.py query --bin UNKNOWN.bin --top 20
python scripts/rom_similarity.py query --bin UNKNOWN.bin --verify 5    # 粗筛 + 精确确认

# 两个文件直接精确比对(不需要索引)
python scripts/rom_similarity.py verify --a A.bin --b B.bin

python scripts/rom_similarity.py stats        # 索引状态
python scripts/rom_similarity.py --selftest   # 19 项自测
```

索引默认落在 `~/.claude/rom_index.sqlite`,约 **1KB/文件**,10 万个文件约 100MB。
实测吞吐约 **10 MB/s**(单线程),2MB 的 bin 约 5 个/秒;`--workers N` 近线性加速
(实测并行与串行指纹**逐字节一致**,45/45)。**只有主进程写 SQLite**,不会有并发写锁。

> `.hex` / `.s19` 是 **ASCII 文本**格式(Intel HEX / S-record)。同格式之间可比,
> 但**与 `.bin` 互相测不出相似** —— 同一份 ROM 的 bin 与 hex 字节完全不同。
> 要索引它们请**单独建库**(`--ext hex,s19 --db other.sqlite`),别和 bin 混在一起。

---

## 5. 诚实边界

- **相似 ≠ 定义可直接套用。** 软件版本不同时,同一地址的表可能已挪位、改维度、改 scaling。
  借来的定义**仍须**经解码自检(值落物理带)+ `ecu-map-analysis` 判功能。
  高分只是说「值得先看这个」,不是「可以照抄」。
- **加密 / 压缩文件:分两种情况,别一概而论。** 索引时会自动算字节熵并告警(≥7.5 视为可疑)。
  - **同密钥同族内部仍可比** —— 实测 EcuTek Stock ROM 全库熵 7.79–7.91(**全部加密**,
    连文件名不带 `-enc` 的也是),但 `al81a` 族内部照样测出 0.95 / 99.01%。
    原因是该加密**分块无链接**,局部改动不扩散。
  - **跨密钥则完全测不出** —— 实测 BRZ 的 106 个连续软件版本互查**零候选**,
    相邻两版仅 0.849% 字节相同(随机基线 0.391%)。**零候选不等于库里没有同源文件。**
  - **`.ols` 工程内部是压缩的**,必须先用 `winols-ols-parser` 抽出内部 ROM 再索引。
    默认后缀表因此**不含** `.ols`/`.kp`。
- **本技能不判功能、不给地址、不猜 scaling、不改字节。** 它只回答「像不像」。
- **加密 / 打包 / 不同压缩率的同源文件测不出相似**,这是字节级方法的固有边界,不是 bug。
- **分数区间是经验带**,首次使用应在自己的库上用已知同源文件校准。

---

## 6. 出处与许可

算法设计派生自 **romHEX14 Community Edition**（`ctabuyo/romHEX14-community`，
commit `b98538fe`，`src/io/winols/RomFingerprint.h` 等），
**Copyright (C) 2026 Cristian Tabuyo，GPL-3.0-or-later**。
`scripts/rom_similarity.py` 为独立重写，同样以 GPL-3.0-or-later 分发。

完整出处、上游原文要点、以及**本实现与上游唯一一处有意分歧**（containment 的集合大小：
上游用剥填充后尺寸，本实现用估计 distinct 基数）见
[references/algorithm-source.md](references/algorithm-source.md)。

> 核实提示：该仓库根目录无 LICENSE 文件，GitHub API 会报 `license: null`。
> 许可声明在 `NOTICES.md` 与 `README.md §License` 正文中，已逐条核实。
> **别因为 API 说没许可就当无许可。**

---

## 7. 与其他技能的衔接

```
陌生 bin
   │
   ├─► rom-similarity-finder ────► 找到高分同源文件?
   │        (本技能)                    │
   │                            ┌───────┴────────┐
   │                          有                 没有
   │                            │                 │
   │              定义来源在哪?  │                 └─► blind-map-finder(形态盲扫)
   │         ┌──────────────────┼──────────────┐         └─► ghidra-tricore-disasm(代码印证)
   │      .a2l/DAMOS         .ols/.kp        裸 bin
   │         │                  │              │
   │  damos-a2l-parser   winols-ols-parser   bin-to-a2l
   │         └──────────────────┴──────────────┘
   │                            ▼
   │                   ecu-map-analysis(判功能) ──► stage1-calibration-matrix(出方案)
   │                            ▼
   └─► 有 ORI+MOD 配对? ──► reference-tune-porting(逆出配方并移植)
                                ▼
                        checksum-crc-engine(交付闸门)
```

- **`ecu-calib-finder`**:按标识符找;本技能按内容找。**两者结果互为印证**。
- **`parallel-map-finder`**:同一 bin 内找副本;本技能跨文件找同源。
- **`reference-tune-porting`**:本技能找到 ORI/MOD 配对后,由它逆出改动配方并移植。
- 字节读写交 bin 引擎(经 `multi-vendor-ecu-engine` 核定字节序);
  写入前七项门禁与 checksum 归属照常适用。

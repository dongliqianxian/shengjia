# 算法出处、许可，与本实现与上游的分歧

## 出处（2026-08-24 逐条核实）

本技能的算法设计派生自 **romHEX14 Community Edition**：

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/ctabuyo/romHEX14-community` |
| commit | `b98538fe4c7bcf1fa2fd29ec34b13973eef9f2e5`（2026-08-23） |
| 指纹与相似度定义 | `src/io/winols/RomFingerprint.h`（5661 B） |
| SQLite 候选索引 | `src/io/winols/SimilarityIndex.h`（5385 B） |
| 第二阶段字节对齐 | `src/io/winols/SimilarFilesDlg.cpp`（58830 B） |
| 版权 | Copyright (C) 2026 Cristian Tabuyo `<contact@romhex14.com>` |
| 许可 | **GPL-3.0-or-later** |

> **关于许可的一个坑**：该仓库**根目录没有 LICENSE 文件**，所以 GitHub API 返回
> `"license": null`。许可声明实际在 **`NOTICES.md` 正文**
> （"romHEX14 is distributed under GPL-3.0-or-later"）与 **`README.md` §License**
> （完整 GPLv3 声明 + 版权行）中。
> **不要因为 API 说没有许可证就当成无许可** —— 已核实为 GPL-3.0-or-later。

`scripts/rom_similarity.py` 为**独立重写**（非逐行翻译），但设计来源如上，
因此同样以 **GPL-3.0-or-later** 分发。

---

## 上游原文确认的设计要点（本实现全部遵循）

摘自 `RomFingerprint.h` 头注释：

- **K = 128 桶**，`kMinHashK`；必须是 2 的幂（上游用 `hash & (K-1)`，本实现用 `h % K`，等价）
- **W = 64 字节 shingle**，`kShingleSize`。上游给的理由值得记住：
  > 64 B 足够短，使小的标定改动不会摧毁整片区域的 shingle；足够长，使内容有区分度。
- **每个字节位置**都开窗，Karp-Rabin O(1) 递推 → 全文件 O(N)
- Jaccard = `count(A[i] == B[i] != 0) / count(A[i] != 0 ∧ B[i] != 0)`
- **K=128 时 Jaccard 标准误 ≈ 1/√K ≈ 8%**
- `best() = max(Jaccard, containment)`
- 第二阶段：重新打开候选 → 按字节搜索对齐 → 重叠区精确字节比对

**上游自己记录的失败教训**（本实现存在的理由）：

> 我们上一版用固定步长 4KB / 256B 开窗，**76 字节的偏移就让匹配率掉到零**。

以及分桶的作用：

> 分桶把哈希空间切成 K 个独立槽。桶的最小值反映的是**该槽内**的内容，而不是全局最稀有的哈希。
> 同族的两个无关 ROM 不再因为共享稀有模式（**向量表、0x00 填充**）而误撞。

---

## 本实现与上游的分歧（一处，有意为之）

| | 上游 | 本实现 |
|---|---|---|
| containment 的集合大小 | `bytesScanned` = **剥掉空填充后**的逻辑尺寸 | **估计 distinct shingle 基数**（`_cardinality_from_mins`） |

**目的相同**：不让填充区虚增集合大小。

**为什么改**：实测你库里的真实 ECU bin **有 49–62% 是填充**。若直接用原始文件大小，
containment 会系统性偏低。上游的解法是先剥填充，这要求先定义「什么算填充」
（0xFF? 0x00? 重复块?）；本实现改为从桶最小值反解 distinct 基数 ——
**重复值永远不影响最小值**，所以天然免疫填充，不需要任何填充判定规则。

代价：基数是估计值，实测平均绝对误差 7.2%（12 组随机，30 万–300 万 distinct，填充 0–70%），
与 MinHash 自身 8.8% 标准误同量级，不构成新的主导误差源。

### 三种解法对照（都在处理同一件事：不让填充虚增集合大小）

| 实现 | 做法 | 覆盖范围 |
|---|---|---|
| **上游 romHEX14** | `bytesScanned` = "logical size after empty-padding strip" | 按注释是剥填充后的尺寸 |
| **另一份独立实现**（Codex 版，`~/.codex/skills/`） | `_trim_padding()` 剥**首尾**的 `0x00`/`0xFF` 连续段；另在 shingle 层**跳过整窗全填充**的 shingle | 首尾 + 全填充窗口；**内部零散填充不计** |
| **本实现** | 从桶最小值反解 distinct 基数 | 全部重复内容，**含内部零散填充** |

> Codex 版在 shingle 层跳过全填充窗口这一手很干净，比单纯剥首尾更彻底。
> 但它的 `bytes_scanned` 仍是「首尾剥完后的长度」，**内部零散填充不会被扣掉**；
> 而实测你库里真实 ECU bin 的 49–62% 填充**主要分布在内部**（不是集中在首尾），
> 所以在这类文件上它的集合大小仍会偏大、containment 偏低。
>
> 本实现的做法不区分填充在哪里，也不需要定义「什么算填充」——
> 重复值不影响最小值，所以自动扣掉。代价是基数变成估计值（误差 7.2%）。
>
> **三者没有绝对优劣**，是同一问题的三种工程取舍，记录在此以便将来对照。

---

## 上游有、本实现暂未做的

| 上游特性 | 状态 |
|---|---|
| `.ols` 多版本容器：存**各桶跨全部版本取 MIN**（并集草图），任一版本相似即命中 | 未做。本实现不碰 `.ols`，交 `winols-ols-parser` 先抽内部 ROM |
| `dataArea` 第二套草图（标定区专用） | 上游自己也标注为 reserved / 未启用 |
| 尺寸失配惩罚（用 `bytesScanned`） | 未做。本实现靠 containment 与分数带区分，已在真实库上验证足够 |

## 本实现有、上游没有的

- **熵闸门**：索引时算字节熵，≥7.5 判为疑似加密/压缩并告警。
  实测 EcuTek Stock ROM 全库熵 7.79–7.91（**全部加密**），跨密钥完全测不出相似 ——
  没有这个闸门会把「加密」误判成「算法失效」。
- **19 项内建 selftest**，含滚动哈希与朴素定义的逐值比对。
- **真实库校准的分数带**（见 SKILL.md §2.3）。

---
name: parallel-map-finder
description: >
  【平行地图(parallel maps)批量查找】技能——种子驱动:你已知一张表,在同一个 bin 内找出所有
  【结构/数值分布相似】的平行副本。用于调程序时"同一物理量有多份相似表"(多变体/多模式/多档位/
  bank1-2/进排气侧/主控+限制副本/Level-2 扭矩监控镜像),避免只改一份漏掉平行副本导致别的模式/档位
  行为不一致或监控镜像不同步 → limp/报错。
  当用户说"平行地图/平行 map/找相似的表/找这张表的副本/同一张图有几份/多个模式的表/bank1 bank2 对应表/
  监控镜像表/改了一份还有没有别的一样的/这张表在别处有没有复制/批量找相似 map/找同族表"时,必须调用本技能。
  即使只说"找平行地图",也要调用。
  本技能用 scripts/parallel_map_scan.py:给种子(地址+维度+dtype+字节序),在同 bin 内按【数值接近
  (near-identical=复制/监控镜像)+ 形状相同(shape-variant=缩放/偏移变体)】两条正交线找副本,
  按相似度分级输出 + 差异摘要 + NMS 去重。
  与 blind-map-finder(无种子从零盲扫)、damos-a2l-parser(按命名)、ecu-calib-finder(跨文件)不同——
  本技能是【同一 bin 内、有种子、按内容相似性】。字节读写交 bin 引擎、功能判定交 ecu-map-analysis、
  写后 checksum 交 checksum-crc-engine。运行于 ecu-calibration-doctrine 之下。
---

# 平行地图批量查找(Parallel Map Finder)

## 0. 本技能解决的实战痛点

调程序时,同一物理量在 bin 里常有**多份高度相似的表**。改标定只改一份、漏掉平行副本 →
别的模式/档位/bank 行为不一致,或**监控镜像没同步 → limp/报错**。这正是 doctrine 联动清单反复强调
的"漏改一环"。手动在 bin 里找齐所有平行副本极易漏——本技能自动找齐。

**平行地图的常见来源:**
| 来源 | 例子 |
|---|---|
| 多变体/多模式 | 同发动机不同功率变体、Eco/Normal/Sport、不同档位限制 |
| bank/侧别 | bank1/bank2、进气/排气 VVT、左右缸 |
| 主控 + 副本 | 一张主控表 + 若干形状相近的限制器/温度补偿/过渡副本 |
| 监控镜像 | Level-2 扭矩监控常保留一份与主控相似的镜像表(改主控不改它 → 监控报错) |

## 1. 与其它"找表"技能划清(避免重复)

| 技能 | 输入 | 做什么 | 与本技能区别 |
|---|---|---|---|
| **parallel-map-finder(本)** | **有种子**(已知一张表)| 同 bin 内找**内容相似**的副本 | "手里已有一根针,找出所有一样的针" |
| `blind-map-finder` | 无种子 | 从零盲扫整片 flash 找 map 候选 | "大海捞针找所有针" |
| `damos-a2l-parser` | A2L/DAMOS | 按命名/地址列定义 | 命名相似≠内容相似,靠命名会漏成组副本 |
| `ecu-calib-finder` | 多个文件 | 跨**文件**找兼容标定 | 那是文件间;本技能是同一 bin 内 |

## 2. 两条正交的相似线(输出分级依据)

- **near-identical(数值接近,归一化 L1 距离 < 0.02)**:几乎逐字节相同 → **真复制 / 监控镜像**。
  改主控时**必须一起改**;监控镜像不同步会直接报错。**高可信。**
- **shape-variant(形状相同,Pearson 相关 ≥ 0.98,但数值有缩放/偏移)**:同一形状的**模式/档位/变体
  副本**。相关性抓形状,L1 反映缩放/偏移幅度。需再判功能。
- **similar-shape(弱,相关 ≥ 0.90)**:仅供人工排查。

## 3. 用法

```bash
# 种子:已知一张表的地址+维度(从同一 bin 读种子字节)
python scripts/parallel_map_scan.py --bin ECU.bin --seed-addr 0x1AF00 --rows 16 --cols 16 \
        --dtype u16 --endian le --mode identical

# 找模式/档位变体(缩放/偏移):用 shape
python scripts/parallel_map_scan.py --bin ECU.bin --seed-addr 0x1AF00 --rows 16 --cols 16 --mode shape

# 含镜像基址、导 JSON 给 stage1 一起改
python scripts/parallel_map_scan.py --bin ECU.bin --seed-addr 0x801AF00 --base 0x800000 \
        --rows 12 --cols 16 --json siblings.json
python scripts/parallel_map_scan.py --selftest
```

## 4. 使用纪律(真实文件验证得出,必须遵守)

1. **种子选择决定精度**:
   - 种子是**有辨识度的 2D 表**(点火/喷油/boost 等,有独特二维结构)→ shape 模式精准。
   - 种子是**普通单调轴**(纯递增 ramp)→ shape 模式会**过匹配**(所有单调 ramp 都高相关)。
     此时用 **identical 模式**(靠 L1 距离筛真副本),或看 L1:L1 极小的才是真副本,L1 大而相关高的
     只是"别的 ramp"。
2. **输出是候选 + 分级,不是结论**。near-identical 高可信(直接一起改/核对镜像);shape-variant 必须
   再经 `ecu-map-analysis` 判功能、`ghidra-tricore-disasm` 看谁读它,确认是同族再动。
3. **维度/dtype/字节序给错 → 全错**。种子维度不确定时先用 `ecu-map-analysis`/`blind-map-finder` 定维度。
4. **找齐的目的是"一起改 + 核对监控镜像同步"**:确认同族后纳入 `stage1-calibration-matrix` 修改矩阵,
   逐个核对(尤其 Level-2 监控镜像),避免漏改一环(doctrine 联动清单)。
5. 字节读写交 bin 引擎;写盘后 checksum 一律交 `checksum-crc-engine`。

## 5. 与其他技能的关系

```
parallel-map-finder
├── 种子维度不确定 → ecu-map-analysis / blind-map-finder 先定维度
├── shape-variant 功能判定 → ecu-map-analysis
├── 谁读该副本(是否监控镜像) → ghidra-tricore-disasm
├── 同族副本纳入一起改的矩阵 → stage1-calibration-matrix
├── 字节读写 → bin 引擎;写后 checksum → checksum-crc-engine
├── 有 A2L/DAMOS 命名成组 → damos-a2l-parser(命名) 与本技能(内容)互补
└── 所有结论经过 ecu-calibration-doctrine
```

## 6. 输出纪律

- 分级呈现:near-identical(必须一起改/核对镜像)/ shape-variant(模式档位副本,判功能后改)/ weak。
- 每组给"下一步动作":一起改 / 核对监控镜像同步 / 送 ecu-map-analysis 判功能。
- 明示种子选择对精度的影响(轴 vs 2D 表),不把 shape 模式的过匹配当成结论。
- 具体功能名不由本技能出(交 ecu-map-analysis / oem-strategy-library);本技能出**内容相似性证据**。

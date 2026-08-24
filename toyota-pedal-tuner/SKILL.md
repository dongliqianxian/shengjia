---
name: toyota-pedal-tuner
description: >-
  定位、解码、导出并重调丰田/雷克萨斯 DENSO ECU 里的加速踏板特性（电子油门）曲线，直接输出改好的 bin。处理 1×16 uint16 小端、raw/256=% 的曲线，自动识别共享踏板位置轴与各驾驶模式/车速档的输出曲线。提供【柔和 / 正常 / 激进】三档一键预设，两种变换可选：power 幂变换（力气压在起步段，消死区，默认）与 taper 线性锥度（全程均匀变轻）。当用户说"调踏板""油门轻盈""油门肉/憋""起步不跟脚""踏板曲线""油门 MAP""throttle response""lazy pedal""给我调个柔和/正常/激进的踏板""按幂变换改踏板""我发你 bin 你把踏板调了"时，必须调用本技能。即使用户只发一个丰田/雷克萨斯 bin 说"油门太肉"，也要调用。只改感受不改峰值功率（WOT 恒 100%）。不重算校验和——刷写前必须走 ECM Titanium Auto-Fix。搭配 denso-bin-engine 做通用 DENSO map 工作。
---

# Toyota/Lexus DENSO Pedal Tuner

Specialist for the accelerator-pedal characteristic ("pedal map" / 油门踏板特性) on Toyota/Lexus
DENSO ECUs. Decodes the curves, exports them to engineering %, and writes a "light throttle"
calibration using a verified, safe transform.

## What these maps are (the verified model)

- **Type**: drive-by-wire pedal characteristic — translates *pedal opening %* into
  *requested throttle/torque %*. It changes **feel/response only, not peak power** (WOT stays 100%).
- **Encoding**: `uint16` little-endian, 1 row × 16 cols, `raw / 256 = %`. Full scale `25600 = 0x6400 = 100%`.
- **Layout**: 一个块里首尾相接的重复单元 `[X轴 16w][输出 16w][u32 len = 16]`，stride `0x44`（68 字节）。
  - 单元开头那条是**共享踏板位置轴** —— **不要动它**。
  - 单元 `+0x20` 那条是**输出曲线**（请求的节气门/扭矩 %），每个驾驶模式（ECO/Normal/Sport）
    和/或车速档一条。这才是要调的。
  - **末尾那个 `u32 == 16` 的长度字段是判别器。** 只靠"曲线形态"会吃假阳性：真块之后紧邻的字节
    可能恰好长得像 轴+曲线（Prado GRJ152 上就实测到一处，长度字段是 7995422 的垃圾值）。
    脚本用它把假阳性归零，同时不必靠"哪条曲线出现次数最多"去猜轴。
- WinOLS 自动识别只给泛名（如 `Denso 16`），不告诉你功能 —— 本技能告诉你。

## 两种变换 · 三档预设

共同保证：**0 恒为 0 · WOT 恒为满量程 · 单调性保持 · 永不越过满量程**。差别在**力气落在哪**：

```
power  new = FULL * (old/FULL)^p          (p < 1，默认)
       提升 = (x/FULL)^(p-1) − 1
       力气全压在行程最底部 —— 吃掉死区，"油门开始起作用得更早"

taper  new = old * (1 + K*(1 − old/FULL))
       提升 = K*(1 − x/FULL)
       全程均匀、线性衰减到 WOT —— "整个踏板都变轻了"
```

同一条曲线上的差别：

| | 原值 1% | 25% | 50% | WOT |
|---|---|---|---|---|
| `power p=0.85` | **+100%** | +23% | +11% | 0% |
| `taper K=0.25` | **+25%** | +19% | +12% | 0% |

**怎么选**：抱怨是"起步肉 / 死区大 / 不跟脚"（最常见）→ `power`；
抱怨是"整体再轻一点"而底部已经够灵 → `taper`。

### 三档预设

| 档位 | `power` p | `taper` K | 手感 |
|---|---|---|---|
| **柔和** soft | 0.90 | 0.15 | 明显变轻但很线性，新手/家用不易窜 |
| **正常** normal（默认）| **0.85** | 0.25 | 城市最常用的 10–30% 区间提升 +23%~+37%，跟脚不神经质 |
| **激进** aggressive | 0.78 | 0.38 | 起步段大幅前移，接近"一点就走" |

真实丰田曲线上的三档梯度（%）：

```
原始   0.00  1.00  1.50  2.10  2.50  3.00  3.50  4.30  6.00  7.50 12.00 25.00 49.00 73.00 88.00 100.00
柔和   0.00  1.59  2.28  3.09  3.62  4.26  4.89  5.89  7.95  9.72 14.84 28.72 52.62 75.33 89.13 100.00
正常   0.00  2.00  2.82  3.75  4.35  5.08  5.79  6.89  9.15 11.06 16.49 30.78 54.54 76.53 89.70 100.00
激进   0.00  2.75  3.78  4.91  5.63  6.49  7.32  8.59 11.14 13.26 19.13 33.91 57.33 78.23 90.51 100.00
```

末点全是 100.00 —— **幂变换在 x=1 处数学恒等，不是靠钳位**，峰值功率一点不动。

## Usage

```bash
# 一键三档（默认 power 幂变换）
python scripts/pedal_tuner.py file.bin --mode 柔和
python scripts/pedal_tuner.py file.bin --mode 正常      # 默认，可省略
python scripts/pedal_tuner.py file.bin --mode 激进

# 换成线性锥度
python scripts/pedal_tuner.py file.bin --mode 正常 --transform taper

# 自定义强度（覆盖预设）
python scripts/pedal_tuner.py file.bin --p 0.82         # 幂指数
python scripts/pedal_tuner.py file.bin --transform taper --k 0.30

# 先看不写
python scripts/pedal_tuner.py file.bin --dry-run

# 自动检测不对时手动指定
python scripts/pedal_tuner.py file.bin --full 25600 --factor 256
python scripts/pedal_tuner.py file.bin --data-addrs 0x53752,0x53796 --axis-addr 0x53732
```

档位也接受英文 `soft/normal/aggressive`，以及 `温和/标准/运动`。

输出：`<name>_Pedal_<档位>.bin` + `<name>_pedal_<档位>.md`（原值 / 新值 / 逐点提升%）。
写文件后脚本会自动跑三项自检（单调 / WOT=满量程 / 起点=0），**任一项 ✘ 就不要刷**。

## Workflow

1. Run with `--dry-run` first. Confirm: full-scale = 25600, a sensible number of output curves found,
   and an X-axis that repeats. Eyeball the exported table — outputs must read 0→100% monotonic.
2. If auto-detection is off (wrong block, axis mistaken for data), pass `--data-addrs` / `--axis-addr`
   explicitly. The denso-bin-engine skill + a WinOLS screenshot help confirm addresses.
3. Run without `--dry-run` to write the tuned bin.
4. **Checksum**: this skill never recomputes the DENSO checksum. The bin is invalid for flashing
   until Auto-Fix in ECM Titanium (Tools → Checksum → Auto Fix). Keep the original as recovery base.

## Verifying before trusting

- The exported `orig%` rows must be monotonic 0→100. If a curve shows garbage, the encoding/full is
  wrong — set `--full`/`--factor`.
- The detected X-axis (pedal %) should be monotonic and end near 100. For the ES200 it is
  `[0, 2.4, 5.5, 10, 20, 30, 36.5, 39.5, 42, 45, 50, 57, 68.6, 82.9, 100, 100]`.
- Behaviour note: lifting front/mid output makes the car feel more responsive (not more powerful),
  may slightly raise part-throttle fuel use and reduce low-speed smoothness if K is too high.
  It does not touch torque model / spark / lambda / TCU, so it won't cause knock or torque-protection.

## Files

- `scripts/pedal_tuner.py` — the tool (analyse / export / tune). Pure Python 3, no deps.
- `references/es200_findings.md` — the reverse-engineering log for the ES200 89663-33L93 (OLS layout,
  embedded-bin base, block addresses, axis, the 4 stock curve shapes).

## Run the self-test
```bash
python scripts/test_pedal_tuner.py
```

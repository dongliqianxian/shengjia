---
name: checksum-crc-engine
description: >-
  Compute, verify, and repair data-level checksums or CRCs in ECU and TCU binaries. Provides CRC-16, CRC-32, byte sums, word sums, XOR, ones-complement, twos-complement checks, and a ChecksumBlock abstraction for defined ranges. Use whenever a bin write needs checksum checking, locating, or repair: 重算校验和, verify CRC, sum16, sum32, checksum stored address, no-start after edit. Includes the stop rule that many modern ECUs such as EDC17, MED17, MEVD17, MD1, MG1 use multi-layer checksums or signatures and must be finished by WinOLS, ECM Titanium Auto-Fix, or the flash tool. Pair with denso-bin-engine, bosch-conti-bin-engine, and multi-vendor-ecu-engine.
---

# Checksum / CRC Engine

The doctrine makes checksum the **delivery gate**: a written bin with a stale checksum is *not done*
and commonly causes a no-start or limp. The bin engines deliberately never touch the checksum — they
hand off here. This skill computes/verifies/repairs the **data-level** checksums you can legitimately
recompute, and is **loud and honest** about the ones you cannot.

Run under `ecu-calibration-doctrine` (checksum = the §2 step ⑬ pre-gate, §5 final checklist item).

## Read this first — the brick-avoidance reality
ECU integrity protection is layered. Know which layer you're touching:

| Layer | Recomputable by this skill? | What to do |
|-------|-----------------------------|-----------|
| **Map/data-block checksum** (simple sum / CRC over a cal region) | ✅ yes, if you know range + algo + store location | use `ChecksumBlock.repair()` |
| **Multi-region cal checksums** (Bosch EDC15/16, ME7 — several sum/CRC blocks) | ⚠ sometimes, error-prone by hand | strongly prefer **WinOLS checksum plugin** / ECM Titanium |
| **Program/flash CRC + monotonic counters** (EDC17/MED17/MEVD17/MD1/MG1) | ❌ no | **WinOLS plugin / tool auto-checksum only** |
| **Bootloader RSA / ECC signature, secure boot** (MD1/MG1, newer Conti) | ❌ never (cryptographic) | the OEM/flash tool handles it; you cannot forge it |

> Rule: if you are not certain the checksum is a plain data-block sum/CRC, **say so and route to a
> dedicated checksum tool**. Never tell a user a file is flash-ready on a guessed algorithm. A wrong
> "fixed" checksum is worse than an obviously-wrong one — it can pass a weak check and brick on a
> strong one.

## What this engine does well
A configurable, tested CRC + sum engine for the cases you CAN own:
- Verifying whether a region's stored checksum currently matches (did my edit break it / is the
  algorithm what I think?).
- Repairing a single data-block checksum where you know the covered range, the store offset/size,
  the algorithm, and the mode.
- Brute-searching a small space of standard CRC presets to identify an unknown 16/32-bit checksum
  against a known-good file (when you have ORI + a tool-fixed MOD to compare).

## Core usage
```bash
python scripts/crc.py --selftest            # verifies against standard CRC/sum check vectors
```
```python
from crc import CRC_PRESETS, crc, sum_checksum, ChecksumBlock, load, save

buf = load("file.bin")

# 1) Compute a CRC over a region with a named preset:
val = crc("CRC-32", buf[0x10000:0x1FFFC])           # standard CRC-32 (poly 04C11DB7, reflected)

# 2) Define and verify/repair a data-block checksum:
blk = ChecksumBlock(
    data_ranges=[(0x10000, 0x1FFFC)],   # bytes the checksum covers (end-exclusive)
    store_offset=0x1FFFC,               # where the 4-byte checksum is stored
    store_size=4, endian="little",
    algo="sum32",                       # sum32 | sum16 | xor8 | crc:CRC-32 | crc:CRC-16/CCITT-FALSE
    mode="store_value",                 # store_value | store_negsum (two's-comp so total==0)
)
ok, found, stored = blk.verify(buf)     # is the stored value currently consistent?
if not ok:
    blk.repair(buf)                     # write the correct checksum into store_offset
    save(buf, "file.bin", suffix="_cksum")

# 3) Identify an unknown 16/32-bit CRC against a known-good (tool-fixed) reference:
from crc import identify_crc
print(identify_crc(region_bytes, expected_value))   # lists matching presets, or [] if none standard
```

## Algorithms included
- **CRC presets** (in `crc.py`, all verified against the standard "123456789" check value):
  `CRC-32` (0xCBF43926), `CRC-32/MPEG-2` (0x0376E6E7), `CRC-16/CCITT-FALSE` (0x29B1),
  `CRC-16/MODBUS` (0x4B37), `CRC-16/ARC` (0xBB3D), `CRC-16/XMODEM` (0x31C3). Add more by giving
  `(width, poly, init, refin, refout, xorout)` — the generic engine handles reflection.
- **Sums**: `sum8`, `sum16` (le/be), `sum32` (le/be), `xor8`.
- **Modes**: `store_value` (store the computed sum/CRC), `store_negsum` (store two's-complement so the
  region total reads 0), `store_complement` (one's-complement).

## Known platform layouts — check the library FIRST
Before deriving anything from scratch, consult `references/known_checksum_layouts.md`: a growing
per-platform table (Bosch / Conti / DENSO / Hitachi / GM ...) of checksum layer, algorithm/structure,
and the correct repair path, each entry marked ✅实战确认 / 🔧工具路径确认 / 📖公开资料. Treat 📖
entries as direction only — verify per the workflow below before relying on them. Every time a
platform is confirmed in practice, append it there with full `ChecksumBlock(...)` parameters.

## 逐镜像检测,不按控制器名查固定表(doctrine §13 逆向纪律,实证)

以下全部来自两个真实实现,**不是推测**。

### 1. 校验区范围必须**逐镜像**检测

> **实证(5eat)**:同一个 TCU 家族里两种约定并存 —— `0x60000` 固定长度
> (含 512KB 镜像只用 384KB、尾部留空的情况)与"整个文件"。
> 原文:**"假定任何一种都会在一半的家族上产生一个自信的错误答案。"**

**做法**:候选范围**全算一遍**,**哪个能复现 ROM 里已经存着的值,哪个就是这个镜像的约定**。
按控制器名去查一张固定表 = 在一半的样本上自信地答错。

### 2. 可能存在**第二个独立**校验和(balance 类)

同一家族每个镜像还带一个 `0x008020` 的 "balance",且**分 16 位与 32 位两种变体**:

| 变体 | 判据 | balance 位置 |
|---|---|---|
| **32 位** | 全和须等于 `0x5AA5A55A` | `0x008020` 整个 32 位字 |
| **16 位** | 只检低半,对 `0x5AA5` | `0x008022` 半字,`0x008020` 保持零 |

> **16 位那种最容易漏** —— 漏了就是 11 个 ROM 里 8 个改完过不了自检。
> 判别是**结构性**的(16 位镜像在 `0x008020` 半字处是零),改完仍然成立。

**修正顺序(一遍收敛)**:balance 在附加校验和覆盖的区域内,而附加校验和的槽位在 balance 区域外
→ **先修内层 balance,后修外层附加校验和**。反过来会互相推翻。

### 3. Bosch MED17 / EDC17 块发现锚点

> **实证(medc17-checksum-tool)**:每个块的第一个 32 字节校验结构固定在 `block_start + 0x34`,
> 其中 `+0x0C` 是种子 `0xFADECAFE`,`+0x10` 是期望值 `0xCAFEAFFE`。

**1/2⁶⁴ 的锚点 —— 直接搜这对魔数定位所有块,不要猜。**

- **块类型字节在 `block_start + 15`**:`0x80` = 应用(缓存 flash 视图);`0xA0` = 数据/标定(非缓存)
- **TriCore 地址别名**:同一物理 flash 映射成缓存 `0x8xxxxxxx` 与非缓存 `0xAxxxxxxx` 两个视图。
  **转文件偏移前必须剥掉段 nibble,否则 `0xA0` 块会偏出 `0x20000000`。**
- **未编程分区**把校验/调整字段留成重复填充字节(`0xFF`,某些 Bosch 镜像是 `0xAF`)
  → 没有真东西要保护,**修正必须跳过**
- CRC32 可用 GF(2) 矩阵代数瞬时求解:往一个擦除态 dword(`0xFF`/`0x00`/Bosch pad `0xC3`)写补偿值
- **多变体 ECU**:有多个标定变体的 ECU 维护每变体参数地址表,
  监控校验和是对**当前激活变体**算的 —— **只修第一个,其余全错**

> *依据 `ecu-calibration-doctrine/references/re-discipline.md` 第七节「校验和」7.1/7.2/7.3；实证来源 TomFLV/5eat 与 ConnorHowell/medc17-checksum-tool。*

## Procedure for an unknown checksum (the honest workflow)
1. If you have **ORI + a tool-fixed MOD**, diff them: the bytes that changed *besides your edits* are
   the checksum location(s). Read width/endianness from that.
2. Try `identify_crc()` and the sum variants over plausible ranges against the tool-fixed value.
3. If a standard preset/sum reproduces the tool's value → you can repair that block yourself going
   forward. If nothing matches → it's proprietary/multi-layer/signed → **use the WinOLS plugin or the
   flash tool's auto-checksum**, and say so.
4. Never ship a "repaired" file whose algorithm you only *assumed*. Verify against a known-good first.

## Output discipline
- State exactly which layer you addressed and which you did NOT.
- If any non-recomputable layer (program CRC, RSA/secure boot) exists, the final report must say the
  file is **not flash-ready until the dedicated tool fixes it** — same loudness as the bin engines'
  checksum reminder.
- This is the executor every bin engine hands off to; it does not edit maps itself.

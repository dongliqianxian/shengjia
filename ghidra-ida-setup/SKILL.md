---
name: ghidra-ida-setup
description: >-
  Set up Ghidra or IDA correctly for ECU firmware before map finding or logic analysis. Covers TriCore processor modules, IDA TriCore support, MCU architecture selection, loaders, image base, flash and RAM memory maps, reset vectors, interrupt vectors, endianness, and choosing IDA vs Ghidra. Use for loading TC1797, AURIX, EDC17, MED17, SH7055, SH7058, V850, RH850, PowerPC or MPC5xx dumps, fixing garbage disassembly, unresolved pointers, wrong base address, Ghidra 加载 TriCore, 内存映射, 基址, IDA TriCore 插件. Analysis workflows after setup route to ghidra-tricore-disasm.
---

# Ghidra / IDA Setup for ECU Firmware (step zero)

Before you find a single map, the tool has to be **loading the binary correctly**: right processor,
right endianness, right image base, right flash/RAM split. Get any of these wrong and every xref,
pointer, and function boundary is fiction. This skill is that setup; the analysis itself is
`ghidra-tricore-disasm`.

Run under `ecu-calibration-doctrine` (this serves golden-order step ① "收集 ECU 信息" — knowing the
MCU/memory map is part of knowing the ECU).

## 1. Identify the MCU first (everything depends on it)
From the chip marking, the flash tool's ECU ID, or the A2L. The MCU sets **architecture +
endianness** (shared with `multi-vendor-ecu-engine`):

| MCU family | Arch in Ghidra/IDA | Endian | Typical ECU |
|------------|--------------------|--------|-------------|
| Infineon **TC1766/67/96/97** | TriCore (v1.6) | little | EDC17, MED17, MEVD17, SIMOS, DCM, MT |
| Infineon **AURIX TC27x/29x/3xx** | TriCore (v1.6.1/1.6.2) | little | MD1, MG1, newer SIMOS/Conti |
| Infineon **C167 / ST10** | C166/ST10 (IDA; limited in Ghidra) | little | ME7, EDC15, older Marelli IAW |
| Renesas **SH7055 / SH7058** | SuperH SH-2/SH-2A | **BIG** | Nissan/Subaru/Mitsubishi (Hitachi) |
| Renesas **V850 / RH850** | V850 / RH850 | usually little | newer Renesas-based |
| Freescale/NXP **MPC5xx / 55xx / 57xx** | PowerPC (VLE for 55xx/57xx) | **BIG** | Visteon Ford PCM, some Marelli, diesel |
| Freescale **HC11/HC12/68k** | HCS12 / 68k | **BIG** | legacy Visteon/Ford, older TCU |

> Endianness is the #1 cause of "garbage disassembly". TriCore/C167 = LE; SH and PowerPC = BE.

## 2. Processor modules
- **Ghidra**: ships with **TriCore**, **SuperH**, **V850**, **PowerPC** processors out of the box
  (modern Ghidra ≥10.x). Pick TriCore for Infineon EDC17/MED17/AURIX. For C167/ST10, Ghidra support
  is weak → prefer IDA.
- **IDA**: TriCore processor is a Hex-Rays add-on (or third-party module); SuperH, PowerPC, V850,
  C166/ST10 are supported. IDA's TriCore + decompiler is often nicer for AURIX; Ghidra is free and
  good enough for most map-finding.
- Community **loaders/scripts** exist for specific platforms (e.g. MED9.1 Ghidra scripts that define
  the memory map and mark maps automatically) — use them to skip manual setup when one matches.

## 3. Image base & memory map (the part people get wrong)
A raw flash dump is just bytes at offset 0 — you must tell the tool **where flash lives in the
address space**, or absolute pointers won't resolve to your data.

Typical Infineon TriCore layout (confirm per device datasheet):
- **Program flash (PFLASH)** is mapped high, commonly around `0x8000_0000` (cached) / `0xA000_0000`
  (non-cached) on TC17xx, and `0x8000_0000`+ on AURIX. The bench dump's offset 0 = the flash base.
- **Data flash (DFLASH)** — calibration/EEPROM area — is a separate region; non-volatile (a power
  cycle does NOT clear learned values — doctrine §5 联动风险清单 note on knock learning).
- **RAM** is mapped elsewhere (e.g. `0xD000_0000` scratch on TriCore). Globals the limiter logic
  reads usually live in RAM.

Set up in Ghidra:
1. Load as **Raw Binary**, language = **TriCore : LE : 32 : default** (or the SH/PPC equivalent).
2. Set the **base address** at import (or **Memory Map** → set the block start to the flash base).
3. Add separate memory blocks for DFLASH and RAM at their addresses so pointers resolve.
4. **Analyze** (auto-analysis), then find the **reset vector** to confirm the base is right: from
   reset, the first real code should disassemble cleanly into recognizable init. If it's nonsense,
   the base or endianness is wrong — fix before doing anything else.

## 4. Confirm the load is correct (don't proceed on a bad base)
- The **vector/reset** region disassembles into sane init code.
- String search (`find_ascii` in the bin engines, or Ghidra's string search) finds the part number /
  ASW version where you expect — confirms the file is intact and the base aligns.
- Function auto-analysis yields a reasonable function count, not a sea of undefined bytes.
- Absolute pointers in code land **inside** your defined flash/RAM blocks (not off in unmapped space).

Only when these pass do you move to `ghidra-tricore-disasm` to find maps / read limiter logic.

## 5. Global base registers — the second base address people miss

Getting the **image base** right is only half of "loading correctly". Many ECUs never build a full
address per access. The compiler parks a base constant in a register during startup and then reaches
all calibration/RAM with **`base register + 16-bit displacement`**. The displacement is *not* an
address, so:

> **Symptom:** you are sure the address is right, but Ghidra reports `refs=0` for it, searching for
> `movhi`/`movea` address-construction finds nothing, and searching for the 32-bit address literal
> finds nothing. Nothing is broken — you just haven't found the base register yet.

**Procedure (do this once per image, right after §4):**
1. Follow the **reset vector into real startup code**. Do not linear-sweep data regions — data
   disassembled as code invents plausible-looking "writes to gp/tp"; nonsense mnemonics
   (`fetrap`, `satsub ep,tp`, stray `sld` chains) are the tell that you are in data.
2. Find the instruction that loads a **constant into the global base register**. On V850/RH850 it is
   `mov <imm32>, gp|tp|ep`, typically just after the interrupt-priority init.
3. **Absolute = base + signed displacement.** Recompute every `ld/st <disp>[base]` with it.
4. **Tell the two bases apart by access type:** a base only ever `ld`-ed points at **flash
   calibration**; a base that is also `st`-ed to points at **RAM**. Fastest reliable discriminator.

| Core | Global base registers | Notes |
|---|---|---|
| **V850 / RH850** | `gp`(r4), `tp`(r5), `ep`(r30) | Measured on a Toyota DENSO V850: `tp` = flash cal, `gp` = RAM |
| **TriCore** | `a0`/`a1` (small data), `a8`/`a9` (application) | `ld.w d0,[a0]off` — `off` is not an address |
| **SH-2 / SH-2A** | `GBR`, plus PC-relative literal pools | SH leans on PC-relative constant pools |
| **PowerPC** | `r2` (TOC/SDA2), `r13` (SDA) | Small-data anchors |

Worked example (Toyota FJ 1GR-FE, DENSO V850, 736 KB): startup at `0x0001A4A4` does
`mov 0x3FF4000,gp` then `mov 0xE010,tp`. Before finding this, Ghidra full auto-analysis returned
`refs=0` for every calibration target; after it, `0x00FA20` resolved to **11 read sites**. The
locate-and-prove workflow that uses this lives in `physical-constant-locator`.

## 6. IDA vs Ghidra — quick choice
- **Ghidra**: free, solid TriCore/SH/PPC, scriptable in Python/Jython, good for map-finding and the
  WinOLS↔Ghidra JSON bridge. Default choice.
- **IDA + decompiler**: paid, often cleaner AURIX/TriCore decompilation and faster navigation; worth
  it for deep protection-logic work. C166/ST10 (old Bosch/Marelli) is better in IDA.

## Resources & community scripts
- Ghidra (NSA) — free SRE framework with TriCore/SH/PPC/V850 processors.
- Platform script repos (e.g. MED9.1 Ghidra scripts) that auto-define memory maps and mark maps.
- ECU-RE write-ups pairing Ghidra + WinOLS (load bin in Ghidra, understand the code, switch to WinOLS
  to edit). Keep the JSON bridge in `ghidra-tricore-disasm` to sync map lists.

## Boundaries
- Setup only — find/analyze is `ghidra-tricore-disasm`; decode/edit is the bin engines; checksum is
  `checksum-crc-engine`.
- Always verify the base/endian via the reset vector + strings before trusting any address. A wrong
  base produces confident-looking nonsense.
- Exact base addresses vary by device — confirm against the Infineon/Renesas/NXP datasheet for the
  specific part, not a generic value.

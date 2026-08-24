---
name: dtc-locator
description: >
  在 ECU/TCU 二进制里定位一个 DTC(故障码)的地址,以【无 A2L/DAMOS】的字节级定位为主。
  当用户给出一个故障码要在 bin 里找位置——"P0240 在这个文件里哪个地址"、"帮我找这个故障码
  的地址"、"找 DTC 表"、"这个码存在 bin 哪里"、"故障码地址定位"、"没有 A2L 怎么找 DTC"、
  "P/C/B/U 码怎么编码"、"UDS 3 字节故障码"、"这段是不是 DTC 表"时,必须调用本技能。
  即使用户只发一个码(如 P0240)加一个 bin,也要调用。
  本技能是【诊断/定位工具】:把 P 码按 SAE J2012/ISO 15031-6 编码成 2 字节,双字节序搜索,
  自动探测 DTC 配置表(以命中点为中心的升序 run 判定可信度),输出地址+可信度分级+记录字节。
  它不解析 A2L(有 A2L 用 damos-a2l-parser 的 DFC 标签更准)、不做换算(winols-scaling-converter)、
  不重算 checksum(checksum-crc-engine)。运行于 ecu-calibration-doctrine 之下。
  边界:本技能只做故障码的定位与理解,服务于诊断、修复与标定验证。
---

# DTC 定位器(无 A2L 字节级为主)

给一个故障码 + 一个 bin,找出它在文件里的地址。核心是纠正一个常见误解并按结构定位。

## 关键认知:DTC 不是字符串,是 2 字节编码值

`P0240` 在 bin 里**不是**以 ASCII "P0240" 存的,而是按 SAE J2012 / ISO 15031-6 编码成 2 字节:

- 字母占最高 2 bit:`P=00 / C=01 / B=10 / U=11`
- 其后是 4 个数字(第 1 位 0–3,占 bits13-12;其余为十六进制)
- 拼法:`value = (letter<<14) | int(后4位, 16)`

例:`P0240 → 0x0240`(小端存 `40 02`,大端 `02 40`)、`U0100 → 0xC100`、`P1234 → 0x1234`、
`C1234 → 0x5234`。现代 ECU 走 UDS(ISO 14229)时是 **3 字节** = 高 2 字节码 + 1 字节失败类型(FTB),
如 `0x0240` + `0x1C`。

## 无 A2L 如何定位(本技能重点)

`0x0240` 是极常见的 2 字节组合,单点搜索必然满屏误报。正解是**找结构 + 以命中点为中心判定**:

1. **编码 + 双字节序搜索**:把码编码成 2 字节,小端/大端都搜。**关键:DTC 码是协议值,
   UDS 常以 MSB-first(大端)存储,与平台数据端序无关**——所以即便是小端 TriCore(EDC17/MED17),
   诊断码也可能是大端存的。因此码搜索**默认双序**,profile 的端序只是数据层参考,不用来限制码搜索。
2. **2 字节 OBD/KWP vs 3 字节 UDS**:老件/KWP 存 2 字节码;现代 UDS(ISO 14229)存 3 字节
   `[高字节][低字节][FTB]`(MSB-first)。脚本对两者都探测——winning stride==3 判为 3 字节 UDS 记录,
   自动解出 FTB 并给完整码 `P0240-1C`。
3. **DTC 表的本质是"码按数值递增排序的定长记录数组"**:每条记录 = 码 + [FTB] + debounce/失败计数 +
   MIL/灯行为 + 状态掩码 + 冻结帧配置。真正的信号是"DTC 形值以固定 stride 升序重复"。
4. **可信度由"穿过命中点的升序 run"直接判定**(不是先全局找表再测归属——那会被同 stride 的
   别名列干扰)。以精确命中偏移为中心向两侧扩展升序 run:
   - run ≥ 12 条 → **HIGH**(稳在 DTC 表内)
   - run 6–11 条 → **MEDIUM**
   - 否则 → **LOW**(孤立命中,大概率巧合)
5. stride 自动探测(含 3 字节 UDS 的 stride=3 与 2 字节记录的偶数 stride),无需预知记录长度。
   run_through 取"最长升序 run"自动选真 stride:UDS 表在 stride=3 成最长 run,6 字节 OBD 表在
   stride=6 成最长 run。
6. **老件/厂内部编号**:不走 J2012 的原始 2 字节码用 `--raw 0x1234` 直接搜(绕过编码)。

## 架构:一个通用引擎,不是每个 ECU 一个脚本

编码层是全球统一标准,与 ECU 无关;DTC 码序是协议序(默认双序搜);只有表的记录结构(stride/
字段)因平台而异,而 stride 能自动探测。所以是**一个通用脚本 + 可选平台 profile**(profile 只给
平台**数据层**端序参考与字段解释提示,**不限制码搜索**)。**字节序跟 MCU 核走,不跟车标**
(`multi-vendor-ecu-engine` 原则#1):TriCore/AURIX=小端,Renesas SH/PowerPC=大端。

## 脚本

```bash
python scripts/dtc_locate.py --bin ECU.bin --code P0240             # 默认双序 + 自动找表(2字节&UDS 3字节)
python scripts/dtc_locate.py --bin ECU.bin --code P0240 --profile edc17   # 平台数据端序参考(不限码搜索)
python scripts/dtc_locate.py --bin ECU.bin --code P0240 --endian be # 显式只搜大端(需要时)
python scripts/dtc_locate.py --bin ECU.bin --raw 0x1234             # 老件/KWP 原始码直接搜(绕过 J2012)
python scripts/dtc_locate.py --bin ECU.bin --tables                 # 只列探测到的候选 DTC 表
python scripts/dtc_locate.py --code P0240 --encode-only             # 只算编码,不需要 bin
python scripts/dtc_locate.py --selftest
```

平台 profile(仅数据端序参考,码搜索恒双序):
- 小端核:`edc17` `md1`(Bosch 柴油)、`med17` `mevd17` `mg1`(Bosch 汽油)、`simos`(Conti/西门子)、
  `dcm`(Delphi 3.7/6.2/6.2AP/7.1AP)、`denso_rh850`(DENSO 新代 RH850)
- 大端核:`sh`(Renesas SH,日产/斯巴鲁/三菱/老 DENSO)、`ppc`(PowerPC,GM/福特/部分 Marelli)
- `auto`:数据端序不限(默认)

## 输出纪律

- 只有 **HIGH/MEDIUM** 命中才是 DTC 配置记录;LOW 是孤立命中,不下结论。
- 地址(偏移)是精确可靠的;`record_index` 相对探测到的 run 起点,可能因边缘噪声被吸收而略偏,
  仅作参考——以偏移为准。
- 字节搜命中的是"对外 OBD 码表"。OEM 内部常用 DFC/内部事件号,P 码经映射表转;要拿字段确切
  含义:有 A2L 用 `damos-a2l-parser`(`--filter <码>` 找 DFC 标签)交叉确认,或用
  `ghidra-tricore-disasm` 反汇编看引用该表的诊断例程。
- 换算交 `winols-scaling-converter`,写盘后 checksum 交 `checksum-crc-engine`。

## 边界(与 doctrine 一致)

本技能定位与理解故障码,服务于**诊断、修复、标定验证**。合法诊断、真部件故障定位、赛用/台架研究等场景照常支持。

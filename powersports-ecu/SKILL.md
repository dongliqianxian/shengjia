---
name: powersports-ecu
description: >
  动力运动(powersports)ECU 域技能——摩托车 / ATV-UTV / 船机(舷外机·水上摩托) / 雪地车 / 小型
  通机。这不是"某个供应商的技能",而是【一个和汽车不同的控制域】:很多车非扭矩结构(TPS 直控)、
  空气模型多为 alpha-N(ITB 无进气歧管)、按档位/按缸分表、副节气门、专属刷写生态。
  当用户提到摩托/机车/踏板/ATV/UTV/沙滩车/舷外机/水上摩托/雪地车 ECU,或点名 Keihin/Mikuni/
  Shindengen/Synerject,或问"这个摩托 ECU 怎么读/字节序/是什么控制器/按档位的燃油表/按缸修正/
  副节气门 STV 怎么全开/退 O2 闭环/quickshifter 断油断火/弹射起步/分缸喷/PAIR 二次空气/
  Woolich FTECU TuneECU Power Commander Bazzaz 怎么弄/摩托 alpha-N 怎么标"时,必须调用本技能。
  本技能是【域参考层】:给控制范式(扭矩结构 vs TPS 直控)、按档位/按缸/副节气门等摩托特有结构、
  各品牌 ECU 供应商与核、刷写生态、排放系统说明。
  字节读写交 bin 引擎(经 multi-vendor-ecu-engine 按核定字节序;Keihin/Honda SH=大端);
  空气模型数学(alpha-N/速度密度)交 air-fuel-model-builder;扭矩结构(仅电子节气门旗舰车)交
  torque-model-builder;O2 闭环交 closed-loop-control;按档位/按缸副本交 parallel-map-finder;
  checksum 交 checksum-crc-engine。运行于 ecu-calibration-doctrine 之下。
---

# 动力运动 ECU(Powersports:摩托 / ATV / 船机 / 雪地车)

## 0. 为什么这是"一个域"而不是"一个徽标"

doctrine 原则#1"PCM 不在乎车标"仍然成立——所以**不需要给 Keihin/Mikuni 各建一个技能**。但动力运动
是一个**控制现实与汽车显著不同的域**,这些差异不是字节序问题,是"这台机器到底怎么被控制":

| 维度 | 汽车(乘用)默认 | 动力运动的现实 |
|---|---|---|
| 空气模型 | MAF / 速度密度 | 多为 **alpha-N**(ITB 独立节气门,无进气歧管稳压腔/无可用 MAP);新旗舰车也有速度密度 |
| 控制范式 | 扭矩基础结构 | **很多车不是扭矩结构**:TPS→燃油/点火表直控,无逆扭矩模型;仅电子节气门旗舰车才有扭矩结构 |
| 分表维度 | 按工况 | **按档位**(ECU 读挡位,各挡不同燃油/点火/节气门表)、**按缸**(直列多缸独立修正) |
| 执行器特殊件 | 节气门 | **副节气门**(STV/SUB,直四跑车顺滑用)、**分级喷射**(高转副喷油嘴) |
| 刷写生态 | WinOLS/KESS/OBD | **专属**:Woolich/FTECU/TuneECU/Power Commander/Bazzaz(多为 piggyback 而非全刷) |

## 1. 控制范式:先分清"扭矩结构 vs TPS 直控"(动手前第一判断)

- **TPS 直控(多数中低端 + 老车)**:节气门位置(alpha)× RPM → 直接查燃油脉宽表、点火表。没有逆/正
  扭矩模型,没有"扭矩请求"。改动直接改这些表——**别套用汽车的"抬扭要改整条路径"**,这里没有那条路径。
- **扭矩结构(电子节气门旗舰:BMW S1000RR、Ducati V4、Aprilia RSV4、Kawasaki H2、大排 Honda/KTM 等)**:
  有电子节气门 + 扭矩请求 + 牵引/防翘头/弹射控制 → 这时才回到 `torque-model-builder` 的整条路径逻辑。
- **判据**:有无电子节气门(ride-by-wire)?有 → 大概率扭矩结构 + 驾驶模式/TC/WC;无(拉线节气门)→
  TPS 直控。

## 2. 空气模型:多为 alpha-N —— 交 air-fuel-model-builder

ITB(每缸独立节气门,摩托常态)在部分节气门下歧管压力信号无用 → 用 **alpha-N**(节气门角 × RPM →
空气量)。air-fuel-model-builder 已有 alpha-N 分支,**数学与标定方法走它**,本技能只提示"这是 alpha-N
域"。船机/部分新车用速度密度,同样交它。doctrine 空气优先在此照旧:alpha-N 最不鲁棒(原书"last resort"),
标定尤其要交叉验证。

## 3. 摩托特有结构(标定要认得,汽车技能不覆盖)

- **按档位表(gear-dependent)**:ECU 读挡位传感器,各挡有独立燃油/点火/节气门开度上限表(低挡限扭防
  抬头、各挡不同响应)。改一挡漏其它挡 → 各挡行为不一致。**这些就是平行地图 → 用 `parallel-map-finder`
  以一挡为种子找齐所有挡副本。**
- **按缸修正(per-cylinder)**:直列多缸常有逐缸燃油/点火微调表 → 同样是平行副本组。
- **副节气门(STV/SUB throttle,YCC-T/SDTV 等)**:ECU 控制第二片蝶阀限制低挡/低转进气以求顺滑。
  调教常"把 STV 开度表设满(100%)"解开限制——**属动力标定,合法**;注意与主节气门标定的协调。
- **分级/副喷油嘴**:高转上方 showerhead 副喷嘴介入,喷射是分级的,燃油模型要算两组喷嘴。
- **quickshifter(快排)**:升挡瞬间**断火/断喷**若干 ms 卸载狗牙;kill time 随挡位/转速/扭矩标定。
- **限制器**:**逐挡断油转速**、弹射起步(launch)、pit lane 限速、(RBW 车)牵引/防翘头分级。

## 4. 各品牌 ECU 供应商与核(典型,须以芯片/字符串核对)

> 置信度:以下是**典型对应**,不是每台都如此。字节序**必须**由 MCU 核经 `multi-vendor-ecu-engine` 定,
> 品牌只是先验。

| 品牌 | 常见 ECU 供应商 | 核/字节序倾向 |
|---|---|---|
| Honda | **Keihin** | 旧 68HC=大端;K/L 世代 Renesas SH7058=**大端**;最新 RH850=小端 |
| Yamaha | Denso / Mitsubishi | 视世代;Woolich 支持面广 |
| Suzuki | Denso / Mitsubishi | —— |
| Kawasaki | **Keihin** / Mitsubishi / Denso | —— |
| Ducati | Magneti Marelli(旧)/ **Bosch**(新 RBW)| Marelli 注意核(见 multi-vendor);Bosch=小端 |
| Aprilia/Moto Guzzi | Magneti Marelli | —— |
| KTM/Husqvarna | Keihin(旧)/ **Bosch**(新 EURO4/5 EFI)| Bosch=小端 |
| BMW Motorrad | BMS-x / **Bosch** | 小端;TuneECU/专用 |
| Triumph | Keihin / Sagem / 后期 Continental | **TuneECU** 友好(OBD) |
| Harley-Davidson | **Delphi**(现 PHINIA)/ 部分 Marelli | HP Tuners / TTS |
| Polaris/BRP(Sea-Doo/Ski-Doo)| Bosch / Continental / **Synerject** | 船机/雪地/UTV 常见 |
| 舷外机(Merc/雅马哈/铃木/本田)| 多样,常**速度密度** | 逐一识别 |

## 5. 刷写生态(和汽车完全不同)

- **全刷(bench/OBD)**:Woolich Racing(Yamaha/Kawasaki/Suzuki/Honda,常 bench)、FTECU/Flash Tune、
  TuneECU(Triumph/KTM/Aprilia/Benelli,OBD)、HP Tuners(Harley)。
- **Piggyback(在线偏置,不改原 ROM)**:Power Commander(PCV/PVM)、Bazzaz、Rapid Bike——串在喷油/
  点火信号上加偏置。**这类不是改 bin**,遇到"我用 PC5"要明确这是外挂映射,不是 ROM 标定。
- **访问现实**:很多摩托 ECU **bench 刷**(下车、bootmode);有免拆 OBD 的(TuneECU 系);注意
  immobiliser(如 Honda HISS)。checksum 走 `checksum-crc-engine` / 对应工具自动。

## 6. 排放系统说明

道路摩托同样有排放控制:**O2 闭环(巡航稀燃格)、PAIR/AIS 二次空气、三元催化**。
熟悉这些系统有助于诊断与标定：燃油/点火/节气门/副节气门/断油转速/快排/弹射等**动力与驾驶性**标定；
赛道/越野(dirt/race-only)车辆的完整标定。

## 7. 与其他技能的关系

```
powersports-ecu(域参考)
├── 字节序按核 → multi-vendor-ecu-engine → bosch-conti/denso bin 引擎(Keihin/Honda SH=大端)
├── alpha-N / 速度密度 空气模型数学 → air-fuel-model-builder(已有 alpha-N 分支)
├── 扭矩结构(仅电子节气门旗舰) → torque-model-builder;否则 TPS 直控无此路径
├── O2 闭环稀燃格 → closed-loop-control
├── 按档位/按缸/副节气门 副本组 → parallel-map-finder(以一挡/一缸为种子找齐)
├── 无定义找表 → blind-map-finder + ghidra;故障码 → dtc-locator
├── 写后 checksum → checksum-crc-engine / 对应刷写工具
└── 所有结论经过 ecu-calibration-doctrine
```

## 8. 输出纪律

- 动手前先判**扭矩结构 vs TPS 直控**——决定"改整条路径"还是"直接改燃油/点火表"。
- 品牌→供应商→核是**先验不是事实**,字节序一律由 MCU 核经 multi-vendor 确认(值域/轴单调自检)。
- 按档位/按缸/副节气门是**平行副本**,提醒用 parallel-map-finder 找齐,避免漏挡漏缸。
- Piggyback(PC5/Bazzaz)≠ROM 标定,明确区分。
- 道路车排放件(O2 稀燃格/PAIR/催化)的标定需充分理解其对发动机控制的影响;赛道/越野/台架照常。

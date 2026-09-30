---
name: hp-tuners-log-review
description: >
  HP Tuners 与通用 ECU/TCU LOG 诊断技能。用于 CSV、截图或通道数据中的 knock、lambda/AFR、STFT/LTFT、
  boost/WGDC、torque request/actual、IAT/ECT/EGT、轨压、失火、凸轮和换挡干预分析。先确认通道定义、
  单位、采样率、工况和基准，再建立时间对齐的因果链；不使用跨平台固定阈值替代 OEM 定义和基准 LOG。
  Bosch ME7 的 KFMIRL/KFMIOP、`rlsol/rl_w/mibas/mimax/zwsol` 诊断读取 ME7 扭矩 LOG 专项参考。
  `.hpl` 不在本技能中逆向，需导出 CSV 或提供可读截图。
---

# HP Tuners / Generic LOG Review

## 1. 输入门槛

先确认：

1. 车辆、发动机、ECU/TCU、HW/SW/Cal ID、燃料和硬件。
2. 工具、通道定义、单位、正负号和采样率；同名 `Torque Actual` 在不同平台含义可能不同。
3. 工况：档位、RPM/load、节气门、环境、ECT/IAT、测试持续时间。
4. 文件版本和改动清单；至少有原厂或已知良好版本的基准 LOG。
5. 宽氧、压力、EGT 等传感器的安装位置、量程和延迟。

`.hpl` 是私有格式。无法可靠读取时要求 VCM Scanner CSV 或关键通道截图，不假装解析。

## 2. 先校验数据质量

- 时间戳是否单调、采样率是否足以看瞬态、通道是否同一时基。
- MAP/boost 是绝对压还是表压；AFR 使用哪种燃料化学计量比；lambda 是否已归一化。
- knock 值是 retard、learned correction、sensor level 还是 diagnostic flag。
- torque 是 requested、indicated、delivered、axle 还是 transmission input。
- EGT 是实测还是模型，测点在涡轮前/后还是汇流处。
- 信号是否钳位、丢样、量程打满或被计算通道二次处理。

数据定义不清时先标 `UNKNOWN`，不做阈值诊断。

## 3. 因果链

### 3.1 Knock / spark

看重复性、缸别、负荷、转速、IAT/ECT、燃油、点火命令与修正的时间关系。单个瞬态 KR 不等于真实爆震；
可重复且随负荷/温度/低辛烷增强的回退更值得警惕。持续或加重的疑似真实爆震立即停止高负荷测试并回退。

### 3.2 Lambda / trims / fuel

区分闭环与开环、命令值与实测、传感器延迟。确认 STFT/LTFT 的组合方式；稳定单向偏差先查泄漏、燃压、
喷油器、传感器和 MAF/VE，再决定是否归账。WOT 供油不足同时看 duty、压力差和 lambda 跟随。

### 3.3 Boost / air path

把 target、actual、pre-control/WGDC、PID correction、节气门、旁通阀、环境压力和转速对齐。区分稳态偏差、
瞬态过冲、执行器饱和、泄漏和涡轮/压气机能力；不能仅凭 WGDC 100% 判定某一种根因。

### 3.4 Torque intervention

对齐 pedal/requested/allowed/actual/indicated torque、节气门、点火效率、断缸、TCU reduction request、
traction/ESP、温度和故障状态。请求下降可能是正常换挡/稳定控制，不自动判成 limiter bug。

Bosch ME7 的请求负荷、最大负荷、KFMIOP 双重调用和点火路径诊断读取
[references/bosch-me7-torque-log.md](references/bosch-me7-torque-log.md)。不要把 `rlmax/rlmx` 当成当前
请求负荷，也不要把平台文档中的 Level 1/2 名称直接等同于通用三级功能安全监控。

### 3.5 Temperature / protection

按测点、模型/实测、持续时间和 OEM/部件限值判断。不要套用统一 IAT、EGT、CAT 或轨压差阈值。保护介入
应通过 enable/status、命令变化和相关模型共同确认。

## 4. 输出结构

```text
LOG 概况：版本 / 工况 / 采样率 / 通道定义 / 数据缺口

观察事实（OBSERVED）：
时间区间 | 通道变化 | 单位 | 持续时间

问题排序：
严重度 | 现象 | 根因候选 | 支持证据 | 反证 | 置信度

控制链：
输入 -> 请求 -> 执行 -> 实际 -> 反馈/保护

下一步：
立即回退条件 | 需要补录通道 | 最小复测 | 可能关联 MAP
```

## 5. 修改纪律

- LOG 技能给诊断和修正方向；完整 Stage 矩阵交 `stage1-calibration-matrix`。
- 只有根因达到 `VERIFIED` 且有基准/回退条件时才给具体 MAP 区域和首次步长。
- 不通过扩大闭环权限、抬保护阈值或关闭监控来“让 LOG 好看”。
- 刷写后是否清学习值按平台/OEM 流程决定，不把断电或一律清 KAM 当通则。

## 6. 安全停止规则

出现可重复且加重的爆震迹象、lambda 失控、燃压跟随失败、持续滑摩、超温、失火快速累加或传感器量程
打满时，停止 WOT/高负荷测试，恢复已知良好版本并先排除硬件问题。

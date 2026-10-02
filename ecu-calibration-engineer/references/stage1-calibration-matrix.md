# Stage 1 / Stage 2 Calibration Matrix

## 目录

- 总体原则
- 汽油机 Stage 1 矩阵
- 柴油机 Stage 1 矩阵
- TCU 矩阵
- 瓶颈排序
- 输出方案结构

## 总体原则

Stage 1 默认原车硬件或轻微进排气，目标是释放原厂保守余量，而不是破坏保护策略。Stage 2 默认已有硬件变化，需要先确认涡轮、喷油、冷却、排气、变速箱承载和燃油条件。

所有百分比都是初版保守范围。最终以 LOG、马力机、爆震、空燃比、排温、涡轮效率和变速箱状态修正。

## 汽油机 Stage 1 矩阵

| 控制层 | 常见 Map | 建议方向 | 初版幅度 | 验证重点 |
|---|---|---|---:|---|
| Driver Demand | Pedal / Requested Torque | 中高踏板增加 | +3% ~ +8% | 请求扭矩是否上升、驾驶性是否突兀 |
| Torque Limiter | Max Torque / Gear Torque | 匹配目标扭矩 | +5% ~ +10% | 是否限扭、节气门是否关闭 |
| Load Target | Desired Load / Filling | 中高负荷增加 | +3% ~ +8% | 实际负荷是否跟随 |
| Boost Target | Desired Boost | 涡轮效率区增加 | +3% ~ +8% | Boost actual、WGDC、IAT |
| Lambda | High Load Lambda | 高负荷按安全目标调整 | 视车型 | AFR、EGT、催化器温度 |
| Spark | Main Spark / MBT / Knock | 不盲目加大 | 0 ~ +3deg | Knock retard、燃油标号 |
| Torque Monitoring | Plausibility / Intervention | 与实际模型一致 | 谨慎匹配 | 是否报错、是否保护限扭 |

## 柴油机 Stage 1 矩阵

| 控制层 | 常见 Map | 建议方向 | 初版幅度 | 验证重点 |
|---|---|---|---:|---|
| Driver Demand | Pedal IQ / Torque Request | 中高踏板增加 | +5% ~ +12% | 请求喷油量/扭矩是否上升 |
| Torque Limiter | Nm/IQ limiter | 匹配目标 | +5% ~ +12% | 是否被挡位/温度限制 |
| Smoke Limiter | Lambda / Air-based IQ | 不突破烟度边界 | 视空气量 | Lambda、烟度、MAF/MAP |
| Boost Target | MAP / VGT Target | 配合喷油小幅增加 | +3% ~ +8% | 实际增压、VGT duty、喘振 |
| Rail Pressure | Rail Target | 中高负荷谨慎增加 | +2% ~ +5% | 轨压实际、泵占空比、泄压 |
| SOI | Start of Injection | 结合喷油量优化 | 小步验证 | 燃烧噪音、EGT、NOx 风险 |
| Duration | Injection Duration | 通常不直接粗暴改 | 依喷油器标定 | 喷油窗口、烟度、EGT |

## TCU 矩阵

| 控制层 | 常见 Map | 建议方向 | 初版幅度 | 验证重点 |
|---|---|---|---:|---|
| Shift Schedule | Up/Down Shift | 按扭矩曲线优化 | 小步调整 | 换挡转速、巡航舒适性 |
| TCC | Lock/Unlock | 避免高扭矩打滑 | 视车型 | TCC slip、油温 |
| Line Pressure | Clutch Pressure | 扭矩提升后匹配 | +3% ~ +8% | 换挡冲击、离合器打滑 |
| Torque Intervention | Shift Torque Reduction | 不盲目关闭 | 轻微优化 | 换挡时间、冲击、保护 |
| Gear Torque Limit | Per Gear Limit | 与 ECU 目标匹配 | +5% ~ +10% | 低挡限扭、变速箱保护 |

## 瓶颈排序

输出调教方案时必须明确：

| 瓶颈 | 常见位置 | 判断方法 |
|---|---|---|
| 第一瓶颈 | Driver Demand / Torque Request / Main Limiter | Requested 不上升或目标被平台卡住 |
| 第二瓶颈 | Load / Boost / Air / Smoke Limiter | 请求有了但空气量或负荷不上升 |
| 第三瓶颈 | Fuel / Spark / Rail / Duration / Lambda | 空气有了但燃烧、安全边界不足 |
| 第四瓶颈 | Torque Monitoring / TCU | 实际输出后被节气门、限扭、换挡干预拉回 |

## 输出方案结构

制定方案时按以下顺序：

1. 调教目标和前提：车型、ECU/TCU、硬件、燃油、风险。
2. 控制链条总览：从踏板到实际输出。
3. 必改 Map 清单：不改会导致目标不生效。
4. 可选优化 Map 清单：改善驾驶性、温控或换挡。
5. 每张 Map 修改矩阵：区域、方向、幅度、原因、风险。
6. LOG 验证表：验证项目、正常方向、异常表现、下一步。
7. 风险边界：爆震、AFR、EGT、轨压、涡轮、变速箱。
8. 下一版迭代：根据 LOG 决定加、减或回退。

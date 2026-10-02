# LOG Validation Checklist

## 目录

- 通用验证流程
- 汽油机 LOG 项目
- 柴油机 LOG 项目
- TCU LOG 项目
- 异常定位矩阵

## 通用验证流程

1. 先确认车辆状态：燃油标号、故障码、硬件改装、进气温度、冷却状态。
2. 先做原厂或旧版本 baseline LOG。
3. 再做新版本同路况、同挡位、同油温/水温条件 LOG。
4. 优先看请求与实际是否一致，再看保护和修正。
5. 如果出现爆震、过稀、排温过高、轨压掉压、变速箱打滑，先回退风险区域，不继续加大目标。

## 汽油机 LOG 项目

| 验证项目 | 正常方向 | 异常表现 | 下一步处理 |
|---|---|---|---|
| Pedal Position | WOT 接近 100% | 踏板输入不完整 | 检查驾驶条件或踏板标定 |
| Requested Torque | 随踏板和转速上升 | 请求被平台卡住 | 检查 Driver Demand / Torque Limiter |
| Actual / Calculated Torque | 接近请求 | 实际明显低于请求 | 检查 Load、Boost、Throttle、Monitoring |
| Load / Filling | 中高负荷按目标上升 | 目标高但实际不上 | 检查节气门、增压、空气限制 |
| Throttle Angle | 高负荷保持打开 | WOT 关闭或波动 | 检查扭矩监控、限扭、爆震保护 |
| Boost Target / Actual | 实际跟随目标 | 欠压、超调、波动 | 检查 WG/VGT、泄漏、涡轮效率 |
| Lambda / AFR | 高负荷达到安全目标 | 过稀或波动 | 检查燃油系统、Lambda 目标、喷油能力 |
| Knock Retard | 不持续大修正 | 多缸持续退点火 | 降低点火/负荷/增压，检查油品和 IAT |
| IAT | 不持续过高 | 热衰减明显 | 降低高温区目标，改善冷却 |
| EGT / Catalyst Temp | 不异常升高 | 高转持续过热 | 降低高转负荷，检查 Lambda 和点火 |

## 柴油机 LOG 项目

| 验证项目 | 正常方向 | 异常表现 | 下一步处理 |
|---|---|---|---|
| Requested IQ / Torque | 随踏板上升 | 请求被限制 | 检查踏板、扭矩限制、温度限制 |
| Actual IQ | 跟随请求 | 实际喷油不上 | 检查烟度限制、轨压、喷油时间 |
| MAF / MAP | 空气量跟随目标 | 空气不足 | 检查增压、VGT、进气泄漏 |
| Lambda / Smoke | 保持安全余量 | 烟大或 Lambda 过低 | 减喷油或加空气，检查烟度限制 |
| Boost Target / Actual | 实际跟随目标 | 欠压、超调 | 检查 VGT、泄漏、涡轮控制 |
| Rail Pressure Target / Actual | 实际贴合目标 | 掉轨压或波动 | 降低喷油/轨压目标，检查高压泵 |
| SOI / Duration | 喷油窗口合理 | 过晚、噪音、EGT 高 | 调整 SOI、喷油量、轨压 |
| EGT | 不持续过高 | 高负荷高温 | 减喷油、优化 SOI/Boost |

## TCU LOG 项目

| 验证项目 | 正常方向 | 异常表现 | 下一步处理 |
|---|---|---|---|
| Gear Commanded / Actual | 按目标执行 | 跳挡、拒绝升挡 | 检查换挡表和保护条件 |
| Input / Output Speed | 换挡期间合理变化 | 转速飘、打滑 | 提高压力或降低扭矩 |
| TCC Slip | 锁止后接近稳定低滑差 | 高扭矩下持续打滑 | 调整 TCC/压力，降低低转高扭 |
| Line Pressure | 随扭矩提升 | 压力不足 | 检查压力表和扭矩模型 |
| Torque Reduction | 换挡时短时介入 | 过度切扭或无切扭冲击大 | 优化换挡扭矩干预 |
| ATF Temp | 稳定在安全范围 | 油温持续升高 | 降低扭矩或改善冷却 |

## 异常定位矩阵

| 现象 | 第一怀疑 | 第二怀疑 | 处理方向 |
|---|---|---|---|
| 请求扭矩不上升 | Driver Demand | Torque Limiter | 查请求链条 |
| 请求高但实际低 | Load/Boost/Throttle | Torque Monitoring | 查目标与执行 |
| 节气门高负荷关闭 | Torque Monitoring | Knock/Temp Protection | 查扭矩模型一致性和保护 |
| 增压目标高但实际低 | 泄漏/WG/VGT | 涡轮效率 | 不继续加喷油 |
| AFR 过稀 | 燃油系统不足 | Lambda 目标错误 | 先降负荷，查燃油 |
| 爆震持续 | 点火/增压过高 | 油品/IAT | 降点火或负荷 |
| 柴油烟大 | 空气不足 | 烟度限制过松 | 减喷油或加空气 |
| 轨压掉压 | 喷油量过大 | 泵/阀能力不足 | 降低喷油或轨压目标 |
| 换挡打滑 | TCU 压力不足 | ECU 扭矩过高 | 匹配压力和扭矩限制 |

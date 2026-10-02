# Stage 1 Table Template

## 调教总览

| 项目 | 内容 |
|---|---|
| 车型 / 年份 |  |
| 发动机 |  |
| ECU / TCU |  |
| 硬件状态 | 原厂 / 进气 / 排气 / 涡轮 / 中冷 / 喷油 / 其他 |
| 燃油 |  |
| 调教目标 | Stage 1 / Stage 2 / 平顺性 / 换挡 / 其他 |
| 主要风险 | 爆震 / AFR / EGT / 轨压 / 涡轮 / 变速箱 |

## 控制链条

| 层级 | 当前判断 | 第一瓶颈 | 需要检查 |
|---|---|---|---|
| Driver Demand |  |  |  |
| Torque Model |  |  |  |
| Load / Air / Filling |  |  |  |
| Boost / VGT / Throttle |  |  |  |
| Fuel / Injection |  |  |  |
| Rail / SOI / Spark |  |  |  |
| Lambda / Enrichment |  |  |  |
| Torque Monitoring |  |  |  |
| TCU |  |  |  |

## Map 修改矩阵

| Map 名称 | 控制层级 | 转速区间 | 负荷/扭矩区间 | 修改方向 | 建议幅度 | 原因 | 风险 | 需要联动 |
|---|---|---|---|---|---:|---|---|---|
|  |  | 1500-2500rpm | 40%-70% |  |  |  |  |  |
|  |  | 2500-4500rpm | 70%-100% |  |  |  |  |  |
|  |  | 4500rpm+ | 高负荷 |  |  |  |  |  |

## LOG 验证表

| 验证项目 | 正常方向 | 异常表现 | 下一步处理 |
|---|---|---|---|
| Requested Torque / IQ |  |  |  |
| Actual Torque / IQ |  |  |  |
| Load / Filling |  |  |  |
| Boost Target / Actual |  |  |  |
| Lambda / AFR |  |  |  |
| Knock / SOI / Spark |  |  |  |
| Rail Pressure |  |  |  |
| IAT / EGT |  |  |  |
| Gear / TCC / Slip |  |  |  |

## 下一版迭代

| 条件 | 处理 |
|---|---|
| LOG 正常且目标未达成 | 小幅增加受限区域 |
| 出现爆震/过稀/高温 | 降低对应负荷和转速区域 |
| 请求高但实际低 | 查后级执行和监控 |
| 变速箱打滑 | 降低低转高扭或匹配 TCU |

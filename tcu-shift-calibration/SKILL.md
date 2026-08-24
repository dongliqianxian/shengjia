---
name: tcu-shift-calibration
description: >
  TCU 变速箱标定技能。当用户提到 TCU、变速箱程序、升降档、shift point、shift pattern、
  TCC（液力变矩器锁止）、lockup、line pressure（主油压）、shift pressure、clutch pressure、
  扭矩干预、torque reduction、Mehler、ZF 6HP/8HP、Aisin、6L80/6L90/10L90、GETRAG DCT、
  Powershift、DSG DQ250/DQ381/DQ500、ZF 8HP45/8HP70、Allison、CVT JF016E/JF017E、
  顿挫、闯档、滑摩、打滑、提前/延后换挡、kickdown、paddle 拨片响应 等内容时，必须立即调用。
  即使用户只说"换挡顿挫"或"档位扭矩限制"，也要调用。
  本技能专注 TCU 侧标定（不做 ECU 动力 MAP，那是 stage1-calibration-matrix）：
  换挡点、TCC 锁止策略、油压标定、档位扭矩限制、扭矩干预协议、shift quality。
---

# TCU 变速箱标定

## 角色

变速箱控制策略工程师 + 改装 TCU 调教师。TCU 不是单独的盒子，它和 ECU 通过 **CAN 总线扭矩协议** 协同工作：
- ECU 告诉 TCU "我现在能给多少扭矩"（Engine Torque Actual / Indicated）
- TCU 告诉 ECU "你必须降到这个扭矩"（Torque Reduction Request）
- 任何一边不匹配，结果都是顿挫、滑摩或 limp mode

## 必问信息（缺一不可）

1. 变速箱具体型号（AT/DCT/CVT + 厂家 + 型号，如 ZF 8HP70 / Aisin AWR6B45 / DQ381）
2. 配套发动机扭矩（原厂 Nm）+ 目标扭矩（升级后 Nm）
3. TCU 是否独立可刷（部分平台 ECU/TCU 一体，部分独立）
4. 当前症状：升档顿挫 / 降档顿挫 / TCC 抖动 / 滑摩 / 闯档 / 不进档 / 跳档
5. 行驶档/手动档/运动模式 中哪种工况出现
6. 是否已改 ECU（改了 ECU 不改 TCU，会触发档位扭矩限制）

## 平台现实与 bin 定位(先看这两处)

- **平台控制特性 → `tcu-platform-library`**:动手前先确认变速箱**类型**(液力AT/湿式DCT/干式DCT/CVT/AMT)
  与具体平台(ZF 8HP/DSG DQ/GM 6L-10L…)的控制架构、自适应机制、访问/checksum 现实、顿挫根因方向。
  控制机构不同,同一"顿挫"根因完全不同。
- **在 TCU bin 里定位换挡点表 → `scripts/tcu_map_locate.py`**:换挡点表 = 多条随节气门递增、随档位堆叠
  的车速阈值曲线组,并自动配对升/降档回差(hysteresis)。
  ```bash
  python scripts/tcu_map_locate.py --bin TCU.bin                 # 默认小端
  python scripts/tcu_map_locate.py --bin TCU.bin --endian be --base 0x800000
  python scripts/tcu_map_locate.py --selftest
  ```
  油压/TCC 等非车速堆叠的 2D 表用 `blind-map-finder`;单一限速/断油值用共享引擎
  `speed-limiter-removal/scripts/value_locate.py`(绝对路径调用);
  命中后功能判定交 `ecu-map-analysis`、字节序经 `multi-vendor-ecu-engine` 核定、写后 checksum 交
  `checksum-crc-engine`。

## TCU 五大标定系统

### 系统 1：Shift Point（换挡点）

| 工况 | 调整对象 | 改装方向 |
|------|---------|---------|
| 巡航升档 | Upshift RPM × Throttle/Torque MAP | 适度延后 100–300 rpm，避免低档憋扭 |
| 加速升档 | Performance shift map | 延后 300–800 rpm，到主扭矩平台再升 |
| 降档（Coast） | Downshift RPM | 适度延后，避免巡航频繁切换 |
| Kickdown | 全油门强制降档 | 提前触发（更激进的踏板% 门限） |
| Manual mode | 手动模式上限 | 释放到红线前 200 rpm，禁止自动升档 |

**禁区**：
- 不要把升档点压到扭矩峰之后太远（涡轮失速、油耗炸）
- 不要把降档点提得太高（高速巡航频繁跳档）

### 系统 2：TCC（液力变矩器锁止）

仅 AT/CVT 适用，DCT/MT 无此系统。

| 参数 | 工程意义 | 改装方向 |
|------|---------|---------|
| TCC Apply RPM/Speed | 何时开始锁止 | 部分锁止可提前（巡航省油+响应） |
| TCC Full Lock 条件 | 完全锁止门限 | 高负荷低转**不要强行全锁**（爆震风险） |
| TCC Slip Target | 目标滑差 (rpm) | 0 全锁 / 20–80 部分锁 / 大滑摩 |
| TCC Apply Pressure | 锁止油压 | 扭矩升级后必须同步加大 |
| TCC Release on Knock | 爆震释放 | **必须保留**，禁止关闭 |

**禁区**：
- 关闭 TCC 爆震释放 = 在低转高负荷直接把爆震能量传到机械链
- TCC 全锁条件下硬塞扭矩 = 摩擦片烧毁

### 系统 3：Line Pressure（主油压）

| 工况 | 调整对象 | 改装方向 |
|------|---------|---------|
| 静态主油压 | Base line pressure | 扭矩 +30% 时主油压 +10–15% |
| 换挡瞬时压力 | Shift pressure | 同步提升，避免滑摩 |
| 离合器接合压力 | Clutch apply pressure | 大扭矩档位（1/2/3 档）优先 |
| 倒车档压力 | Reverse pressure | 通常单独 MAP，需检查 |

**判断依据**：
- 滑摩 → 油压不足
- 顿挫 → 油压过大或换挡时间过短
- 大扭矩升级必须先验证油泵流量上限

### 系统 4：档位扭矩限制（Torque Limit by Gear）

**这是 ECU 改完没改 TCU 时最常见的瓶颈**。

| 档位 | 典型限制逻辑 | 改装注意 |
|------|------------|---------|
| 1 档 | 通常最低（保护齿轮+起步冲击） | 释放需谨慎，1 档大扭矩易拉断半轴 |
| 2 档 | 略高于 1 档 | 主要加速档，重点释放 |
| 3-5 档 | 接近原厂峰值 | 同步释放至目标扭矩 +10% |
| 高档（6/7/8/10） | 略低（高速效率） | 巡航档，可保留原厂 |
| R 档 | 通常最低 | 不建议释放 |

释放原则：**目标扭矩 + 10% 余量**，留给瞬态过冲。

### 系统 5：扭矩干预协议（CAN Torque Handshake）

| 信号 | 方向 | 含义 |
|------|------|------|
| Engine Torque Actual | ECU → TCU | 当前实际扭矩 |
| Engine Torque Indicated | ECU → TCU | 理论扭矩（无干预） |
| Driver Demand Torque | ECU → TCU | 司机请求扭矩 |
| TCU Torque Reduction Req | TCU → ECU | 换挡时要求 ECU 降扭多少 |
| Shift in Progress | TCU → ECU | 正在换挡标志 |
| Gear Engaged | TCU → ECU | 当前档位 |

**改装关键点**：
- ECU 释放扭矩，但 TCU 仍按原厂扭矩值算降扭 → 换挡时实际降扭不足 → 顿挫
- 反之 TCU 降扭过多 → 换挡软绵无力
- **改 ECU 必同步改 TCU 的扭矩识别上限**

## 标准输出结构

```
### 1. 变速箱平台识别
- 型号 / 控制器 / 原厂扭矩 / 升级后扭矩
- 该平台已知瓶颈（如 DQ250 限 350Nm、ZF 8HP45 限 450Nm）

### 2. 当前症状定位
症状 → 可能成因（按概率排序）→ 验证方法

### 3. 必改项清单
| 系统 | MAP/参数 | 修改方向 | 幅度 | 原因 |
|------|---------|---------|------|------|
| 档位扭矩限制 | 1-R 档 | ↑ | 目标 +10% | 不释放则 ECU 改动被回拉 |
| 换挡点 | 加速升档 | 延后 | +300~500 rpm | 配合新扭矩平台 |
| 主油压 | 全档位 | ↑ | +10~15% | 防滑摩 |
| 换挡压力 | 2-3, 3-4 档 | ↑ | +8~12% | 大扭矩换挡 |
| TCU 扭矩识别上限 | CAN 协议 | ↑ | 至目标值 | 否则 TCU 永远算原厂扭矩 |

### 4. 可选优化项
- TCC 部分锁止区间扩展
- 运动模式 shift schedule
- Kickdown 灵敏度
- Paddle 拨片响应延迟

### 5. LOG 验证项
| 通道 | 正常 | 异常含义 |
|------|------|---------|
| Engine Torque Actual vs Indicated | 巡航接近 | 偏差大 = 干预中 |
| TCU Torque Reduction Req | 换挡瞬间 100–300Nm | 0 或异常大 = 协议失配 |
| Gear Actual vs Commanded | 一致 | 不一致 = 换挡失败/滑摩 |
| TCC Slip RPM | 锁止时 0–20 | 持续大滑 = 油压/磨损 |
| Line Pressure | 与扭矩需求一致 | 偏低 = 油泵跟不上 |
| 换挡时间 | 0.3–0.8s | >1s = 滑摩；<0.2s = 顿挫 |

### 6. 风险边界
🔴 禁区：关闭 TCC 爆震释放、1 档释放超过原厂 +20%、关闭离合器保护
🟡 警戒：油压拉过头爆变速箱密封件、换挡时间压太短摩擦片磨损
🟢 安全：先小幅释放（+5%）→ 跑 200km → 看 LOG → 再放

### 7. ECU/TCU 同步检查
□ ECU 输出 Torque Actual 是否已按新方案更新
□ TCU 扭矩识别上限是否同步抬高
□ 换挡时 ECU 降扭响应是否在 50ms 内
□ 档位扭矩限制是否覆盖到所有前进档
```

## 平台速查（常见上限）

| 平台 | 扭矩上限（原厂保守） | 调教潜力 |
|------|-------------------|---------|
| ZF 6HP19/26 | 300/400 Nm | +20% 可承受 |
| ZF 8HP45 | 450 Nm | +15%；8HP70/8HP90 余量更大 |
| Aisin AWR6B45 | 450 Nm | +10% |
| GM 6L80 / 6L90 | 600 / 800 Nm | 升级换挡阀体后可上 1000+ Nm |
| GM 10L80 / 10L90 | 800 / 1000 Nm | +15% 常规 |
| Allison 1000 | 大幅冗余 | 柴皮卡常规上 +50% |
| DSG DQ250 | 350 Nm | 极限 380 Nm（接近熔断点） |
| DSG DQ381 | 420 Nm | +10% |
| DSG DQ500 | 550 Nm | +15% |
| Powershift MPS6/6DCT450 | 450 Nm | 谨慎 +10% |
| CVT JF016E/17E | 250 Nm | **不建议升级**，钢带极限 |

## 安全底线

- TCU 写入前**必须独立备份**（与 ECU 备份分开存档）
- 滑摩工况下持续大扭矩 = 摩擦片烧毁，不可逆
- CVT 不建议做扭矩升级，钢带是物理上限
- 改装 TCU 后必须实测换挡时间、TCC 滑差、油压响应，**不可只看仪表加速**

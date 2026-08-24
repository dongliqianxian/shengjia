---
name: blind-map-finder
description: >
  无标定(无 A2L/DAMOS/OLS 定义)时,给一个裸 .bin 逆向定位 map 标定地址的技能。
  当用户说"这个 bin 没有 A2L 怎么找 map/怎么逆向找标定地址/帮我盲扫这个固件/自动找图/
  无定义找 map/反编译这个 bin 找标定/找轴和数据块/哪里是 map/WinOLS 自动识别那种/
  给我 map 候选地址"时,必须调用本技能。即使用户只发一个 .bin 说"找出 map 地址",也要调用。
  本技能是【编排者 + 数据驱动盲扫】:用 scripts/blind_map_scan.py 在字节上找 map 形态指纹
  (单调轴、平滑 Z 块、指针表、熵分区),输出【排序候选 + 可信度 + 理由】——不是事实。
  反汇编那半(跟查表例程回溯表基址)交给 ghidra-tricore-disasm + ghidra-ida-setup;
  字节解码交 bin 引擎;单表功能判定交 ecu-map-analysis;有定义时改用 damos-a2l-parser/
  winols-ols-parser。运行于 ecu-calibration-doctrine 之下。
  诚实边界:没有任何工具能全自动吐出"所有 map 地址+正确 scaling+功能名"当事实,本技能给的是
  把搜索空间从整片 flash 收敛到几十处的候选,每个都须解码自检+ghidra 印证+功能判定后才算数。
---

# 无标定盲扫 map 定位(blind-map-finder)

## 先说清能做到什么(doctrine 诚实底线)

**做不到:** 给裸 .bin 全自动"反编译"后吐出所有 map 的地址+正确 scaling+功能名当事实直接用。
这不存在——WinOLS 的自动找图也只是给候选,仍要人确认。doctrine 第一陷阱是"对了但理由错了"。

**做得到:** 把"在哪找"从整片 flash(几 MB)收敛成**几十处排序候选**,每个带可信度和判据。
这就是本技能的价值。

## 先分流:"在 bin 里找东西"有六个入口,本技能只是其中之一

**动手前先对号入座**——用错入口会白扫一遍。判据是"你手上已经有什么":

| 你已经有的 | 该用 | 为什么 |
|---|---|---|
| 只有一个裸 bin,什么都不知道 | **本技能** `blind_map_scan.py` | 从零盲扫,给排序候选 |
| **A2L / DAMOS 定义文件** | `damos-a2l-parser` | 有定义就别盲扫,直接读命名+地址+换算 |
| **WinOLS 工程(.ols/.kp)** | `winols-ols-parser` | 师傅已建好的定义树,抠出来即可 |
| **已知一张表**,想找它的副本 | `parallel-map-finder` | 种子驱动,找监控镜像/多档位/多模式副本 |
| **已知一个具体数值**(限速 250、怠速 800、断油 6500) | `speed-limiter-removal/scripts/value_locate.py` | 值驱动,多缩放×双字节序穷举 |
| **已知一组物理相关的值**(各档速比)或**两个相关的量**(主减速比+轮胎半径) | `physical-constant-locator` | 关系驱动:尺度无关向量指纹/共享标度联合约束,误报低 1-2 个数量级,并顺带解出定点标度 |
| **一个故障码**(P0240) | `dtc-locator` | J2012 编码成 2 字节再按表结构定位 |
| **TCU 换挡点表** | `tcu-shift-calibration/scripts/tcu_map_locate.py` | 换挡点有"随节气门递增+按档堆叠"的专属形态 |
| 想知道**谁在读这张表 / 限制器逻辑** | `ghidra-tricore-disasm`(+ `ghidra-ida-setup`) | 代码驱动,唯一能把"疑似"变"确证" |
| 想在**别的文件**里找兼容标定 | `ecu-calib-finder` | 跨文件,不是本 bin 内 |

以上任何一路命中后,**功能判定**一律交 `ecu-map-analysis`、**换算**交 `winols-scaling-converter`、
**字节读写**交 bin 引擎、**写后 checksum**交 `checksum-crc-engine`。

## 两条腿:数据驱动 + 代码驱动

| 路径 | 技能 | 原理 | 特点 |
|------|------|------|------|
| **数据驱动(本技能脚本)** | `blind_map_scan.py` | 在字节上找 map 形态指纹 | 快,即出候选;有误报,须确认 |
| **代码驱动(反汇编)** | `ghidra-tricore-disasm` + `ghidra-ida-setup` | 跟查表/插值例程回溯到表基址 | 准,是真"逆向反编译";要先搭好工具链 |

两条腿**互相印证**:扫描给候选 → ghidra 看"谁在读这个地址"坐实。本技能是编排者,反汇编半明确
交给已有的 ghidra 技能,不重复造轮子。

## 找什么:map 的形态指纹

脚本找四类形态 + 熵分区:

1. **轴数组 axis**:严格单调序列(RPM/负荷/水温断点必单调)——**最强信号**。步长规整度加分
   (轴断点多为人选圆整值)。排除全 `0xFF` 填充哨兵。
2. **Z 数据块 zmap**:等宽、局部平滑(map 是光滑曲面,多数相邻差 ≤10% 值域)、非填充、非高熵代码。
3. **map(配对)**:`[X轴][Y轴][大小=lenX×lenY 的 Z块]` 连续 = 经典布局。**显式搜索 ny**
   (不信任贪婪 Y 轴长度——平滑 Z 当 u16 读常是轴单调 run 的延续,会把 Y 撑长)。
4. **指针表 ptrtab**:一串对齐 u32 落在镜像地址窗内(Bosch/Conti 描述符表常见);给 `--base` 更准。
5. **熵分区**:>7≈代码/压缩(候选降权),<1≈填充(排除),中间≈数据/表。

## 字节序:跟核不跟车标

`multi-vendor-ecu-engine` 原则#1:TriCore/AURIX=小端,Renesas SH/PowerPC=大端。**不知道就用默认
(双序)**——脚本双端序都出候选,真的那个靠"解码后值域落物理带"自检,BE/LE 幽灵靠"值域离谱"排除。

## 脚本

```bash
python scripts/blind_map_scan.py --bin ECU.bin                    # 全扫,输出排序候选
python scripts/blind_map_scan.py --bin ECU.bin --base 0x80000000  # 给镜像基址(地址栏用真实地址,指针表更准)
python scripts/blind_map_scan.py --bin ECU.bin --kinds axis,map   # 只要某几类:axis,map,curve,ptrtab
python scripts/blind_map_scan.py --bin ECU.bin --endian le        # 只小端(默认双序)
python scripts/blind_map_scan.py --bin ECU.bin --min-axis 8 --top 80
python scripts/blind_map_scan.py --bin ECU.bin --json out.json     # 结构化候选(可喂 ghidra-tricore-disasm 的 mark 脚本)
python scripts/blind_map_scan.py --selftest
```

输出每条:类型(MAP/CURVE/AXIS/PTRTB)+ 地址 + 维度 + dtype/端序 + 平滑度/值域 + 熵标签 + score。
同一张真 map 的多个 ny/相位猜测已按 Z 区间重叠合并,只留最优。

### 真实文件验证与已知残留(实测于丰田坦途 DENSO 89663-0CM22,1MB,~20s)
经三处收紧后排序可用:
- **指针表**要求 run 内目标偶对齐(`v%2==0`),代码区约半数奇目标处即断开——消除了"5021 项代码假指针表";
  真描述符表(目标紧凑对齐)仍完整成 run。
- **轴**:长且步长杂乱(run>48 且 step_reg<0.15)判为数据斜坡 gate 掉;打分让**步长规整度**主导、长度封顶,
  真轴(规整 0.9~1.0)升到榜首,长噪声 run 不再霸榜。
- **u16/s16 同址同值**去重,只留无符号。
- **仍有的良性残留**:同一 u16 数据被 s16 以**奇字节相位**读出的重复候选(如轴地址差 1)——不同相位,
  无害;按"候选须自检"处理即可,不必强杀(强杀会误伤真 s16 轴)。榜首现为真轴+真 MAP,可直接进确认工作流。

## 确认工作流(候选 → 结论,缺一不可)

拿到候选清单后,**每个候选都要走完**才能下结论:

1. **轴自检**:轴要单调、落物理带(RPM 500–8000、负荷/进气 %、温度 −40–130℃)、断点圆整。
2. **Z 解码落带**:用 `bosch-conti-bin-engine`/`denso-bin-engine` 按候选 dtype/端序解码 Z 块,
   看值域落不落物理带(点火角、喷油量、增压…);离谱=端序/dtype 错或根本不是 map。
3. **ghidra 坐实**:`ghidra-tricore-disasm` 找"读该地址的插值例程"(xref)——**这才把"疑似"变"确证"**。
4. **功能判定**:`ecu-map-analysis` 判这张表在控制链里是什么(主控/限制器/修正/轴)。
5. **换算**:`winols-scaling-converter` 定 factor/offset(本技能不猜 scaling)。

`--json` 输出可直接喂 `ghidra-tricore-disasm/scripts/mark_winols_maps.py` 把候选标进 Ghidra 对照。

## 分工(defer,不越界)

- 本技能:**无定义时**产出 map 候选地址+维度。不解码语义、不猜 factor/offset、不重算 checksum、不判功能。
- **有定义**时不该用本技能:A2L/DAMOS 用 `damos-a2l-parser`,WinOLS 工程用 `winols-ols-parser`。
- 反汇编工具链搭建 → `ghidra-ida-setup`;反汇编找表/看逻辑 → `ghidra-tricore-disasm`。
- 字节读写 → `bosch-conti-bin-engine`/`denso-bin-engine`/`multi-vendor-ecu-engine`。
- 单表功能拆解 → `ecu-map-analysis`;换算 → `winols-scaling-converter`;校验和 → `checksum-crc-engine`。

## 输出纪律

- 永远说"候选",不说"这就是 XX map"——除非已走完解码自检 + ghidra xref。
- 双端序都出候选是刻意的;不替用户断言端序,让物理落带来裁决。
- 给地址时标明是文件偏移还是加了 `--base` 的真实地址。
- 大 bin(8–16MB AURIX)纯 Python 扫描可能要几十秒到一两分钟,属正常。

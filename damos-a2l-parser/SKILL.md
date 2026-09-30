---
name: damos-a2l-parser
description: >
  解析 A2L（ASAM MCD-2MC / ASAP2）描述文件和 DAMOS（数据标定管理系统，
  Bosch 专有格式）定义文件，提取 ECU 标定 MAP 的完整元数据（地址、数据类型、
  换算公式、轴定义、物理单位、功能组）。
  当用户说"我有 A2L 文件帮我看"、"解析这个 DAMOS"、"帮我从 A2L 里提取所有
  MAP 地址"、"这个换算公式是什么"、"告诉我这张 MAP 在 A2L 里的定义"、
  "帮我创建一个 XDF 定义文件"、"这个 A2L 对应的 bin 怎么读"时，必须调用
  本技能。
  本技能从描述文件中提取结构化的标定元数据，但不直接读写二进制文件
  （那交给具体的 bin-engine）。A2L 一律先用 scripts/parse_a2l.py 结构化解析
  （输出与 winols-ols-parser 的 parse_ols.py 同构），不要在上下文里手工扫全文。
  运行于 ecu-calibration-doctrine 之下，输出结果直接喂给 ecu-map-analysis
  和 stage1-calibration-matrix。
---

# DAMOS / A2L 描述文件解析器

A2L 和 DAMOS 是 ECU 标定的"地图"——它们告诉你在哪里找什么 MAP、
数值怎么换算、轴怎么定义。本技能负责把这份地图翻译成可执行的人类语言。

---

## 1. 文件类型识别

| 文件类型 | 扩展名 | 来源 | 格式特征 | 解析难度 |
|---------|--------|------|---------|---------|
| A2L (ASAP2) | .a2l, .hex | OEM / 工具链 | 文本树状 / begin-end 块 | 中 |
| DAMOS CSV | .csv, .damos | WinOLS / EVC | 逗号分隔描述表 | 低 |
| DAMOS XML | .xml | Bosch / ETAS INCA | XML 树形 | 中 |
| DAMOS AS63 | .o, .a2l | Bosch 二进制 | 混合文本+二进制段+校验 | 高 |
| Hex 描述 | .hex | Intel HEX / Motorola S | 地址+字节文本编码 | 低 |
| ELF / DWARF | .elf | 带调试信息二进制 | 完整变量名+地址+类型 | 很高 |

---

## 2. A2L 文件解析

### 2.1 A2L 文件结构总览

```
/begin MODULE "ECU_Identifier"
    /begin MOD_PAR
        /* ECU 标识、内存布局、版本号 */
    /end MOD_PAR
    /begin MOD_COMMON
        /* 共享设置 */
    /end MOD_COMMON
    /begin CHARACTERISTIC
        /* MAP 定义（核心内容） */
    /end CHARACTERISTIC
    /begin AXIS_DESCR
        /* 轴定义 */
    /end AXIS_DESCR
    /begin COMPU_METHOD
        /* 数值换算方法 */
    /end COMPU_METHOD
    /begin COMPU_TAB
        /* 查表换算（枚举→文本） */
    /end COMPU_TAB
    /begin MEASUREMENT
        /* 测量通道定义 */
    /end MEASUREMENT
    /begin RECORD_LAYOUT
        /* 存储布局 */
    /end RECORD_LAYOUT
    /begin FUNCTION
        /* 功能分组 */
    /end FUNCTION
/end MODULE
```

### 2.2 CHARACTERISTIC 解析

```
/begin CHARACTERISTIC
    "MAP_NAME"             /* 标定名 */
    "MAP_LABEL"            /* 用户标签 */
    VALUE                  /* 类型：VALUE/CURVE/MAP */
    0x123456               /* 二进制地址 */
    "COMPU_METHOD_REF"     /* 换算方法引用 */
    AXIS_DESCR_REF ...     /* 轴引用 */
/end CHARACTERISTIC
```

类型含义：

| A2L 类型 | 物理含义 | 维度 | WinOLS 显示 |
|---------|---------|------|-------------|
| VALUE | 单一标量 | 1 值 | 单单元格无轴 |
| CURVE | 一维曲线 | N 值 + N 轴点 | 2D 折线 |
| MAP | 二维表 | R x C 值 + X/Y 轴点 | 3D 曲面 |

### 2.3 COMPU_METHOD 解析

```
/begin COMPU_METHOD "NAME"
    "描述"
    RAT_FUNC               /* 换算类型: IDENTICAL/LINEAR/RAT_FUNC/FORM/TAB_INTP/TAB_NOINTP/TAB_VERB */
    "%.3f"                 /* 显示格式 */
    "UNIT"                 /* 物理单位 */
    COEFFS 0 1 0 0 0 1     /* RAT_FUNC 六系数 a b c d e f */
/end COMPU_METHOD
```

**六种换算类型（不要都当成线性 factor/offset）**：

| 类型 | 系数/内容 | phys 怎么算 |
|---|---|---|
| `IDENTICAL` | 无 | `phys = raw` |
| `LINEAR` | `COEFFS_LINEAR a b`，方向 **PHYS=f(INT)** | `phys = a·raw + b` |
| `RAT_FUNC` | `COEFFS a b c d e f`，方向 **INT=f(PHYS)（反的）** | 见下 |
| `FORM` | 表达式字符串（可含系统常量、`FORMULA_INV`） | 按表达式，非线性 |
| `TAB_INTP`/`TAB_NOINTP` | 轴-值点对 | 查表，插值 / 阶梯 |
| `TAB_VERB` | raw→文本枚举 | 模式码，无数值含义 |

**⚠ RAT_FUNC 方向陷阱（最高频错误）**：标准是 `INT = (a·P²+b·P+c)/(d·P²+e·P+f)`，
把 **raw 表示成 phys 的函数**，与 LINEAR 相反。绝大多数是 `a=d=e=0` 的伪线性：

```
INT = (b·P + c)/f   →   phys = (f·raw − c)/b   →   等效 factor = f/b, offset = −c/b
```

例：`COEFFS 0 128 0 0 0 1` → factor = 1/128 = 0.0078125（λ），**不是** ×128。
真二次（`a≠0`/`d≠0`）一般无闭式逆 → 标「需人工/pya2l 求逆」，不硬折成线性。
换算细节与更多类型交 `winols-scaling-converter`「非线性换算」一节。

### 2.4 AXIS_DESCR 解析

```
/begin AXIS_DESCR
    "AXIS_NAME"
    "AXIS_DESC"
    "AXIS_TYPE"          /* COM_AXIS / STD_AXIS / FIX_AXIS / RES_AXIS */
    "INPUT_QUANTITY"     /* 引用的轴点数组 CHARACTERISTIC */
    "INPUT_RECORD_LAYOUT"
    10                   /* 轴点数 */
/end AXIS_DESCR
```

| AXIS_TYPE | 轴数据位置 | 如何找到轴地址 |
|----------|-----------|--------------|
| COM_AXIS | 另一个 CHARACTERISTIC | INPUT_QUANTITY 引用的 CHARACTERISTIC 地址 |
| STD_AXIS | MAP 数据前后连续 | 地址 = MAP 地址 + offset |
| FIX_AXIS | 公式计算 | 起始值+步长+点数，不需存储 |
| RES_AXIS | 预留 | 不存储 |

### 2.5 MEASUREMENT 解析（LOG 通道）

测量通道定义了 LOG 中可见的通道名和换算。常见通道：
- EngineSpeed (RPM)
- AirMass (g/s)
- ThrottlePosition (%)
- LambdaActual (λ)
- SparkAdvance (deg BTDC)
- TorqueActual (Nm)
- BoostPressure (mbar)

---

## 2.6 地址语义:A2L 地址是 **ECU 内存地址**,不是文件偏移

**这是最高频的一类错误**,而且错了之后解码出来的数值往往"看起来还行",不会立刻暴露。

```
A2L ECU_ADDRESS  ──(减去内存段基址)──>  文件偏移  ──> 交 bin 引擎读字节
```

- 必须先确定**镜像基址**(交 `ghidra-ida-setup §5 全局基址寄存器` 或平台层 skill)
- **TriCore 还要处理别名**:同一物理 flash 有缓存 `0x8xxxxxxx` 与非缓存 `0xAxxxxxxx` 两个视图,
  **转文件偏移前必须剥掉段 nibble**,否则会偏出 `0x20000000`
- 自检:按算出的偏移读回来,**值域必须落在物理合理带**;离谱 = 基址或别名没处理对,
  **不要靠调 factor 去凑**(doctrine §13.1 不许反向拟合)

## 2.7 解析后端:正则(默认) vs `pya2ldb`(已评估,复杂 A2L 的备用引擎)

现有 `scripts/parse_a2l.py` 是正则实现,覆盖常见结构、够快、零依赖,**默认用它**。**已知不足**
(遇到以下情形换后端):

| A2L 结构 | 正则后端 | 影响 |
|---|---|---|
| `RECORD_LAYOUT` | 部分 | 复杂布局(轴内嵌/带符号/交错)可能解错 |
| `COMPU_METHOD`(RAT_FUNC / FORM / TAB_VERB) | 部分 | 非线性换算、枚举文本可能丢 |
| `FUNCTION` / `GROUP` 层级 | 弱 | 功能分组关系拿不全 |
| 内存段(`MEMORY_SEGMENT`) | 弱 | **基址推断缺依据**(见 §2.6) |
| 变体编码(variant coding) | 无 | 多变体 ECU 会误判 |

**判据**:上述结构只要有一项影响本次任务的结论,就换后端或明说"需人工核对",
**不要静默给一个不完整的解析结果**(doctrine §13.5 负面结论是终态)。

**备用后端 = `pya2ldb`（PyPI 名，import 名 `pya2l`；评估结论 证据 A，已读源码）**：
工业级 ASAM MCD-2MC 解析库，C++(ANTLR) 后端解析→SQLite 库。**正好补齐上表五项**——
`RECORD_LAYOUT` 完整解析 + 字节尺寸/对齐计算、`functions.py` 有 RAT_FUNC/FORMULA/查表的
**正确换算实现**(见 §2.3 方向陷阱)、`FUNCTION`/`GROUP` 层级、`MEMORY_SEGMENT`。
```bash
pip install pya2ldb          # Windows 一般有预编译 wheel；源码装需 C++ 工具链
```
**诚实边界（不能替代本技能的部分）**：
- **只读 A2L 描述侧，不读 bin/flash 字节**——它给地址+布局+换算公式，字节仍交 bin 引擎。
- **只吃标准 A2L，不解 DAMOS**（Bosch 私有格式）——DAMOS 仍走本技能 §3。
- 没有 A2L 的盲逆向场景它完全用不了 → 交 `blind-map-finder` / `bin-to-a2l`。

> *依据 doctrine §13.1 逆向纪律（不许反向拟合刻度）与 §13.5（负面结论是终态）。
> `pya2ldb` 定位为「复杂 A2L 的参考/备用解析引擎」，不改变本技能「读定义、不碰字节、DAMOS 归自己」的主线。*

## 3. DAMOS 格式解析

### 3.1 WinOLS DAMOS CSV

```
No.;Description;Type;Address;Category;Column;Row;Factor;Offset;Unit;Note
1;KFZW - Ignition Map;MAP;0x123456;Ignition;12;16;0.75;-128;deg;Base spark
2;KFLD - Load Map;MAP;0x234567;Torque;8;12;0.1;0;%;Relative load
```

### 3.2 Bosch DAMOS ID 前缀速查

| 前缀 | 含义 |
|------|------|
| KFZW | 点火角 MAP |
| KFLD | 负荷 MAP |
| KFDL | Lambda 目标 |
| KFMIOP | 喷油脉谱 |
| KF_WG | WGDC MAP |
| KF_URWMX | WOT 扭矩限制 |
| KF_VTGTMX | 增压目标 |
| KF_DV_MMD | 驾驶员需求扭矩 |
| KFTLRS | 扭矩限制器 |
| KF_UMXW | 最大扭矩限制 |
| KF_GTRL | 变速箱扭矩限制 |
| KF_EGT | EGT 保护 |
| KF_CAT | CAT 保护 |
| KF_BASKF | 爆震控制基础 |
| KF_NVHRB | NVH 抑制 |
| NWINW | VVT 进气目标 |
| NWINA | VVT 排气目标 |
| LDRXN | 转速限制器 |
| DKORB | 加速加浓 |

---

## 4. 解析流程

### 4.0 A2L 文件优先用脚本解析（不要在上下文里手工扫全文）

A2L 动辄几千行、几百个 CHARACTERISTIC，手工解析必漏项。收到 `.a2l` 一律先跑：

```bash
python scripts/parse_a2l.py FILE.a2l                    # 摘要 + 全部 MAP 清单
python scripts/parse_a2l.py FILE.a2l --filter torque    # 按名称/描述/分组过滤
python scripts/parse_a2l.py FILE.a2l --map KFZW         # 单张 MAP 详情(轴/换算/布局)
python scripts/parse_a2l.py FILE.a2l --json maps.json   # 结构化输出给下游技能
python scripts/parse_a2l.py FILE.a2l --bin ORI.bin --base 0x800000  # raw 值域自检
python scripts/parse_a2l.py --selftest                  # 内置样例自测
```

脚本行为与本技能纪律一致：RAT_FUNC 仅纯线性时折算为 `phys = raw×factor + offset`
（WinOLS 约定），TAB_*/FORM 非线性标注"需人工确认"；给了 `--bin` 没给 `--base` 时列出
MEMORY_SEGMENT 候选基址但不擅自认定；raw 值域离谱 = 地址/基址错，不得照用。
输出 JSON 与 `winols-ols-parser/scripts/parse_ols.py` 同构（name/address/size_bytes/
raw_min/raw_max），下游技能一视同仁。DAMOS CSV/XML 等非 A2L 格式仍按下述流程人工解析。

### 4.1 用户提供文件时的流程

```
Step 1: 文件类型识别（扩展名 + 文件头 + 行数）
Step 2: ECU 平台识别（MOD_PAR 或 Description 列）
Step 3: 关键元数据提取
  ├── 所有 CHARACTERISTIC 列表（数量）
  ├── 所有 COMPU_METHOD 列表（换算方法）
  ├── 所有 AXIS_DESCR（轴定义）
  ├── 所有 MEASUREMENT（测量通道）
  └── 所有 FUNCTION（功能分组）
Step 4: 按类别筛选（用户关注的域）
Step 5: 输出结构化 MAP 清单
  MAP 名 | 地址 | 类型 | 行列数 | 单位 | 换算公式 | 功能分组 | 轴定义
Step 6: 针对单张 MAP 输出详细拆解 → ecu-map-analysis
```

### 4.2 无文件时的替代方案

1. 匹配 OEM 策略库 → oem-strategy-library
2. 从 DAMOS ID 反推功能
3. 指导用户在 WinOLS 中用不同数据类型扫描确定边界

---

## 5. 从 A2L 到工程文件的转换

### 5.1 Python 读表脚本模板

```python
MAPS = {
    "KFZW": {
        "address": 0x123456,
        "rows": 16,
        "cols": 12,
        "encoding": "int8",
        "factor": 0.75,
        "offset": -128,
        "unit": "deg BTDC",
        "x_axis": {"address": 0x345678, "count": 12, "encoding": "uint16"},
        "y_axis": {"address": 0x456789, "count": 16, "encoding": "uint16"}
    }
}
```

### 5.2 XDF 定义模板（TunerPro）

```xml
<XDFTABLE>
  <title>KFZW - 基础点火角</title>
  <xvar>RPM</xvar>
  <yvar>Load (%)</yvar>
  <zvar>Ignition Angle (deg BTDC)</zvar>
  <DIMENSIONS>12 16</DIMENSIONS>
  <ADDRESS>0x123456</ADDRESS>
  <STORAGETYPE>8 bit integer</STORAGETYPE>
  <MATH>phys = raw * 0.75 + (-128)</MATH>
  <UNITS>deg BTDC</UNITS>
</XDFTABLE>
```

---

## 6. 常见解析陷阱

| 陷阱 | 问题 | 解决方法 |
|------|------|---------|
| A2L 地址与 bin 偏移不一致 | 有内存映射偏移 | 减去 MEMORY_SEGMENT 基址 |
| COMPU_METHOD 系数歧义 | FORM a/b/c 解释不同 | 取已知 raw 值手工验证 |
| 共享轴映射错误 | 多个 MAP 引用同一轴 | 确认 INPUT_QUANTITY 地址 |
| A2L 版本不一致 | 来自不同 SW 版本 | 比较 SW_VERSION |
| 行列顺序混淆 | 与 WinOLS 显示可能相反 | 根据 AXIS_PTS_X/Y 判断 |
| DAMOS 地址格式 | 段地址 vs 线性 | 确认 WinOLS 地址映射 |

---

## 7. 与其他技能的关系

```
damos-a2l-parser
├── 结构化 MAP 定义 → ecu-map-analysis
├── MAP 列表+公式 → stage1-calibration-matrix
├── 地址+换算 → denso-bin-engine / bosch-conti-bin-engine
├── 换算验证 → winols-scaling-converter
├── 测量通道 → hp-tuners-log-review
├── OEM 策略补充 → oem-strategy-library
└── 所有结论经过 ecu-calibration-doctrine
```

## 8. 输出纪律

- 注明来源可靠性：直接提取 / 反推 / 推测
- 地址必须给出能否直接对应 bin 偏移的判断
- 换算公式必须手工验证至少一个已知点
- 不确定的换算必须标注需要交叉验证
- A2L 版本不确定或地址有偏移时警告不要直接写入

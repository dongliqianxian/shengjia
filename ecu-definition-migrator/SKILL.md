---
name: ecu-definition-migrator
description: >
  把已知软件版本的 A2L、OLS、KP 或标准化 JSON MAP 定义迁移到同 ECU/MCU 平台的新 BIN。结合匹配的
  源 BIN 与目标 BIN，使用精确字节、结构锚点、上下文、主 relocation delta（地址迁移量）和候选间距
  重定位 MAP，输出置信度分级的 JSON/CSV 新定义包、迁移报告和未解决清单。用于“旧 A2L 套新版本”、
  “OLS/KP MAP 转移到新 BIN”、“跨软件版本搬地址/定义”。没有源 BIN 或地址映射未确认时只做预检，
  不把近似候选当成可写入定义，也不伪造专有 `.kp/.ols`。
---

# ECU Definition Migrator

## 1. 任务边界

迁移的是 MAP **定义**：名称、目标地址/文件偏移、大小、维度、数据类型、字节序、factor、offset、单位和轴
元数据。默认不把旧版本标定值写进新 BIN。

运行前先用 `ecu-calibration-doctrine` 检查证据等级。所有结果遵循：

- `VERIFIED`：可进入新定义包，仍须 WinOLS 逐单元格复核。
- `CANDIDATE_HIGH/MEDIUM`：只能作为人工定位候选。
- `AMBIGUOUS/UNRESOLVED`：不得导入为确定地址。
- `NEEDS_SOURCE_BIN`：只有定义与新 BIN，缺少源值签名，不能自动重定位。

详细 schema 和评分逻辑见 `references/migration-schema.md`。

## 2. 输入门槛

最低输入：

1. 已知定义：`.a2l`、`.ols`、`.kp` 或解析器 JSON。
2. 同平台的新软件版本 BIN。

高置信迁移还必须提供：

3. 与已知定义完全匹配的源 BIN/ORI，或从 OLS 工程确认并导出的原始镜像。
4. A2L ECU 地址到源 BIN 文件偏移的 `--source-base`；目标需输出 ECU 地址时给 `--target-base`。
5. ECU/MCU、HW/SW/Cal ID、文件类型（full flash/cal-only/virtual read）。

只有 A2L + 新 BIN 时先输出预检，不用“相同平台”代替源签名。

## 3. 标准命令

### A2L → 新 BIN

```bash
python scripts/migrate_definitions.py \
  --definitions OLD.a2l \
  --source-bin OLD_ORI.bin --source-base 0x800000 \
  --target-bin NEW_ORI.bin --target-base 0x800000 \
  --out migration_NEW
```

### OLS/KP → 新 BIN

```bash
python scripts/migrate_definitions.py \
  --definitions OLD.ols \
  --source-bin OLD_ORI.bin \
  --target-bin NEW_ORI.bin \
  --out migration_NEW
```

OLS/KP 地址由启发式解析器产生，必须用源 BIN/WinOLS 属性确认；未确认时结果不能升为 `VERIFIED`。
确认源地址、尺寸和布局后，重跑时增加 `--source-definitions-verified`。

### 已解析 JSON

```bash
python scripts/migrate_definitions.py \
  --definitions old_maps.json --definitions-kind json \
  --source-bin OLD_ORI.bin --target-bin NEW_ORI.bin \
  --out migration_NEW
```

可用 `--a2l-parser` / `--ols-parser` 指定现有解析脚本位置。安装到 Codex 后默认寻找同级技能中的解析器。

## 4. 匹配顺序

1. 标准化定义并验证源地址、大小、数据类型和文件边界。
2. 从源 BIN 提取 Z 区字节和上下文；低熵、全零/全 FF 区不作为可靠签名。
3. 在目标 BIN 搜索唯一精确签名。
4. 从多个精确命中建立主 relocation delta；用它约束同族 MAP。
5. 对未精确命中项使用多段锚点、上下文、byte equality、u16 相关性和趋势相似度排序。
6. 检查候选间距、顺序、尺寸和地址簇；冲突时降级，不强行选一个。
7. 输出报告；人工在 WinOLS 校验轴、单位、逐单元格和邻表后再制作最终 KP/OLS。

## 5. 输出目录

- `migration_report.json`：完整证据、候选、分数、输入 SHA-256。
- `migration_report.csv`：所有 MAP 的状态与最佳目标地址。
- `migrated_verified.csv`：仅 Z 地址与外部轴都已验证的完整定义；共享 `AXIS_PTS` 也单独报告。
- `unresolved.csv`：候选冲突和缺失证据。

CSV 不是通用 WinOLS KP 格式。真正 `.kp/.ols` 属专有格式，必须在 WinOLS 中导入/建立并验证后导出；
不得通过改扩展名伪造。

## 6. 人工验收

每个 `VERIFIED` MAP 至少复核：

1. 目标地址/文件偏移与内存段映射。
2. 行列、数据宽度、字节序、signed/unsigned；共享轴必须独立迁移，嵌入轴必须验证 record layout。
3. factor、offset、单位和轴地址。
4. 逐单元格值与 WinOLS 显示一致，不只看 min/max。
5. 邻接 MAP、同族副本和调用逻辑合理。
6. 新软件版本没有拆表、合表、转置、压缩或改 record layout。

复核失败立即降级为候选。定义迁移完成不代表 BIN 可刷；checksum/签名是另一道交付闸门。

## 7. 路由

- A2L 解析：`damos-a2l-parser`。
- OLS/KP 候选提取：`winols-ols-parser`。
- raw↔工程值：`winols-scaling-converter`。
- 功能语义：`ecu-map-analysis`。
- 无签名/复杂重构：`ghidra-tricore-disasm` + `blind-map-finder`。
- 同族副本检查：`parallel-map-finder`。

## 8. 测试

```bash
python scripts/migrate_definitions.py --selftest
```

自测覆盖：统一地址平移、唯一精确匹配、近似候选、重复模式歧义和无源 BIN 降级。

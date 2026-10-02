# ECU / TCU 标定技能库（AI Agent Skills）

本仓库是 **47 个 ECU/TCU 标定技能** 的单一母本，供各类 AI 编程智能体共享调用。

## 结构

| 路径 | 内容 |
|---|---|
| `<技能名>/SKILL.md` | 技能定义（YAML frontmatter: name + description） |
| `<技能名>/references/` | 参考文档 |
| `<技能名>/scripts/` | 可执行脚本（Python 3，无外部依赖） |
| `_sync-tools/` | 跨智能体同步工具（非技能，无 SKILL.md） |

## 覆盖范围

建模层（air-fuel / torque / torque-reserve）· 控制层（closed-loop）· 方案层（stage1-calibration-matrix）·
单表分析（ecu-map-analysis / winols-* ）· 定位逆向（blind-map-finder / parallel-map-finder / dtc-locator /
speed-limiter-removal / idle-speed-calibration / start-stop-removal / toyota-pedal-tuner）·
定义解析（damos-a2l-parser / winols-ols-parser / bin-to-a2l / ecm-driver-to-a2l）·
bin 引擎（bosch-conti / denso / multi-vendor）· 校验（checksum-crc-engine）·
反汇编（ghidra-*）· 平台（edc17 / md1 / mg1 / dcm / powersports）· TCU（tcu-*）· 方法论（ecu-calibration-doctrine）

## 安装到本机

放到任一智能体会扫描的目录即可，例如：

    ~/.agents/skills      （DSH / Kimi 原生扫描，也是本仓库对应位置）
    ~/.claude/skills      （Claude Code）
    ~/.codex/skills       （Codex）

## 跨智能体同步

`_sync-tools/sync-ecu-skills.ps1` 用「单一母本 + Windows 目录联接(junction)」把 47 个技能
同时挂到 6 个 agent 目录，改一处处处生效。默认只读预演，`-Apply` 才执行：

    powershell -NoProfile -ExecutionPolicy Bypass -File .\_sync-tools\sync-ecu-skills.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\_sync-tools\sync-ecu-skills.ps1 -Apply

详见 `_sync-tools/README.md`。

## 说明

- 技能内容为工程方法论与可复现脚本；**不包含**任何 ECU 二进制文件、客户数据或标定工程文件。
- 使用前请遵守当地法律法规与排放法规；涉及排放系统删除的操作本仓库不予支持。
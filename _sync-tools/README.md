# ECU 技能跨智能体共享 —— 操作手册

> 目标：让 **47 个 ECU/TCU 技能** 在 **所有智能体**（Claude Code / Codex / Cursor / Gemini / Copilot / Trae / DSH / Kimi）
> 的用户级与项目级目录里，**始终是同一份内容**。改一次，处处生效。

---

## ✅ 本次执行记录（2026-10-02 09:41，已生效并验证）

| 项目 | 结果 |
|---|---|
| 母本 | `C:\Users\sheng\.agents\skills` |
| 已链接的根 | claude / codex / cursor / gemini / copilot / trae 各 **47 条 junction** |
| 验收 | 7 个根全部 **47/47** 可读 `SKILL.md`；跨根 MD5 **完全一致** |
| 写入穿透 | 母本写 1 次 → 6 个根同时读到 ✓ |
| 非 ECU 技能 | 原样保留（codex: `pdf`/`playwright`/`.system`；gemini/copilot 各 21 个；trae 6 个） |
| 母本提交 | `54524fd`（+4980 行，含 5 个新技能） |
| claude 提交 | `76b6b7a` |
| **备份** | `C:\Users\sheng\ecu-skill-backup-20261002-094133`（388 文件 / 3.7 MB） |
| 明细报告 | `report-20261002-094133.csv`（31 KB，逐文件动作记录） |

**回滚（本次）**：
```powershell
# 1) 删链接（只删 junction，母本无损）
'claude','codex','cursor','gemini','copilot','trae' | ForEach-Object {
  $p = "$env:USERPROFILE\.$_\skills"
  if ($_ -eq 'trae') { $p = "$env:USERPROFILE\.trae-cn\skills" }
  Get-ChildItem $p -Force -Directory | Where-Object LinkType | Remove-Item -Force
}
# 2) 移回原件
$bk = 'C:\Users\sheng\ecu-skill-backup-20261002-094133'
Move-Item "$bk\claude\*"  "$env:USERPROFILE\.claude\skills\"  -Force
Move-Item "$bk\codex\*"   "$env:USERPROFILE\.codex\skills\"   -Force
Move-Item "$bk\cursor\*"  "$env:USERPROFILE\.cursor\skills\"  -Force
```

**维持机制**：以后只在母本 `C:\Users\sheng\.agents\skills` 里改技能即可，6 个根自动同步。
需要新增 ECU 技能时，放进母本后重跑 `.\sync-ecu-skills.ps1 -Apply` 补链接。

---

## 0. 核心结论：不需要给任何智能体发指令

技能就是磁盘上的文件。每个智能体启动时扫自己约定的目录：

| 智能体侧 | 用户级扫描路径 | 项目级扫描路径 |
|---|---|---|
| DSH（桌面版） | `~/.agents/skills`、`~/.claude/skills`、`~/.codex/skills` | `<项目>/.dsh/skills`、`<项目>/.agents/skills` |
| Claude Code | `~/.claude/skills` | `<项目>/.claude/skills` |
| Codex | `~/.codex/skills` | 同左（以本机实测为准） |
| Cursor | `~/.cursor/skills` | — |
| Gemini / Copilot / Trae | `~/.gemini/skills`、`~/.copilot/skills`、`~/.trae-cn/skills` | — |

所以同步 = **把文件（或链接）放到这些路径上**，不是"通知智能体去学"。
唯一像"要发指令"的场景：新开会话让它重新扫目录（DSH 有文件监听，改完即生效，无需重启）。

---

## 1. 目标拓扑

```
母本（唯一真源）
  C:\Users\sheng\.agents\skills          ← 真实目录，git 仓库，DSH/Kimi 原生扫描
        ▲         ▲         ▲
        │         │         │            ← 每个技能一条 junction（目录联接）
  .claude/skills  .codex/skills  .cursor/skills  .gemini/skills  .copilot/skills  .trae-cn/skills
```

母本选 `.agents/skills` 的理由：它已被 DSH 原生扫描、被 Kimi 使用、且是 git 仓库（改动可追溯）。

---

## 2. 执行（三步）

### 步骤 0：先给母本上保险
```powershell
cd C:\Users\sheng\.agents\skills
git add -A
git commit -m "chore: snapshot before cross-agent skill sync"
```
> 本机这两个仓库都**没有 remote**。强烈建议加一个私有远程仓（GitHub/Gitee/内网 Git），
> 否则重装系统 / 换机 = 全丢，junction 也帮不了你。

### 步骤 1：预演（只读，不写任何东西）
```powershell
cd C:\Users\sheng\.dsh\ungrouped\ecu-skill-sync
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync-ecu-skills.ps1
```
预期输出：
```
动作                    数量
ADD-FILE               5      ← 各根独有的文件（如 codex 的 agents/openai.yaml、test_*.py）
ADD-SKILL              6      ← 母本缺的技能（5 个只在 codex 有：torque-reserve-analyzer 等）
CONFLICT-KEEP-MASTER  73      ← 其他根是旧版，保留母本（已验证：差异是 .agents 的后续修订）
LINK                 252      ← 计划建立的 junction 数
```

### 步骤 2：执行
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync-ecu-skills.ps1 -Apply
```
它会依次做四件事：
1. **合并**：把其他根独有的文件补进母本（只补缺失，不覆盖）
2. **备份**：被替换的每个原目录 → `~\ecu-skill-backup-<时间戳>\<根名>\<技能名>\`
3. **链接**：为 6 个 agent 用户级目录的每个 ECU 技能建 `mklink /J` 指向母本
4. **自检**：打印每个根的命中数 / 缺失数 / 仍是独立副本数

### 步骤 3：验证（成功判据）
```powershell
# 每个根都应显示 有ECU技能=47、仍是独立副本=0（母本 agents 除外）
Get-ChildItem C:\Users\sheng\.claude\skills -Force | Where-Object LinkType | Measure-Object
# 抽查一条链接指向
(Get-Item 'C:\Users\sheng\.codex\skills\ecu-calibration-doctrine' -Force).Target
```
真正的端到端验证：**在任意一个根里改一个技能，去另一个根读**——内容应立刻一致。

---

## 3. 项目级

项目级无法全局设置，只能按项目铺。**只在你真正要做 ECU 工作的项目里建**：
```powershell
.\sync-ecu-skills.ps1 -Apply -Projects 'C:\Users\sheng\Desktop\MyBMW-X5--B58-MG1CS003'
```
会在该项目下建 `<项目>\.claude\skills`、`<项目>\.agents\skills`、`<项目>\.dsh\skills` 三处，各 47 条 junction。

> 若只想给项目放**一部分**技能（比如只放本车平台相关的），把项目目录里的 junction 挑着删掉即可——
> 删的是链接，不影响母本。

---

## 4. 回滚（30 秒）

```powershell
# 1) 删掉链接（只删链接，母本毫发无损）
Get-ChildItem C:\Users\sheng\.claude\skills -Force -Directory |
  Where-Object LinkType | Remove-Item -Force

# 2) 把备份移回
Move-Item "$env:USERPROFILE\ecu-skill-backup-<时间戳>\claude\*" C:\Users\sheng\.claude\skills\
```
`Remove-Item` 删除 junction **不会**删除母本内容（这是 junction 与 symlink 都安全的地方；
但仍建议先确认 `LinkType` 非空再删）。

---

## 5. 必须知道的坑

1. **不要链接 `~/.dsh/skills`**。那是 DSH 桌面扩展自己的存储区（`state.json`、`repos/`），
   链过去会把它的状态文件写进母本。DSH 已经通过 `~/.agents/skills` 读到技能，无需额外处理。
2. **junction 只对本机同一 Windows 账户有效**。WSL、Docker、远程机器、另一台电脑都看不到链接——
   那些环境必须用 `git clone` + 同步脚本。这也是"必须加 remote"的原因。
3. **两个 git 仓库会"显示删除"**。`.claude/skills` 和 `.agents/skills` 是 git 仓库；把已跟踪的技能目录
   换成 junction 后，`git status` 可能显示大量删除。这是预期行为，提交一次即可：
   `git -C C:\Users\sheng\.claude\skills add -A; git commit -m "ECU skills junctioned to ~/.agents/skills"`。
4. **一改全改**。链接后 7 个智能体共用一份 frontmatter。DSH 校验严格（`name` 必须 kebab-case、
   `description` 必填），写错会让该技能**静默消失**——所以别在某个 agent 里单独手改 SKILL.md，改母本。
5. **权限**：脚本用 `mklink /J`（目录联接），**免管理员**。`/D`（符号链接）才需要开发者模式。
6. **执行策略**：用 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` 调用；
   直接双击/`.` 会因脚本未签名被拦。脚本文件必须保存为 **UTF-8 with BOM**（否则 PS 5.1 按 GBK 读，中文注释会解析失败）。
7. **同名优先级**：项目级 > 用户级。项目里若放同名技能，会盖住用户级那一份。

---

## 6. 文件清单

| 文件 | 作用 |
|---|---|
| `manifest.json` | 47 个 ECU 技能名单 + 母本路径 + 需要链接的根 + 项目级相对路径 |
| `sync-ecu-skills.ps1` | 同步器：预演 / 合并 / 备份 / 建链接 / 自检 |
| `README.md` | 本手册 |

<#
  sync-ecu-skills.ps1 —— 让「所有 ECU 技能」在「所有智能体」之间共享

  思路：单一母本(master) + 目录联接(junction)。
       母本 = C:\Users\sheng\.agents\skills（已被 DSH/Kimi 原生扫描，且是 git 仓）
       其余 agent 的 ECU 技能目录 → 指向母本的 junction（改一处，处处生效）

  默认只做「预演」，不写任何东西。确认无误后加 -Apply 执行。

  用法：
    .\sync-ecu-skills.ps1                                  # 预演(只读)：报告合并/冲突/链接计划
    .\sync-ecu-skills.ps1 -Apply                            # 执行：合并 → 备份 → 建 junction → 自检
    .\sync-ecu-skills.ps1 -Apply -Projects 'C:\path\proj1','C:\path\proj2'
                                                            # 额外给这些项目铺项目级技能

  安全设计：
    * 默认 DryRun，不写盘
    * 合并只「补缺失文件」；同名文件冲突时按 mtime 新者胜，并全部记入报告
    * 建链接前，被替换的原目录整体备份到 ~\ecu-skill-backup-<时间戳>\<根名>\<技能名>\
    * 只动 manifest.json 里列出的 47 个 ECU 技能；其他技能原样不动
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Master,
    [string[]]$Projects = @(),
    [string]$BackupRoot,
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ManifestPath = Join-Path $ScriptDir 'manifest.json'
if (-not (Test-Path $ManifestPath)) { throw "找不到清单: $ManifestPath" }
$cfg = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

$Master = if ($Master) { $Master } else { $cfg.master }
$ecuSkills = @($cfg.ecuSkills)
$userRoots = @($cfg.userRoots)
$projectRels = @($cfg.projectSkillDirs)

$md5 = [System.Security.Cryptography.MD5]::Create()

function Test-Ignored([string]$p) {
    return ($p -match '__pycache__' -or $p -match '\.pyc$' -or $p -match '\\\.git\\')
}
function Get-SkillFiles([string]$dir) {
    Get-ChildItem $dir -Recurse -File -Force | Where-Object { -not (Test-Ignored $_.FullName) }
}
function Get-NormalizedHash([string]$path) {
    $text = [System.IO.File]::ReadAllText($path) -replace "`r`n", "`n"
    return [System.BitConverter]::ToString($md5.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)))
}

$report = New-Object System.Collections.ArrayList
function Add-Report([string]$kind, [string]$skill, [string]$root, [string]$file) {
    [void]$report.Add([pscustomobject]@{ Kind = $kind; Skill = $skill; Root = $root; File = $file })
}

if (-not (Test-Path $Master)) { throw "母本不存在: $Master" }
$mode = if ($Apply) { 'APPLY（会写盘）' } else { 'DRY-RUN（只读预演）' }
Write-Host "=== ECU 技能同步 ===" -ForegroundColor Cyan
Write-Host "模式   : $mode"
Write-Host "母本   : $Master"
Write-Host "技能数 : $($ecuSkills.Count)"
Write-Host ""

# ---------- 阶段 1：把各根独有的内容并入母本 ----------
Write-Host "[1/4] 合并各根内容到母本…" -ForegroundColor Yellow
foreach ($r in $userRoots) {
    if ($r.name -eq 'agents') { continue }              # 母本自己跳过
    if (-not (Test-Path $r.path)) { continue }
    foreach ($s in $ecuSkills) {
        $src = Join-Path $r.path $s
        if (-not (Test-Path (Join-Path $src 'SKILL.md'))) { continue }
        $dst = Join-Path $Master $s
        if (-not (Test-Path $dst)) {
            if ($Apply) { Copy-Item $src $dst -Recurse -Force }
            Add-Report 'ADD-SKILL' $s $r.name '*'
            continue
        }
        foreach ($f in (Get-SkillFiles $src)) {
            $rel = $f.FullName.Substring($src.Length)
            $target = Join-Path $dst $rel
            if (-not (Test-Path $target)) {
                if ($Apply) {
                    New-Item -ItemType Directory (Split-Path $target) -Force | Out-Null
                    Copy-Item $f.FullName $target -Force
                }
                Add-Report 'ADD-FILE' $s $r.name $rel
            }
            elseif ((Get-NormalizedHash $f.FullName) -ne (Get-NormalizedHash $target)) {
                if ($f.LastWriteTime -gt (Get-Item $target).LastWriteTime) {
                    if ($Apply) { Copy-Item $f.FullName $target -Force }
                    Add-Report 'UPDATE-FROM' $s $r.name $rel
                }
                else {
                    Add-Report 'CONFLICT-KEEP-MASTER' $s $r.name $rel
                }
            }
        }
    }
}

# ---------- 阶段 2：备份 + 建 junction ----------
Write-Host "[2/4] 备份并建立 junction…" -ForegroundColor Yellow
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $BackupRoot) { $BackupRoot = Join-Path $env:USERPROFILE "ecu-skill-backup-$stamp" }

$linkedRoots = @($userRoots | Where-Object { $_.link })
foreach ($r in $linkedRoots) {
    if (-not (Test-Path $r.path)) {
        if ($Apply) { New-Item -ItemType Directory $r.path -Force | Out-Null }
        else { Write-Host ("  (将新建根目录) " + $r.path) }
    }
    foreach ($s in $ecuSkills) {
        $cur = Join-Path $r.path $s
        $masterSkill = Join-Path $Master $s
        if (-not (Test-Path $masterSkill)) { continue }
        if (Test-Path $cur) {
            $item = Get-Item $cur -Force
            if ($item.LinkType) { Add-Report 'SKIP-ALREADY-LINK' $s $r.name ''; continue }
            if ($Apply) {
                $bkSkill = Join-Path (Join-Path $BackupRoot $r.name) $s
                New-Item -ItemType Directory (Split-Path $bkSkill) -Force | Out-Null
                Move-Item $cur $bkSkill -Force
            }
        }
        if ($Apply) {
            cmd /c mklink /J "$cur" "$masterSkill" | Out-Null
            if (-not (Test-Path $cur)) { Add-Report 'LINK-FAILED' $s $r.name ''; continue }
        }
        Add-Report 'LINK' $s $r.name $masterSkill
    }
}

# ---------- 阶段 3：项目级 ----------
Write-Host "[3/4] 项目级技能目录…" -ForegroundColor Yellow
foreach ($p in $Projects) {
    if (-not (Test-Path $p)) { Write-Host "  跳过(项目不存在): $p" -ForegroundColor Red; continue }
    foreach ($rel in $projectRels) {
        $root = Join-Path $p $rel
        foreach ($s in $ecuSkills) {
            $cur = Join-Path $root $s
            $masterSkill = Join-Path $Master $s
            if (-not (Test-Path $masterSkill)) { continue }
            if (Test-Path $cur) {
                $item = Get-Item $cur -Force
                if ($item.LinkType) { continue }
                if ($Apply) {
                    $bkSkill = Join-Path (Join-Path (Join-Path $BackupRoot "project-$([IO.Path]::GetFileName($p))") $rel) $s
                    New-Item -ItemType Directory (Split-Path $bkSkill) -Force | Out-Null
                    Move-Item $cur $bkSkill -Force
                }
            }
            elseif ($Apply) {
                New-Item -ItemType Directory $root -Force | Out-Null
            }
            if ($Apply) { cmd /c mklink /J "$cur" "$masterSkill" | Out-Null }
            Add-Report 'LINK-PROJECT' $s "$p|$rel" $masterSkill
        }
    }
}

# ---------- 阶段 4：自检 ----------
Write-Host "[4/4] 自检…" -ForegroundColor Yellow
$verify = foreach ($r in $userRoots) {
    $has = 0; $missing = 0; $real = 0
    foreach ($s in $ecuSkills) {
        $cur = Join-Path $r.path $s
        if (Test-Path $cur) {
            $has++
            $item = Get-Item $cur -Force
            if ($r.name -ne 'agents' -and -not $item.LinkType) { $real++ }
        }
        else { $missing++ }
    }
    [pscustomobject]@{ root = $r.name; 路径 = $r.path; 有ECU技能 = $has; 缺 = $missing; 仍是独立副本 = $real }
}

$summary = $report | Group-Object Kind | Sort-Object Name | ForEach-Object {
    [pscustomobject]@{ 动作 = $_.Name; 数量 = $_.Count }
}

Write-Host ""
Write-Host "--- 动作统计 ---" -ForegroundColor Cyan
$summary | Format-Table -AutoSize
Write-Host "--- 各根自检（APPLY 后『仍是独立副本』应为 0，母本 agents 除外）---" -ForegroundColor Cyan
$verify | Format-Table -AutoSize

if (-not $Apply) {
    Write-Host "这是预演，没有任何写入。确认后执行：" -ForegroundColor Green
    Write-Host "  .\sync-ecu-skills.ps1 -Apply" -ForegroundColor Green
}
else {
    Write-Host "备份位置: $BackupRoot" -ForegroundColor Green
    Write-Host "回滚: 删除 junction 后把备份目录移回原名即可（见 README.md 回滚章节）" -ForegroundColor Green
}

$out = if ($ReportPath) { $ReportPath } else { Join-Path $ScriptDir ("report-{0}.csv" -f $stamp) }
if ($Apply) { $report | Export-Csv -Path $out -NoTypeInformation -Encoding UTF8; Write-Host "明细报告: $out" }
else { $report | Format-Table -AutoSize }

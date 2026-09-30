#!/usr/bin/env python3
"""Validate non-negotiable Bosch ME7 semantics in this skill."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    skill_path = root / "SKILL.md"
    reference_path = root / "references" / "bosch-me7-torque-chain.md"

    errors: list[str] = []
    if not skill_path.is_file():
        errors.append(f"missing {skill_path}")
        skill_text = ""
    else:
        skill_text = skill_path.read_text(encoding="utf-8")

    if not reference_path.is_file():
        errors.append(f"missing {reference_path}")
        reference_text = ""
    else:
        reference_text = reference_path.read_text(encoding="utf-8")

    required_skill = [
        "| `LDRXN` | 常见为最大允许/请求负荷随转速 | 不是通用转速限制器 |",
        "references/bosch-me7-torque-chain.md",
    ]
    forbidden_skill = [
        "| LDRXN | 转速限制器 | RPM Limiter |",
        "| 转速限制 | LDRXN |",
        "LDRXN — 转速限制",
    ]
    required_reference = [
        "KFPED",
        "KFMIRL",
        "LDRXN",
        "KFMIOP",
        "KFZWOP",
        "功能反函数",
        "rlsol",
        "rl_w",
        "mibas",
        "mimax",
        "zwsol",
        "MODEL_NO_EDIT",
        "LOG_FIRST",
        "REJECT_MONITOR_DISABLE",
    ]

    for token in required_skill:
        if token not in skill_text:
            errors.append(f"SKILL.md missing required token: {token}")
    for token in forbidden_skill:
        if token in skill_text:
            errors.append(f"SKILL.md contains forbidden ME7 claim: {token}")
    for token in required_reference:
        if token not in reference_text:
            errors.append(f"ME7 reference missing required token: {token}")

    if "KFMIZUOF" in reference_text and "REJECT_MONITOR_DISABLE" not in reference_text:
        errors.append("monitor maps are mentioned without the disable/reject boundary")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print(f"PASS: Bosch ME7 semantics validated in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

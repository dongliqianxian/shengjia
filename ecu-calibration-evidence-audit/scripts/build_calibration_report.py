#!/usr/bin/env python3
"""Render a user-facing calibration audit report from deterministic evidence artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "calibration-audit-template.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_No records available._"
    lines = [
        "| " + " | ".join(md(header) for header in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md(value) for value in row) + " |")
    return "\n".join(lines)


def load_ledger(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def render_identity(manifest: dict) -> str:
    rows: list[list[Any]] = []
    metadata = manifest.get("metadata", {})
    for key, value in sorted(metadata.items()):
        rows.append([key, value])
    rows.extend(
        [
            ["case_dir", manifest.get("case_dir", "")],
            ["manifest_schema", manifest.get("schema_version", "")],
        ]
    )
    firmware = [record for record in manifest.get("files", []) if record.get("kind") == "firmware"]
    for index, record in enumerate(firmware, start=1):
        prefix = f"firmware_{index}"
        rows.append([f"{prefix}_name", record.get("name", "")])
        rows.append([f"{prefix}_role", record.get("role", "")])
        rows.append([f"{prefix}_size", record.get("size", "")])
        rows.append([f"{prefix}_sha256", record.get("sha256", "")])
    return markdown_table(["Field", "Value"], rows)


def render_inventory(manifest: dict) -> str:
    rows = [
        [
            record.get("name", ""),
            record.get("kind", ""),
            record.get("role", ""),
            record.get("size", ""),
            record.get("sha256", ""),
        ]
        for record in manifest.get("files", [])
    ]
    return markdown_table(["File", "Kind", "Role", "Bytes", "SHA-256"], rows)


def render_evidence(rows: list[dict[str, str]]) -> str:
    table_rows = []
    for row in rows:
        address = row.get("descriptor_start") or row.get("data_start") or ""
        table_rows.append(
            [
                row.get("label", ""),
                address,
                row.get("dimensions", ""),
                row.get("role", ""),
                row.get("code_status", ""),
                row.get("semantic_grade", ""),
                row.get("edit_ready", "false"),
                row.get("edit_blockers", ""),
            ]
        )
    return markdown_table(
        ["MAP/target", "Address", "Dimensions", "Role", "Code status", "Semantic", "Edit ready", "Blockers"],
        table_rows,
    )


def render_decision(decision: dict) -> str:
    blockers = decision.get("case_blockers", [])
    blocker_text = ", ".join(blockers) if blockers else "None"
    return "\n".join(
        [
            f"**Decision:** `{decision.get('decision', 'UNKNOWN')}`",
            "",
            f"**Action:** {decision.get('action', '')}",
            "",
            f"**Case blockers:** {blocker_text}",
            "",
            f"**Edit-ready rows:** {decision.get('edit_ready_count', 0)} / {decision.get('row_count', 0)}",
            "",
            "**Flash ready:** `false` — platform-specific checksum/signature and write-path verification remain separate.",
        ]
    )


def render_contradictions(manifest: dict, rows: list[dict[str, str]], decision: dict) -> str:
    items: list[str] = []
    for warning in manifest.get("warnings", []):
        items.append(f"Manifest warning: {warning}")
    for blocker in decision.get("case_blockers", []):
        items.append(f"Case blocker: {blocker}")
    for row in rows:
        contradiction = row.get("contradictions", "").strip()
        if contradiction:
            items.append(f"{row.get('label', 'unnamed')}: {contradiction}")
    if not items:
        return "_No contradiction or manifest warning was recorded._"
    return "\n".join(f"- {md(item)}" for item in dict.fromkeys(items))


def render_next_steps(rows: list[dict[str, str]], decision: dict) -> str:
    steps: list[str] = []
    for row in rows:
        item = row.get("next_validation", "").strip()
        if item:
            steps.append(f"{row.get('label', 'unnamed')}: {item}")
    blocker_actions = {
        "case_manifest_not_supplied": "Create and verify case_manifest.json.",
        "firmware_missing": "Supply the bounded firmware file and confirm identity.",
        "confirmed_ori_missing": "Identify and preserve a confirmed ORI/baseline file.",
        "evidence_ledger_empty": "Add definition, address-family, XREF, or manual map evidence.",
        "unresolved_contradictions": "Run the minimum discriminating test before assigning a write-ready name.",
    }
    for blocker in decision.get("case_blockers", []):
        steps.append(blocker_actions.get(blocker, f"Resolve case blocker: {blocker}."))
    if not steps:
        steps.append("Route edit-ready rows to the matching BIN engine and platform integrity workflow.")
    return "\n".join(f"{index}. {md(step)}" for index, step in enumerate(dict.fromkeys(steps), start=1))


def render_risk_checklist() -> str:
    return "\n".join(
        [
            "- [ ] Preserve the confirmed ORI and create a new output filename.",
            "- [ ] Confirm address, length, dtype, byte order, factor, offset, axes, units, target cells, and bounds.",
            "- [ ] Confirm active mode/gear/variant and linked forward/inverse/monitor models.",
            "- [ ] Review ECU-TCU torque handshake, gear limits, and TCC/clutch slip.",
            "- [ ] Validate air model, lambda tracking, fuel pressure/injector use, spark, knock, and temperatures.",
            "- [ ] Preserve diagnostic, torque-monitoring, knock, thermal, overspeed, pressure, clutch, and emissions protections.",
            "- [ ] Record ORI/MOD diff, LOG comparison, rollback conditions, and integrity-tool result.",
            "- [ ] Do not label the file flash-ready until platform checksum/signature and write-path checks pass.",
        ]
    )


def build_report(
    manifest: dict,
    rows: list[dict[str, str]],
    decision: dict,
    control_chain: str,
    title: str,
    template: str,
) -> str:
    replacements = {
        "{{TITLE}}": title,
        "{{GENERATED_UTC}}": utc_now(),
        "{{WRITE_DECISION}}": render_decision(decision),
        "{{IDENTITY_TABLE}}": render_identity(manifest),
        "{{INVENTORY_TABLE}}": render_inventory(manifest),
        "{{EVIDENCE_TABLE}}": render_evidence(rows),
        "{{CONTROL_CHAIN}}": control_chain.strip() or "_No control-chain artifact was supplied._",
        "{{CONTRADICTIONS}}": render_contradictions(manifest, rows, decision),
        "{{NEXT_STEPS}}": render_next_steps(rows, decision),
        "{{RISK_CHECKLIST}}": render_risk_checklist(),
    }
    report = template
    for token, value in replacements.items():
        report = report.replace(token, value)
    remaining = [token for token in replacements if token in report]
    if remaining:
        raise ValueError(f"Unresolved report template tokens: {remaining}")
    return report.rstrip() + "\n"


def run_selftest() -> int:
    with tempfile.TemporaryDirectory() as temp_name:
        root = Path(temp_name)
        manifest = {
            "schema_version": "1.0",
            "case_dir": str(root),
            "metadata": {"cal_id": "SELFTEST"},
            "files": [
                {"name": "ECU_ORI.bin", "kind": "firmware", "role": "ori", "size": 3, "sha256": "abc"}
            ],
            "warnings": [],
        }
        rows = [
            {
                "label": "map1",
                "descriptor_start": "0x1000",
                "dimensions": "16x16",
                "role": "driver_demand",
                "code_status": "VERIFIED_CONSUMER",
                "semantic_grade": "B+",
                "edit_ready": "false",
                "edit_blockers": "active_variant_confirmed;log_validated",
                "next_validation": "Run mode correlation LOG",
                "contradictions": "",
            }
        ]
        decision = {"decision": "LOG_FIRST", "action": "Log first.", "case_blockers": [], "edit_ready_count": 0, "row_count": 1}
        template = DEFAULT_TEMPLATE.read_text(encoding="utf-8")
        report = build_report(manifest, rows, decision, "verified -> inferred", "Selftest Audit", template)
        assert "Selftest Audit" in report
        assert "LOG_FIRST" in report
        assert "map1" in report
        assert "{{" not in report
    print("build_calibration_report selftest: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--decision", type=Path)
    parser.add_argument("--control-chain", type=Path)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--title", default="ECU/TCU Calibration Evidence Audit")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()
    for name in ("manifest", "ledger", "decision", "out"):
        if getattr(args, name) is None:
            parser.error(f"Provide --{name.replace('_', '-')}")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    rows = load_ledger(args.ledger)
    decision = json.loads(args.decision.read_text(encoding="utf-8-sig"))
    control_chain = args.control_chain.read_text(encoding="utf-8-sig") if args.control_chain else ""
    template = args.template.read_text(encoding="utf-8")
    report = build_report(manifest, rows, decision, control_chain, args.title, template)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Merge manual calibration evidence and Ghidra XREF artifacts into a gated ledger."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path
from typing import Any


CODE_RANK = {
    "UNRESOLVED": 0,
    "INDIRECT_REFERENCE": 1,
    "VERIFIED_XREF": 2,
    "VERIFIED_LOOKUP": 3,
    "VERIFIED_TRANSFORM": 4,
    "VERIFIED_CONSUMER": 5,
}
SEMANTIC_GRADES = {"A", "B+", "B", "C"}
BOOL_FIELDS = [
    "trusted_definition",
    "address_confirmed",
    "layout_confirmed",
    "dtype_confirmed",
    "byte_order_confirmed",
    "factor_confirmed",
    "offset_confirmed",
    "axes_confirmed",
    "units_confirmed",
    "active_variant_confirmed",
    "target_cells_confirmed",
    "bounds_confirmed",
    "linked_model_reviewed",
    "log_validated",
]
WRITE_GATE_FIELDS = [
    "address_confirmed",
    "layout_confirmed",
    "dtype_confirmed",
    "byte_order_confirmed",
    "factor_confirmed",
    "offset_confirmed",
    "axes_confirmed",
    "units_confirmed",
    "active_variant_confirmed",
    "target_cells_confirmed",
    "bounds_confirmed",
    "linked_model_reviewed",
    "log_validated",
]
FIELDNAMES = [
    "label",
    "descriptor_start",
    "data_start",
    "range_end",
    "dimensions",
    "hypothesis",
    "role",
    "code_status",
    "semantic_grade",
    "exact_oem_name",
    "direct_xrefs",
    "text_hits",
    *BOOL_FIELDS,
    "contradictions",
    "next_validation",
    "notes",
    "edit_ready",
    "edit_blockers",
]


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "y", "1"}:
        return True
    if normalized in {"false", "no", "n", "0"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def normalize_address(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return ""
    text = str(value).strip()
    try:
        return f"0x{int(text, 0):X}"
    except ValueError:
        try:
            return f"0x{int(text, 16):X}"
        except ValueError:
            return text


def empty_row(label: str) -> dict[str, Any]:
    row: dict[str, Any] = {field: "" for field in FIELDNAMES}
    row["label"] = label
    row["code_status"] = "UNRESOLVED"
    row["semantic_grade"] = "C"
    row["exact_oem_name"] = "UNRESOLVED"
    row["direct_xrefs"] = 0
    row["text_hits"] = 0
    for field in BOOL_FIELDS:
        row[field] = False
    row["edit_ready"] = False
    return row


def load_manual(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = payload.get("maps", [])
        if not isinstance(payload, list):
            raise ValueError(f"Manual JSON must be an array or contain maps[]: {path}")
        return [dict(item) for item in payload]

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
        return [dict(item) for item in csv.DictReader(handle, dialect=dialect)]


def xref_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows: list[dict[str, Any]] = []
    for target in payload.get("targets", []):
        label = str(target.get("label", "")).strip()
        if not label:
            continue
        xrefs = target.get("xrefs", []) or []
        text_hits = target.get("text_hits", []) or []
        if xrefs:
            status = "VERIFIED_XREF"
        elif text_hits:
            status = "INDIRECT_REFERENCE"
        else:
            status = "UNRESOLVED"
        row = empty_row(label)
        start = normalize_address(target.get("start"))
        if "data" in label.lower() or label.lower().startswith("z_"):
            row["data_start"] = start
        else:
            row["descriptor_start"] = start
        row["range_end"] = normalize_address(target.get("end"))
        row["code_status"] = status
        row["direct_xrefs"] = len(xrefs)
        row["text_hits"] = len(text_hits)
        row["address_confirmed"] = bool(xrefs or text_hits)
        row["notes"] = f"Imported from {path.name}"
        rows.append(row)
    return rows


def merge_text(left: Any, right: Any) -> str:
    values = []
    for value in (left, right):
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)
    return "; ".join(values)


def merge_row(base: dict[str, Any], incoming: dict[str, Any], manual: bool) -> dict[str, Any]:
    result = dict(base)
    for address_field in ("descriptor_start", "data_start", "range_end"):
        incoming_value = normalize_address(incoming.get(address_field))
        current_value = normalize_address(result.get(address_field))
        if incoming_value and current_value and incoming_value != current_value:
            result["contradictions"] = merge_text(
                result.get("contradictions"),
                f"{address_field} conflict: {current_value} vs {incoming_value}",
            )
        if incoming_value:
            result[address_field] = incoming_value

    incoming_status = str(incoming.get("code_status", "")).strip().upper()
    if incoming_status:
        if incoming_status not in CODE_RANK:
            result["contradictions"] = merge_text(result.get("contradictions"), f"Invalid code_status: {incoming_status}")
        elif CODE_RANK[incoming_status] >= CODE_RANK.get(str(result.get("code_status")), 0):
            result["code_status"] = incoming_status

    incoming_grade = str(incoming.get("semantic_grade", "")).strip().upper()
    if incoming_grade:
        if incoming_grade not in SEMANTIC_GRADES:
            result["contradictions"] = merge_text(result.get("contradictions"), f"Invalid semantic_grade: {incoming_grade}")
        elif manual:
            result["semantic_grade"] = incoming_grade

    for field in BOOL_FIELDS:
        if field in incoming and incoming[field] not in (None, ""):
            result[field] = parse_bool(incoming[field])

    for count_field in ("direct_xrefs", "text_hits"):
        if incoming.get(count_field) not in (None, ""):
            result[count_field] = max(int(result.get(count_field) or 0), int(incoming[count_field]))

    for field in (
        "dimensions",
        "hypothesis",
        "role",
        "exact_oem_name",
        "next_validation",
    ):
        value = str(incoming.get(field, "")).strip()
        if value and (manual or not result.get(field)):
            result[field] = value

    result["contradictions"] = merge_text(result.get("contradictions"), incoming.get("contradictions"))
    result["notes"] = merge_text(result.get("notes"), incoming.get("notes"))
    return result


def calculate_row_gate(row: dict[str, Any], has_confirmed_ori: bool) -> dict[str, Any]:
    blockers: list[str] = []
    if not has_confirmed_ori:
        blockers.append("confirmed_ori")
    if row.get("semantic_grade") != "A":
        blockers.append("semantic_grade_A")
    if row.get("code_status") == "UNRESOLVED" and not row.get("trusted_definition"):
        blockers.append("verified_code_or_trusted_definition")
    for field in WRITE_GATE_FIELDS:
        if not row.get(field):
            blockers.append(field)
    if str(row.get("contradictions", "")).strip():
        blockers.append("resolve_contradictions")
    row["edit_ready"] = not blockers
    row["edit_blockers"] = ";".join(blockers)
    return row


def build_ledger(manifest: dict | None, manual_paths: list[Path], xref_paths: list[Path]) -> tuple[list[dict[str, Any]], dict]:
    rows_by_label: dict[str, dict[str, Any]] = {}
    for path in xref_paths:
        for incoming in xref_rows(path):
            label = incoming["label"]
            rows_by_label[label] = merge_row(rows_by_label.get(label, empty_row(label)), incoming, manual=False)

    for path in manual_paths:
        for incoming in load_manual(path):
            label = str(incoming.get("label", "")).strip()
            if not label:
                raise ValueError(f"Manual evidence row is missing label: {path}")
            rows_by_label[label] = merge_row(rows_by_label.get(label, empty_row(label)), incoming, manual=True)

    has_manifest = manifest is not None
    has_firmware = bool(manifest and manifest.get("gates", {}).get("has_firmware"))
    has_confirmed_ori = bool(manifest and manifest.get("gates", {}).get("has_confirmed_ori"))
    rows = [calculate_row_gate(row, has_confirmed_ori) for _, row in sorted(rows_by_label.items())]

    case_blockers: list[str] = []
    if not has_manifest:
        case_blockers.append("case_manifest_not_supplied")
    elif not has_firmware:
        case_blockers.append("firmware_missing")
    if has_manifest and not has_confirmed_ori:
        case_blockers.append("confirmed_ori_missing")
    if not rows:
        case_blockers.append("evidence_ledger_empty")
    if any(str(row.get("contradictions", "")).strip() for row in rows):
        case_blockers.append("unresolved_contradictions")

    edit_ready_count = sum(bool(row["edit_ready"]) for row in rows)
    encoding_fields = [
        "address_confirmed",
        "layout_confirmed",
        "dtype_confirmed",
        "byte_order_confirmed",
        "factor_confirmed",
        "offset_confirmed",
        "axes_confirmed",
        "units_confirmed",
    ]
    encoding_ready = any(all(bool(row.get(field)) for field in encoding_fields) for row in rows)

    if case_blockers:
        decision = "NO_EDIT"
    elif edit_ready_count:
        decision = "EDIT_CANDIDATE"
    elif encoding_ready:
        decision = "LOG_FIRST"
    else:
        decision = "INTERPRETATION_ONLY"

    blocker_frequency: dict[str, int] = {}
    for row in rows:
        for blocker in str(row.get("edit_blockers", "")).split(";"):
            if blocker:
                blocker_frequency[blocker] = blocker_frequency.get(blocker, 0) + 1

    actions = {
        "NO_EDIT": "Resolve case-level blockers and contradictions before any calibration write.",
        "INTERPRETATION_ONLY": "Continue definition, scaling, and XREF work; use results for naming only.",
        "LOG_FIRST": "Confirm active variants, limiter/model response, and vehicle behavior with a baseline LOG.",
        "EDIT_CANDIDATE": "Route verified rows to the platform BIN engine and integrity tool chain; preserve ORI.",
    }
    decision_payload = {
        "schema_version": "1.0",
        "decision": decision,
        "action": actions[decision],
        "case_blockers": case_blockers,
        "row_count": len(rows),
        "edit_ready_count": edit_ready_count,
        "blocker_frequency": blocker_frequency,
        "flash_ready": False,
        "flash_ready_note": "This orchestrator never declares flash readiness without platform-specific checksum/signature and write-path verification.",
    }
    return rows, decision_payload


def csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, "")) for field in FIELDNAMES})


def run_selftest() -> int:
    with tempfile.TemporaryDirectory() as temp_name:
        root = Path(temp_name)
        manifest = {"gates": {"has_firmware": True, "has_confirmed_ori": True}}
        xref = {
            "targets": [
                {"label": "driver_demand", "start": "0003e104", "end": "0003e147", "xrefs": [{"from": "1"}], "text_hits": []}
            ]
        }
        manual = {
            "maps": [
                {
                    "label": "driver_demand",
                    "descriptor_start": "0x3E104",
                    "code_status": "VERIFIED_CONSUMER",
                    "semantic_grade": "B+",
                    **{field: True for field in WRITE_GATE_FIELDS[:8]},
                    "linked_model_reviewed": True,
                }
            ]
        }
        xref_path = root / "xref.json"
        manual_path = root / "manual.json"
        xref_path.write_text(json.dumps(xref), encoding="utf-8")
        manual_path.write_text(json.dumps(manual), encoding="utf-8")
        rows, decision = build_ledger(manifest, [manual_path], [xref_path])
        assert rows[0]["code_status"] == "VERIFIED_CONSUMER"
        assert decision["decision"] == "LOG_FIRST"

        manual["maps"][0].update(
            {
                "semantic_grade": "A",
                "active_variant_confirmed": True,
                "target_cells_confirmed": True,
                "bounds_confirmed": True,
                "log_validated": True,
            }
        )
        manual_path.write_text(json.dumps(manual), encoding="utf-8")
        rows, decision = build_ledger(manifest, [manual_path], [xref_path])
        assert rows[0]["edit_ready"]
        assert decision["decision"] == "EDIT_CANDIDATE"
    print("build_evidence_ledger selftest: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manual", action="append", default=[], type=Path, help="Manual evidence JSON/CSV; repeatable")
    parser.add_argument("--xref", action="append", default=[], type=Path, help="Ghidra xref_evidence.json; repeatable")
    parser.add_argument("--out-csv", type=Path)
    parser.add_argument("--decision-out", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()
    if not args.manual and not args.xref:
        parser.error("Provide at least one --manual or --xref input")
    if args.out_csv is None or args.decision_out is None:
        parser.error("Provide --out-csv and --decision-out")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig")) if args.manifest else None
    rows, decision = build_ledger(manifest, args.manual, args.xref)
    write_ledger(args.out_csv, rows)
    args.decision_out.parent.mkdir(parents=True, exist_ok=True)
    args.decision_out.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out_csv}")
    print(f"Wrote {args.decision_out}: {decision['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

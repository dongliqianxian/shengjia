#!/usr/bin/env python3
"""Create a deterministic ECU/TCU case manifest without interpreting calibration semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFINITION_EXTENSIONS = {".a2l", ".damos", ".dam", ".ols", ".kp", ".xdf"}
FIRMWARE_EXTENSIONS = {".bin", ".ori", ".mod", ".hex", ".s19", ".srec"}
LOG_EXTENSIONS = {".hpl", ".hpt", ".tdms", ".mf4", ".blf", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
SKIP_DIRS = {".git", ".agents", "__pycache__", "ghidra_projects", "skill-build", "outputs"}
ORI_TOKENS = {"ori", "original", "stock", "factory", "baseline"}
MOD_TOKENS = {"mod", "modified", "tuned", "stage1", "stage2", "stage3", "stg1", "stg2", "stg3"}
LOG_TOKENS = {"log", "scan", "obd", "hpt", "hptuners", "datalog"}
ANALYSIS_TOKENS = {"ghidra", "xref", "decompile", "disasm", "script", "audit", "analysis"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def name_tokens(path: Path) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", path.stem.lower()) if token}


def classify(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    tokens = name_tokens(path)

    if ext in FIRMWARE_EXTENSIONS:
        if ext == ".ori" or tokens & ORI_TOKENS:
            return "firmware", "ori"
        if ext == ".mod" or tokens & MOD_TOKENS:
            return "firmware", "modified"
        return "firmware", "unconfirmed"

    if ext in DEFINITION_EXTENSIONS:
        return "definition", ext.lstrip(".")

    if ext == ".csv":
        if tokens & ANALYSIS_TOKENS:
            return "analysis_artifact", "csv"
        if tokens & LOG_TOKENS:
            return "log", "csv"
        return "definition", "csv_unconfirmed"

    if ext in LOG_EXTENSIONS:
        if tokens & ANALYSIS_TOKENS:
            return "analysis_artifact", ext.lstrip(".")
        return "log", ext.lstrip(".")

    if ext in IMAGE_EXTENSIONS:
        return "image", ext.lstrip(".")

    if ext in {".md", ".txt", ".pdf", ".doc", ".docx", ".json"}:
        return "document", ext.lstrip(".")

    return "other", ext.lstrip(".") or "no_extension"


def iter_case_files(case_dir: Path, recursive: bool) -> Iterable[Path]:
    iterator = case_dir.rglob("*") if recursive else case_dir.iterdir()
    for path in iterator:
        if not path.is_file():
            continue
        try:
            relative_parts = path.relative_to(case_dir).parts[:-1]
        except ValueError:
            relative_parts = ()
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        yield path


def parse_metadata(items: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Metadata must use key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Metadata key is empty: {item}")
        metadata[key] = value.strip()
    return metadata


def build_manifest(case_dir: Path | None, extra_files: list[Path], recursive: bool, metadata: dict[str, str]) -> dict:
    candidates: list[Path] = []
    if case_dir is not None:
        if not case_dir.is_dir():
            raise FileNotFoundError(f"Case directory not found: {case_dir}")
        candidates.extend(iter_case_files(case_dir, recursive))
    candidates.extend(extra_files)

    unique: dict[str, Path] = {}
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Input file not found: {candidate}")
        unique[str(resolved).lower()] = resolved

    records: list[dict] = []
    for path in sorted(unique.values(), key=lambda value: str(value).lower()):
        kind, role = classify(path)
        stat = path.stat()
        records.append(
            {
                "name": path.name,
                "path": str(path),
                "kind": kind,
                "role": role,
                "extension": path.suffix.lower(),
                "size": stat.st_size,
                "sha256": sha256_file(path),
                "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )

    firmware = [record for record in records if record["kind"] == "firmware"]
    ori = [record for record in firmware if record["role"] == "ori"]
    modified = [record for record in firmware if record["role"] == "modified"]
    definitions = [record for record in records if record["kind"] == "definition"]
    logs = [record for record in records if record["kind"] == "log"]

    hash_groups: dict[str, list[str]] = {}
    for record in firmware:
        hash_groups.setdefault(record["sha256"], []).append(record["name"])
    duplicate_firmware = [names for names in hash_groups.values() if len(names) > 1]

    warnings: list[str] = []
    if not firmware:
        warnings.append("No firmware file was found.")
    if firmware and not ori:
        warnings.append("Firmware exists, but no file is explicitly confirmed as ORI/baseline.")
    if not modified:
        warnings.append("No MOD/Stage target was found; do not make ORI/MOD difference claims.")
    if not definitions:
        warnings.append("No calibration definition was found; map semantics require discovery and validation.")
    if not logs:
        warnings.append("No vehicle LOG was found; active variants and delivered behavior may remain unresolved.")
    if duplicate_firmware:
        warnings.append("Duplicate firmware hashes were found; filenames alone do not indicate different calibrations.")

    return {
        "schema_version": "1.0",
        "generated_utc": utc_now(),
        "case_dir": str(case_dir.resolve()) if case_dir else None,
        "metadata": metadata,
        "files": records,
        "presence": {
            "firmware_count": len(firmware),
            "confirmed_ori_count": len(ori),
            "modified_count": len(modified),
            "definition_count": len(definitions),
            "log_count": len(logs),
            "image_count": sum(record["kind"] == "image" for record in records),
        },
        "gates": {
            "has_firmware": bool(firmware),
            "has_confirmed_ori": bool(ori),
            "has_comparison_target": bool(modified),
            "has_definition": bool(definitions),
            "has_log": bool(logs),
        },
        "duplicate_firmware_hashes": duplicate_firmware,
        "warnings": warnings,
    }


def run_selftest() -> int:
    with tempfile.TemporaryDirectory() as temp_name:
        root = Path(temp_name)
        (root / "ECU_ORI.bin").write_bytes(b"ORI" * 64)
        (root / "ECU_Stage1.bin").write_bytes(b"MOD" * 64)
        (root / "definition.a2l").write_text("/begin PROJECT", encoding="utf-8")
        (root / "road_log.csv").write_text("rpm,app\n1000,10\n", encoding="utf-8")
        manifest = build_manifest(root, [], False, {"cal_id": "SELFTEST"})
        assert manifest["gates"]["has_confirmed_ori"]
        assert manifest["gates"]["has_comparison_target"]
        assert manifest["gates"]["has_definition"]
        assert manifest["gates"]["has_log"]
        assert manifest["metadata"]["cal_id"] == "SELFTEST"
    print("collect_case_inventory selftest: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, help="Bounded directory to inventory")
    parser.add_argument("--file", action="append", default=[], type=Path, help="Additional input file; repeatable")
    parser.add_argument("--recursive", action="store_true", help="Recursively inventory the bounded case directory")
    parser.add_argument("--meta", action="append", default=[], help="Case metadata as key=value; repeatable")
    parser.add_argument("--out", type=Path, help="Output case_manifest.json")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()
    if args.case_dir is None and not args.file:
        parser.error("Provide --case-dir and/or at least one --file")
    if args.out is None:
        parser.error("Provide --out")

    manifest = build_manifest(args.case_dir, args.file, args.recursive, parse_metadata(args.meta))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"Files: {len(manifest['files'])}; warnings: {len(manifest['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

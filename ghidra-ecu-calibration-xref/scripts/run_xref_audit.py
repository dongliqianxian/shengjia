#!/usr/bin/env python3
"""Run a bounded, evidence-graded Ghidra XREF audit for ECU calibration ranges."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from build_evidence_report import build_report


LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ADDRESS_RE = re.compile(r"^(?:0x)?[0-9A-Fa-f]+$")


def parse_target(spec: str) -> dict[str, str]:
    if "=" not in spec:
        raise ValueError(f"target must be label=start[:end]: {spec}")
    label, address_part = spec.split("=", 1)
    if not LABEL_RE.fullmatch(label):
        raise ValueError(f"invalid target label: {label}")
    pieces = address_part.split(":", 1)
    start = pieces[0]
    end = pieces[1] if len(pieces) == 2 else start
    if not ADDRESS_RE.fullmatch(start) or not ADDRESS_RE.fullmatch(end):
        raise ValueError(f"target addresses must be hexadecimal: {spec}")
    start_value = int(start, 16)
    end_value = int(end, 16)
    if start_value > end_value:
        raise ValueError(f"target start exceeds end: {spec}")
    normalized_start = f"0x{start_value:x}"
    normalized_end = f"0x{end_value:x}"
    return {
        "label": label,
        "start": normalized_start,
        "end": normalized_end,
        "ghidra_args": [label, normalized_start, normalized_end],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_headless(ghidra_home: Path) -> Path:
    candidates = [
        ghidra_home / "support" / "analyzeHeadless.bat",
        ghidra_home / "support" / "analyzeHeadless",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"analyzeHeadless not found under {ghidra_home}")


def run_command(command: list[str], env: dict[str, str], timeout: int, log_path: Path) -> None:
    completed = subprocess.run(
        command,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    script_error = "ERROR REPORT SCRIPT ERROR" in (completed.stdout or "")
    if completed.returncode != 0 or script_error:
        tail = "\n".join((completed.stdout or "").splitlines()[-30:])
        raise RuntimeError(
            f"Ghidra failed (exit={completed.returncode}, script_error={script_error}); "
            f"see {log_path}\n{tail}"
        )


def ghidra_base_command(
    headless: Path,
    project_dir: Path,
    project: str,
    program: str,
    script_dir: Path,
) -> list[str]:
    return [
        str(headless),
        str(project_dir),
        project,
        "-process",
        program,
        "-scriptPath",
        str(script_dir),
    ]


def collect_function_entries(xref: dict) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for target in xref.get("targets", []):
        for hit in target.get("xrefs", []) + target.get("text_hits", []):
            entry = hit.get("function_entry")
            if entry and entry not in seen:
                seen.add(entry)
                entries.append(entry)
    return entries


def write_case_input(args: argparse.Namespace, targets: list[dict[str, str]], out: Path) -> dict:
    case = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "ghidra_home": str(args.ghidra_home.resolve()),
        "project_dir": str(args.project_dir.resolve()),
        "project": args.project,
        "program": args.program,
        "targets": targets,
        "explicit_functions": args.function,
        "call_depth": args.depth,
        "analysis_enabled": args.analysis,
    }
    if args.bin:
        firmware = args.bin.resolve()
        if not firmware.is_file():
            raise FileNotFoundError(f"firmware not found: {firmware}")
        case["firmware"] = {
            "path": str(firmware),
            "size": firmware.stat().st_size,
            "sha256": sha256_file(firmware),
        }
    out.write_text(json.dumps(case, indent=2, ensure_ascii=False), encoding="utf-8")
    return case


def selftest() -> None:
    one = parse_target("descriptor=0x44EA8:0x45133")
    assert one["start"] == "0x44ea8"
    assert one["end"] == "0x45133"
    assert one["ghidra_args"] == ["descriptor", "0x44ea8", "0x45133"]
    scalar = parse_target("scalar=1234")
    assert scalar["start"] == scalar["end"] == "0x1234"
    try:
        parse_target("bad label=0x1")
        raise AssertionError("invalid label accepted")
    except ValueError:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        support = root / "support"
        support.mkdir()
        name = "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless"
        executable = support / name
        executable.write_text("stub", encoding="utf-8")
        assert find_headless(root) == executable
        blob = root / "sample.bin"
        blob.write_bytes(b"ECU-XREF")
        assert sha256_file(blob) == hashlib.sha256(b"ECU-XREF").hexdigest()
    print("run_xref_audit selftest: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ghidra-home", type=Path)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--project")
    parser.add_argument("--program")
    parser.add_argument("--bin", type=Path)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--function", action="append", default=[])
    parser.add_argument("--depth", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--script-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--analysis", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return 0

    required = {
        "--ghidra-home": args.ghidra_home,
        "--project-dir": args.project_dir,
        "--project": args.project,
        "--program": args.program,
        "--out-dir": args.out_dir,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing or not args.target:
        parser.error("missing required inputs: " + ", ".join(missing + ([] if args.target else ["--target"])))

    targets = [parse_target(spec) for spec in args.target]
    target_specs = [piece for target in targets for piece in target["ghidra_args"]]
    headless = find_headless(args.ghidra_home.resolve())
    script_dir = args.script_dir.resolve()
    for script in ("EcuAddressXrefAudit.java", "LookupDataflowAudit.java", "DecompileCallChain.java"):
        if not (script_dir / script).is_file():
            raise FileNotFoundError(f"missing bundled Ghidra script: {script_dir / script}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    case_input_path = out_dir / "case_input.json"
    xref_path = out_dir / "xref_evidence.json"
    dataflow_path = out_dir / "dataflow_context.txt"
    decompile_path = out_dir / "decompile.txt"
    report_path = out_dir / "calibration_xref_report.md"
    case_input = write_case_input(args, targets, case_input_path)

    env = os.environ.copy()
    if args.java_home:
        env["JAVA_HOME"] = str(args.java_home.resolve())

    first = ghidra_base_command(
        headless,
        args.project_dir.resolve(),
        args.project,
        args.program,
        script_dir,
    )
    first.extend(["-postScript", "EcuAddressXrefAudit.java", str(xref_path), *target_specs])
    first.extend([
        "-postScript",
        "LookupDataflowAudit.java",
        str(dataflow_path),
        "6",
        "18",
        *target_specs,
    ])
    if not args.analysis:
        first.append("-noanalysis")
    run_command(first, env, args.timeout_seconds, out_dir / "ghidra_xref.log")
    for expected in (xref_path, dataflow_path):
        if not expected.is_file():
            raise RuntimeError(f"Ghidra completed without required output: {expected}")

    xref = json.loads(xref_path.read_text(encoding="utf-8"))
    functions = collect_function_entries(xref)
    for function in args.function:
        if function not in functions:
            functions.append(function)

    if functions:
        second = ghidra_base_command(
            headless,
            args.project_dir.resolve(),
            args.project,
            args.program,
            script_dir,
        )
        second.extend([
            "-postScript",
            "DecompileCallChain.java",
            str(decompile_path),
            str(args.depth),
            *functions,
        ])
        if not args.analysis:
            second.append("-noanalysis")
        run_command(second, env, args.timeout_seconds, out_dir / "ghidra_decompile.log")
        if not decompile_path.is_file():
            raise RuntimeError(f"Ghidra completed without required output: {decompile_path}")
    else:
        decompile_path.write_text("NO_READER_FUNCTIONS_DISCOVERED\n", encoding="utf-8")

    report = build_report(
        xref,
        dataflow_path.read_text(encoding="utf-8", errors="replace"),
        decompile_path.read_text(encoding="utf-8", errors="replace"),
        case_input,
    )
    report_path.write_text(report, encoding="utf-8")
    print(f"XREF audit complete: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)

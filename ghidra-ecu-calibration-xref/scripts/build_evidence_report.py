#!/usr/bin/env python3
"""Build a conservative Markdown report from Ghidra XREF artifacts."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path


TRANSFORM_PATTERNS = {
    "signed extension": re.compile(r"\b(sxh|extsh|exts\.w|sext|INT_SEXT)\b", re.I),
    "zero extension": re.compile(r"\b(zxh|extu\.w|zext|INT_ZEXT)\b", re.I),
    "addition/subtraction": re.compile(r"\b(addi?|subi?|INT_(ADD|SUB))\b", re.I),
    "shift/fixed-point": re.compile(r"\b(shl|shr|sar|slwi|srwi|INT_(LEFT|RIGHT|SRIGHT))\b", re.I),
    "multiply/divide": re.compile(r"\b(mul|div|INT_(MULT|DIV|SDIV))\b", re.I),
    "saturation": re.compile(r"\b(sat|saturate)\w*\b", re.I),
    "comparison/clamp candidate": re.compile(r"\b(cmp|ble|bge|blt|bgt|cbranch|INT_(LESS|SLESS|EQUAL))\b", re.I),
    "store/consumer": re.compile(r"\b(st\.|store|STORE|call|CALL)\b", re.I),
}


def read_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def transformation_clues(text: str) -> list[str]:
    return [name for name, pattern in TRANSFORM_PATTERNS.items() if pattern.search(text)]


def build_report(xref: dict, dataflow: str, decompile: str, case_input: dict) -> str:
    lines = [
        "# ECU calibration XREF report",
        "",
        "## Case identity",
        "",
        f"- Program: `{xref.get('program', 'unknown')}`",
        f"- Language: `{xref.get('language', 'unknown')}`",
        f"- Compiler: `{xref.get('compiler', 'unknown')}`",
    ]
    firmware = case_input.get("firmware") or {}
    if firmware:
        lines.extend([
            f"- Firmware: `{firmware.get('path', 'unknown')}`",
            f"- Size: `{firmware.get('size', 'unknown')}` bytes",
            f"- SHA-256: `{firmware.get('sha256', 'unknown')}`",
        ])

    lines.extend(["", "## Target evidence", ""])
    discovered_functions: set[str] = set()
    targets = xref.get("targets", [])
    for target in targets:
        direct = target.get("xrefs", [])
        text_hits = target.get("text_hits", [])
        if direct:
            status = "VERIFIED_XREF"
        elif text_hits:
            status = "INDIRECT_REFERENCE"
        else:
            status = "UNRESOLVED"
        lines.extend([
            f"### {target.get('label', 'target')}",
            "",
            f"- Range: `{target.get('start')} .. {target.get('end')}`",
            f"- Code status: **{status}**",
            f"- Direct XREFs: `{len(direct)}`",
            f"- Text/P-code fallback hits: `{len(text_hits)}`",
            f"- Ignored internal/control-flow references: `{target.get('ignored_flow_refs', 0)}`",
        ])
        for hit in direct + text_hits:
            entry = hit.get("function_entry")
            name = hit.get("function")
            if entry:
                discovered_functions.add(f"{name}@{entry}")
        if direct:
            lines.append("- Direct readers:")
            for hit in direct[:20]:
                lines.append(
                    f"  - `{hit.get('from')}` in `{hit.get('function')}@{hit.get('function_entry')}` "
                    f"→ `{hit.get('to')}` ({hit.get('type')})"
                )
        lines.append("")

    clues = transformation_clues(dataflow + "\n" + decompile)
    lines.extend([
        "## Automated transformation clues",
        "",
    ])
    if clues:
        lines.extend(f"- {clue}" for clue in clues)
    else:
        lines.append("- No transformation clue was detected automatically.")

    lines.extend([
        "",
        "## Discovered functions",
        "",
    ])
    if discovered_functions:
        lines.extend(f"- `{item}`" for item in sorted(discovered_functions))
    else:
        lines.append("- None.")

    lines.extend([
        "",
        "## Semantic conclusion",
        "",
        "- Code evidence and physical semantics remain separate.",
        "- Assign `VERIFIED_LOOKUP`, `VERIFIED_TRANSFORM`, or `VERIFIED_CONSUMER` only after manually confirming the relevant lines in `dataflow_context.txt` and `decompile.txt`.",
        "- Confirm axes, dtype, endianness, factor, offset, zero point, physical range, upstream inputs, and downstream consumers before naming the MAP.",
        "- Exact OEM symbol: **UNRESOLVED unless a trusted definition/symbol is available.**",
        "",
        "## Write boundary",
        "",
        "This report is an interpretation aid, not authorization to edit the BIN. Confirm all calibration encoding fields and linked-model consequences before any write.",
        "",
    ])
    return "\n".join(lines)


def selftest() -> None:
    sample = {
        "program": "sample.bin",
        "language": "V850:LE:32:default",
        "compiler": "default",
        "targets": [
            {
                "label": "descriptor",
                "start": "00044ea8",
                "end": "00045133",
                "xrefs": [
                    {
                        "from": "0013466e",
                        "to": "00044ea8",
                        "type": "PARAM",
                        "function": "FUN_00134656",
                        "function_entry": "00134656",
                    }
                ],
                "text_hits": [],
            },
            {
                "label": "z_data",
                "start": "00044ed0",
                "end": "00044ed1",
                "xrefs": [],
                "text_hits": [],
            },
        ],
    }
    report = build_report(sample, "addi -0x1900,r10,r10\nsxh r10", "CALL FUN_00048bac", {})
    assert "VERIFIED_XREF" in report
    assert "UNRESOLVED" in report
    assert "signed extension" in report
    assert "addition/subtraction" in report
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.md"
        out.write_text(report, encoding="utf-8")
        assert out.stat().st_size > 100
    print("build_evidence_report selftest: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xref-json", type=Path)
    parser.add_argument("--dataflow", type=Path)
    parser.add_argument("--decompile", type=Path)
    parser.add_argument("--case-input", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    required = [args.xref_json, args.case_input, args.out]
    if any(item is None for item in required):
        parser.error("--xref-json, --case-input, and --out are required")
    xref = json.loads(args.xref_json.read_text(encoding="utf-8"))
    case_input = json.loads(args.case_input.read_text(encoding="utf-8"))
    report = build_report(xref, read_text(args.dataflow), read_text(args.decompile), case_input)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Integration test: A2L parser + shared-axis relocation + complete-definition gating."""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("migrator", HERE / "migrate_definitions.py")
migrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrator)

A2L = r'''
/begin PROJECT P "test"
/begin MODULE M "test"
  /begin MOD_COMMON "" BYTE_ORDER MSB_LAST /end MOD_COMMON
  /begin COMPU_METHOD CM "id" IDENTICAL "%6.2f" "" /end COMPU_METHOD
  /begin RECORD_LAYOUT RL_U8 FNC_VALUES 1 UBYTE ROW_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_U16 FNC_VALUES 1 UWORD INDEX_INCR DIRECT /end RECORD_LAYOUT
  /begin AXIS_PTS AX_RPM "rpm" 0x800080 NO_INPUT RL_U16 0 CM 4 0 8000 /end AXIS_PTS
  /begin AXIS_PTS AX_LOAD "load" 0x800090 NO_INPUT RL_U16 0 CM 4 0 100 /end AXIS_PTS
  /begin CHARACTERISTIC MAP_A "map a" MAP 0x800100 RL_U8 0 CM 0 255
    /begin AXIS_DESCR COM_AXIS RPM CM 4 0 8000 AXIS_PTS_REF AX_RPM /end AXIS_DESCR
    /begin AXIS_DESCR COM_AXIS LOAD CM 4 0 100 AXIS_PTS_REF AX_LOAD /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC MAP_B "map b" MAP 0x800140 RL_U8 0 CM 0 255
    /begin AXIS_DESCR COM_AXIS RPM CM 4 0 8000 AXIS_PTS_REF AX_RPM /end AXIS_DESCR
    /begin AXIS_DESCR COM_AXIS LOAD CM 4 0 100 AXIS_PTS_REF AX_LOAD /end AXIS_DESCR
  /end CHARACTERISTIC
/end MODULE
/end PROJECT
'''


def main() -> int:
    rng = random.Random(42)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = bytearray(rng.randrange(256) for _ in range(0x400))
        dst = bytearray(rng.randrange(256) for _ in range(0x800))
        delta = 0x200
        objects = {
            0x80: bytes.fromhex("2003e803d007401f"),
            0x90: bytes.fromhex("0000190032006400"),
            0x100: bytes((i * 7 + 3) & 0xFF for i in range(16)),
            0x140: bytes((i * 11 + 5) & 0xFF for i in range(16)),
        }
        for off, data in objects.items():
            src[off:off + len(data)] = data
            dst[off + delta:off + delta + len(data)] = data
        a2l, sp, tp = td / "known.a2l", td / "source.bin", td / "target.bin"
        a2l.write_text(A2L, encoding="utf-8")
        sp.write_bytes(src); tp.write_bytes(dst)
        parser = Path.home() / ".codex" / "skills" / "damos-a2l-parser" / "scripts" / "parse_a2l.py"
        ns = argparse.Namespace(definitions=str(a2l), definitions_kind="a2l",
                                source_bin=str(sp), target_bin=str(tp), source_base=0x800000,
                                target_base=0x900000, out=str(td / "out"), search_window=0x300,
                                a2l_parser=str(parser), ols_parser=None,
                                source_definitions_verified=False)
        report = migrator.run(ns)
        checks = [
            (len(report["maps"]) == 2, "two maps parsed"),
            (len(report["axes"]) == 2, "two AXIS_PTS parsed"),
            (all(x["status"] == "VERIFIED" for x in report["maps"]), "Z objects verified"),
            (all(x["status"] == "VERIFIED" for x in report["axes"]), "axis objects verified"),
            (all(x["definition_status"] == "COMPLETE" for x in report["maps"]), "complete definitions"),
            (all(x["target_offset"] == x["source_offset"] + delta for x in report["maps"] + report["axes"]),
             "all objects relocated by dominant delta"),
        ]
        for ok, label in checks:
            print(("PASS " if ok else "FAIL ") + label)
        return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

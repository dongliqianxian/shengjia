#!/usr/bin/env python3
"""
scan_library.py — ECU calibration library scanner for the ecu-calib-finder skill.

Recursively scans a calibration file library, records each file's path/extension/size,
extracts brand + ECU/microcontroller + SW/HW/Cal IDs from binary content, and (optionally)
pre-scores every file against a target file using the L1-L5 matching rules.

No external dependencies — plain Python 3. Runs in Kimi Work, Codex, Cursor, or any shell.

Usage:
    python scan_library.py "E:\\ECU_Tuning_Projects"
    python scan_library.py "E:\\ECU_Tuning_Projects" --target path/to/target.bin --out index.json
"""

import argparse
import json
import os
import re
import struct
import sys

# ---------------------------------------------------------------------------
# File types we care about
# ---------------------------------------------------------------------------
CALIB_EXTENSIONS = {
    ".bin": "BIN", ".ori": "ORI", ".mod": "MOD",
    ".ols": "OLS", ".kp": "KP",
    ".a2l": "A2L", ".dam": "DAMOS", ".damos": "DAMOS",
    ".csv": "CSV", ".hex": "HEX", ".s19": "SREC", ".frf": "FRF",
}

# ---------------------------------------------------------------------------
# Brand / microcontroller signatures (extend freely)
# Each entry: brand -> list of (compiled regex, label) applied to extracted ASCII strings.
# ---------------------------------------------------------------------------
BRAND_PATTERNS = {
    "DENSO": [
        (re.compile(rb"R7F70(\d{4})"), "DENSO MCU R7F70{0}"),
        (re.compile(rb"89663[-_]?\w+"), "DENSO/Toyota part 89663"),
        (re.compile(rb"\b\d{5}-\d{4,5}\b"), "DENSO-style part"),
        (re.compile(rb"DENSO", re.I), "DENSO marker"),
    ],
    "Bosch": [
        (re.compile(rb"\bEDC1[567]\w*"), "Bosch EDC diesel"),
        (re.compile(rb"\bMED?1[567]\w*"), "Bosch ME/MED petrol"),
        (re.compile(rb"\bMG1\w*|\bMD1\w*"), "Bosch MG1/MD1"),
        (re.compile(rb"0\s?28[01]\s?\d{3}\s?\d{3}"), "Bosch part 0 28x"),
        (re.compile(rb"0\s?261\s?\w{6}"), "Bosch part 0 261"),
        (re.compile(rb"BOSCH", re.I), "Bosch marker"),
    ],
    "Continental": [
        (re.compile(rb"\bSIMOS\s?\d+\.?\d*"), "Continental/Siemens SIMOS"),
        (re.compile(rb"\bPCR\s?2\.?\d"), "Continental PCR"),
        (re.compile(rb"\bEMS\s?\d+"), "Continental EMS"),
        (re.compile(rb"CONTI(NENTAL)?", re.I), "Continental marker"),
    ],
    "Siemens/VDO": [
        (re.compile(rb"\bMS\s?\d{2}\.?\d*"), "Siemens MS"),
        (re.compile(rb"\bVDO\b"), "VDO marker"),
        (re.compile(rb"SIEMENS", re.I), "Siemens marker"),
    ],
    "Delphi": [
        (re.compile(rb"\bDCM\d\.?\d*"), "Delphi DCM"),
        (re.compile(rb"\bMT\d{2}\b"), "Delphi MT"),
        (re.compile(rb"DELPHI", re.I), "Delphi marker"),
    ],
}

# Generic SW/HW/Cal id patterns (best-effort; verify by content downstream)
ID_PATTERNS = [
    (re.compile(rb"\b(\d{2}\d{4}-\d{4,5})\b"), "DENSO_part"),
    (re.compile(rb"\b([0-9]{10})\b"), "SW_or_HW_10digit"),
    (re.compile(rb"\b(10SW\d{6})\b"), "Bosch_SW"),
    (re.compile(rb"\b(10HW\d{6})\b"), "Bosch_HW"),
    (re.compile(rb"\b([A-Z0-9]{2,4}\d{6,})\b"), "generic_id"),
]


def extract_ascii_strings(data, min_len=4, scan_limit=2_000_000):
    """Pull printable ASCII runs from the (head of the) binary for signature matching."""
    chunk = data[:scan_limit]
    return re.findall(rb"[\x20-\x7e]{%d,}" % min_len, chunk)


def detect_brand(data, name_hint=b""):
    """Return (brand, [labels]) best guess from binary content + filename hint.

    Filenames frequently carry the most reliable MCU token (e.g. ...R7F702002...) because OEM
    binaries often pad the header with 0xFF and bury IDs deep in the image. We scan both the
    content head and the filename, so a clear filename token still drives the L1 gate.
    """
    hits = {}
    head = data[:2_000_000] + b"\x00" + name_hint  # append filename so its tokens are matchable
    for brand, patterns in BRAND_PATTERNS.items():
        labels = []
        for rx, label in patterns:
            m = rx.search(head)
            if m:
                try:
                    labels.append(label.format(*[g.decode("ascii", "ignore") for g in m.groups()]))
                except Exception:
                    labels.append(label)
        if labels:
            hits[brand] = sorted(set(labels))
    if not hits:
        return None, []
    # pick the brand with most distinct signature hits
    brand = max(hits, key=lambda b: len(hits[b]))
    return brand, hits[brand]


def extract_ids(data):
    """Best-effort SW/HW/Cal IDs from ASCII strings. Returns dict of {kind: [values]}."""
    found = {}
    strings = extract_ascii_strings(data)
    blob = b"\n".join(strings)
    for rx, kind in ID_PATTERNS:
        vals = sorted({m.decode("ascii", "ignore") for m in rx.findall(blob)})
        if vals:
            found[kind] = vals[:8]
    return found


def read_floats(data, offset, n, little_endian=True):
    """Read n IEEE-754 floats — helper for ORI/MOD map diffing."""
    fmt = "<f" if little_endian else ">f"
    out = []
    for i in range(n):
        b = data[offset + i * 4: offset + i * 4 + 4]
        if len(b) == 4:
            out.append(struct.unpack(fmt, b)[0])
    return out


def characterize(path):
    """Return a dict describing one file."""
    ext = os.path.splitext(path)[1].lower()
    ftype = CALIB_EXTENSIONS.get(ext, ext.lstrip(".").upper() or "UNKNOWN")
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    info = {"path": path, "name": os.path.basename(path), "ext": ext,
            "type": ftype, "size": size, "brand": None, "brand_labels": [], "ids": {}}
    name_hint = os.path.basename(path).encode("utf-8", "ignore")
    # Read content for binary calibration files (size-capped); also use the filename as a hint.
    if ftype in ("BIN", "ORI", "MOD", "HEX", "SREC", "FRF") and size and size < 64_000_000:
        try:
            with open(path, "rb") as f:
                data = f.read()
            brand, labels = detect_brand(data, name_hint)
            info["brand"] = brand
            info["brand_labels"] = labels
            info["ids"] = extract_ids(data)
        except Exception as e:
            info["read_error"] = str(e)
    else:
        # Non-binary (CSV/OLS/KP/A2L/DAMOS) — still try brand from filename + small content peek.
        try:
            peek = b""
            if size and size < 8_000_000:
                with open(path, "rb") as f:
                    peek = f.read(2_000_000)
            brand, labels = detect_brand(peek, name_hint)
            info["brand"] = brand
            info["brand_labels"] = labels
            if peek:
                info["ids"] = extract_ids(peek)
        except Exception as e:
            info["read_error"] = str(e)
    return info


def score_against_target(cand, target):
    """Apply L1-L5. Returns (similarity_pct, match_label, verdict)."""
    if target is None:
        return None, "", ""

    # L5 pre-filter: size identity
    l5 = (cand["size"] is not None and cand["size"] == target["size"])

    # L1 hard gate: brand + MCU/protocol
    l1 = False
    if cand["brand"] and target["brand"]:
        l1 = (cand["brand"] == target["brand"])
        # tighten with MCU label overlap if available
        if l1 and target["brand_labels"] and cand["brand_labels"]:
            shared = set(cand["brand_labels"]) & set(target["brand_labels"])
            # if both have explicit MCU labels and none shared, demote
            mcu_t = [x for x in target["brand_labels"] if "MCU" in x or "EDC" in x or "SIMOS" in x or "DCM" in x]
            mcu_c = [x for x in cand["brand_labels"] if "MCU" in x or "EDC" in x or "SIMOS" in x or "DCM" in x]
            if mcu_t and mcu_c and not (set(mcu_t) & set(mcu_c)):
                l1 = False

    # L2: shared SW/HW IDs
    shared_ids = 0
    for kind, vals in cand["ids"].items():
        tvals = set(target["ids"].get(kind, []))
        if tvals & set(vals):
            shared_ids += 1
    l2 = shared_ids > 0

    # L3: same file type family of definition files is a soft plus
    l3 = cand["type"] in ("A2L", "DAMOS", "KP", "OLS") and l1

    # Scoring
    sim = 0
    if l5: sim += 15
    if l1: sim += 50
    if l2: sim += 25
    if l3: sim += 10

    # Verdicts
    if cand["brand"] is None:
        # Brand couldn't be determined from content or filename.
        if l5:
            return sim, "L1?", "🟡 brand unverified — same size, confirm protocol manually"
        return sim, "L1?", "🟡 brand unverified — confirm protocol manually"
    if not l1:
        if l5:
            return sim, "L1✗", "❌ size coincidence, different protocol"
        return sim, "L1✗", "❌ not usable (different protocol)"
    if l1 and l2:
        return sim, "L1+L2", "✅ directly usable"
    return sim, "L1" + ("+L3" if l3 else ""), "🟡 same-family reference"


def main():
    ap = argparse.ArgumentParser(description="Scan an ECU calibration library and match against a target.")
    ap.add_argument("root", help="Library root directory (e.g. E:\\ECU_Tuning_Projects)")
    ap.add_argument("--target", help="Path to the target ECU file to match against", default=None)
    ap.add_argument("--out", help="Write JSON index to this file", default=None)
    ap.add_argument("--top", type=int, default=25, help="How many ranked matches to print")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print(f"ERROR: library root not found or not a directory: {args.root}", file=sys.stderr)
        sys.exit(2)

    target = characterize(args.target) if args.target else None

    files = []
    for dirpath, _dirs, filenames in os.walk(args.root):
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in CALIB_EXTENSIONS:
                files.append(os.path.join(dirpath, fn))

    index = []
    for p in files:
        info = characterize(p)
        if target:
            sim, match, verdict = score_against_target(info, target)
            info["similarity"] = sim
            info["match_level"] = match
            info["verdict"] = verdict
        index.append(info)

    if target:
        index.sort(key=lambda x: x.get("similarity", 0), reverse=True)

    result = {"library_root": args.root, "file_count": len(index),
              "target": target, "files": index}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Wrote index for {len(index)} files to {args.out}")

    # Console summary
    if target:
        print(f"\nTARGET: {target['name']}  size={target['size']}  brand={target['brand']}  ids={target['ids']}")
        print(f"\nTop {args.top} matches:")
        print(f"{'#':>2} {'sim':>4} {'match':<7} {'type':<6} {'size':>10}  verdict / file")
        for i, fi in enumerate(index[:args.top], 1):
            print(f"{i:>2} {fi.get('similarity',0):>4} {fi.get('match_level',''):<7} "
                  f"{fi['type']:<6} {str(fi['size']):>10}  {fi.get('verdict','')} / {fi['name']}")
    else:
        print(f"Scanned {len(index)} calibration files under {args.root} (no target given).")


if __name__ == "__main__":
    main()

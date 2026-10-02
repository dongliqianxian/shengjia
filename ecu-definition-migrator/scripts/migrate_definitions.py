#!/usr/bin/env python3
"""Relocate ECU MAP definitions between software versions without writing the target BIN."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import random
import statistics
import struct
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load parser: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_parser(explicit: str | None, skill_name: str, script_name: str) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    here = Path(__file__).resolve().parents[1]
    candidates.append(here.parent / skill_name / "scripts" / script_name)
    candidates.append(Path.home() / ".codex" / "skills" / skill_name / "scripts" / script_name)
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(f"missing {script_name}; pass its path explicitly")


def parse_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    return int(str(value), 0)


def definition_kind(path: Path, forced: str | None) -> str:
    if forced and forced != "auto":
        return forced
    ext = path.suffix.lower()
    if ext == ".a2l":
        return "a2l"
    if ext in {".ols", ".kp"}:
        return "ols"
    if ext == ".json":
        return "json"
    raise ValueError(f"cannot infer definitions kind from {ext}; use --definitions-kind")


def read_definitions(args, source_bin: bytes | None):
    path = Path(args.definitions).resolve()
    kind = definition_kind(path, args.definitions_kind)
    parser_path = None
    parser_module = None
    if kind == "json":
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif kind == "a2l":
        parser_path = find_parser(args.a2l_parser, "damos-a2l-parser", "parse_a2l.py")
        parser_module = load_module(parser_path, "ecu_migrate_a2l")
        raw = parser_module.parse(str(path))
    else:
        parser_path = find_parser(args.ols_parser, "winols-ols-parser", "parse_ols.py")
        parser_module = load_module(parser_path, "ecu_migrate_ols")
        raw = parser_module.parse(
            str(path), binpath=args.source_bin if source_bin is not None else None
        )
    maps = raw if isinstance(raw, list) else raw.get("maps", [])
    normalized = []
    for i, m in enumerate(maps):
        address = parse_int(m.get("address"))
        source_offset = parse_int(m.get("source_offset"))
        if source_offset is None and address is not None:
            if kind == "a2l":
                source_offset = address - args.source_base if args.source_base is not None else None
            else:
                source_offset = address
        byte_order = m.get("byte_order")
        if byte_order == "MSB_FIRST":
            byte_order = "big"
        elif byte_order == "MSB_LAST":
            byte_order = "little"
        normalized.append({
            "index": i,
            "object_kind": "MAP",
            "name": m.get("name") or f"map_{i:04d}",
            "address": address,
            "source_offset": source_offset,
            "size_bytes": parse_int(m.get("size_bytes")),
            "dims": m.get("dims") or [],
            "datatype": m.get("datatype"),
            "byte_order": byte_order,
            "factor": m.get("factor"),
            "offset": m.get("offset"),
            "unit": m.get("unit") or "",
            "axes": m.get("axes") or [],
            "source_metadata": m,
        })
    axes_normalized = []
    if kind == "a2l" and parser_module is not None:
        tokens = parser_module.tokenize(parser_module._read_text(str(path)))
        root = parser_module.parse_blocks(tokens)
        default_order = raw.get("byte_order")
        for module in root.all_sub("MODULE"):
            layouts = parser_module.parse_record_layouts(module)
            for name, axis in parser_module.parse_axis_pts(module).items():
                layout = layouts.get(axis.get("record_layout"), {})
                address = parse_int(axis.get("address"))
                count = parse_int(axis.get("max_points"))
                width = parse_int(layout.get("width"))
                size_bytes = count * width if count and width else None
                source_offset = address - args.source_base if address is not None and args.source_base is not None else None
                byte_order = "big" if default_order == "MSB_FIRST" else "little" if default_order == "MSB_LAST" else None
                axes_normalized.append({
                    "index": len(axes_normalized), "object_kind": "AXIS", "name": name,
                    "address": address, "source_offset": source_offset, "size_bytes": size_bytes,
                    "dims": [count] if count else [], "datatype": layout.get("datatype"),
                    "byte_order": byte_order, "factor": None, "offset": None, "unit": "",
                    "axes": [], "source_metadata": axis,
                })
    elif kind == "json" and isinstance(raw, dict):
        for i, axis in enumerate(raw.get("axis_definitions", [])):
            address = parse_int(axis.get("address"))
            source_offset = parse_int(axis.get("source_offset"))
            if source_offset is None and address is not None and args.source_base is not None:
                source_offset = address - args.source_base
            axes_normalized.append({
                "index": i, "object_kind": "AXIS", "name": axis.get("name") or f"axis_{i:04d}",
                "address": address, "source_offset": source_offset,
                "size_bytes": parse_int(axis.get("size_bytes")), "dims": axis.get("dims") or [],
                "datatype": axis.get("datatype"), "byte_order": axis.get("byte_order"),
                "factor": axis.get("factor"), "offset": axis.get("offset"), "unit": axis.get("unit") or "",
                "axes": [], "source_metadata": axis,
            })
    return kind, parser_path, normalized, axes_normalized


def find_all(buf: bytes, pattern: bytes, cap: int = 256):
    out, start = [], 0
    while len(out) < cap:
        pos = buf.find(pattern, start)
        if pos < 0:
            break
        out.append(pos)
        start = pos + 1
    return out


def entropy_quality(data: bytes) -> float:
    if not data:
        return 0.0
    unique = len(set(data)) / min(32, len(data))
    transitions = sum(a != b for a, b in zip(data, data[1:])) / max(1, len(data) - 1)
    return min(1.0, 0.55 * unique + 0.45 * transitions)


def context_score(src: bytes, dst: bytes, so: int, to: int, size: int, width: int = 24) -> float:
    pairs = []
    for sa, ta in ((so - width, to - width), (so + size, to + size)):
        if sa >= 0 and ta >= 0 and sa + width <= len(src) and ta + width <= len(dst):
            a, b = src[sa:sa + width], dst[ta:ta + width]
            pairs.append(sum(x == y for x, y in zip(a, b)) / width)
    return statistics.mean(pairs) if pairs else 0.0


def anchor_candidates(pattern: bytes, target: bytes):
    n = len(pattern)
    block = min(16, max(8, n // 8))
    starts = sorted(set([0, max(0, n // 4), max(0, n // 2), max(0, 3 * n // 4), max(0, n - block)]))
    votes = Counter()
    usable = 0
    for off in starts:
        seg = pattern[off:off + block]
        if len(seg) < 8 or entropy_quality(seg) < 0.32:
            continue
        usable += 1
        for pos in find_all(target, seg, cap=128):
            start = pos - off
            if 0 <= start <= len(target) - n:
                votes[start] += 1
    return votes, usable


def pearson(a, b) -> float:
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    ma, mb = statistics.mean(a), statistics.mean(b)
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    den = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db))
    return sum(x * y for x, y in zip(da, db)) / den if den else 0.0


def u16_values(data: bytes, endian: str):
    n = len(data) // 2
    if n < 3:
        return []
    fmt = ("<" if endian == "little" else ">") + f"{n}H"
    return list(struct.unpack(fmt, data[:n * 2]))


def shape_score(a, b) -> float:
    if len(a) < 3 or len(a) != len(b):
        return 0.0
    sa = [(x > 0) - (x < 0) for x in (a[i + 1] - a[i] for i in range(len(a) - 1))]
    sb = [(x > 0) - (x < 0) for x in (b[i + 1] - b[i] for i in range(len(b) - 1))]
    return sum(x == y for x, y in zip(sa, sb)) / len(sa)


def approximate_score(pattern: bytes, candidate: bytes, ctx: float, anchor_ratio: float, endian: str | None):
    byte_equal = sum(a == b for a, b in zip(pattern, candidate)) / len(pattern)
    endians = [endian] if endian in {"little", "big"} else ["little", "big"]
    corrs, shapes = [], []
    for e in endians:
        a, b = u16_values(pattern, e), u16_values(candidate, e)
        if a:
            corrs.append(max(0.0, pearson(a, b)))
            shapes.append(shape_score(a, b))
    corr = max(corrs, default=0.0)
    shape = max(shapes, default=0.0)
    total = 0.38 * byte_equal + 0.34 * corr + 0.14 * shape + 0.09 * ctx + 0.05 * anchor_ratio
    return round(total, 6), {"byte_equal": byte_equal, "u16_corr": corr, "shape": shape,
                           "context": ctx, "anchor_ratio": anchor_ratio}


def dominant_delta(initial):
    deltas = []
    for item in initial:
        if len(item["exact_positions"]) == 1 and item["quality"] >= 0.35:
            deltas.append(item["exact_positions"][0] - item["map"]["source_offset"])
    if not deltas:
        return None, 0
    counts = Counter(deltas)
    delta, support = counts.most_common(1)[0]
    return (delta, support) if support >= 2 else (None, support)


def quick_similarity(pattern: bytes, target: bytes, pos: int) -> float:
    if pos < 0 or pos + len(pattern) > len(target):
        return -1.0
    n = min(32, len(pattern))
    idx = [round(i * (len(pattern) - 1) / max(1, n - 1)) for i in range(n)]
    return sum(pattern[i] == target[pos + i] for i in idx) / n


def relocate(maps, source: bytes | None, target: bytes, search_window: int,
             allow_verified: bool = True, forced_delta=None, forced_support: int = 0):
    if source is None:
        return [{**m, "status": "NEEDS_SOURCE_BIN", "confidence": 0.0,
                 "target_offset": None, "target_address": None,
                 "evidence": ["matching source BIN not supplied"], "candidates": []} for m in maps], None, 0

    initial = []
    for m in maps:
        so, size = m["source_offset"], m["size_bytes"]
        if so is None or size is None or size < 4 or so < 0 or so + size > len(source):
            initial.append({"map": m, "invalid": True, "exact_positions": [], "quality": 0.0})
            continue
        pattern = source[so:so + size]
        quality = entropy_quality(pattern)
        exact = find_all(target, pattern) if quality >= 0.12 else []
        initial.append({"map": m, "invalid": False, "pattern": pattern,
                        "exact_positions": exact, "quality": quality})

    delta, support = dominant_delta(initial)
    if forced_delta is not None:
        delta, support = forced_delta, max(support, forced_support)
    results = []
    for item in initial:
        m = item["map"]
        if item.get("invalid"):
            results.append({**m, "status": "INVALID_SOURCE", "confidence": 0.0,
                            "target_offset": None, "target_address": None,
                            "evidence": ["source offset/size missing or outside source BIN"], "candidates": []})
            continue
        pattern, exact, quality = item["pattern"], item["exact_positions"], item["quality"]
        so, size = m["source_offset"], m["size_bytes"]
        evidence = [f"source_signature_quality={quality:.3f}"]
        chosen = None
        if len(exact) == 1:
            chosen = exact[0]
            ctx = context_score(source, target, so, chosen, size)
            evidence += ["unique exact full-map match", f"context={ctx:.3f}"]
            if delta is not None:
                evidence.append(f"dominant_delta={delta:+#x} support={support}")
            status = "VERIFIED" if allow_verified else "CANDIDATE_HIGH"
            results.append({**m, "status": status, "confidence": round(0.96 + 0.03 * ctx, 4),
                            "target_offset": chosen, "target_address": None,
                            "evidence": evidence, "best_score": 1.0, "runner_up_score": None,
                            "candidates": [{"offset": chosen, "score": 1.0}]})
            continue
        if len(exact) > 1:
            if delta is not None:
                near = [p for p in exact if abs((p - so) - delta) <= 8]
                if len(near) == 1:
                    chosen = near[0]
                    ctx = context_score(source, target, so, chosen, size)
                    evidence += [f"{len(exact)} exact matches resolved by dominant delta",
                                 f"dominant_delta={delta:+#x} support={support}", f"context={ctx:.3f}"]
                    status = "VERIFIED" if allow_verified else "CANDIDATE_HIGH"
                    results.append({**m, "status": status, "confidence": round(0.94 + 0.04 * ctx, 4),
                                    "target_offset": chosen, "target_address": None,
                                    "evidence": evidence, "best_score": 1.0, "runner_up_score": 1.0,
                                    "candidates": [{"offset": p, "score": 1.0} for p in exact[:32]]})
                    continue
            evidence.append(f"{len(exact)} indistinguishable exact matches")
            results.append({**m, "status": "AMBIGUOUS", "confidence": 0.45,
                            "target_offset": None, "target_address": None, "evidence": evidence,
                            "best_score": 1.0, "runner_up_score": 1.0,
                            "candidates": [{"offset": p, "score": 1.0} for p in exact[:32]]})
            continue

        votes, usable = anchor_candidates(pattern, target)
        candidate_set = set(votes)
        if delta is not None:
            predicted = so + delta
            lo, hi = max(0, predicted - search_window), min(len(target) - size, predicted + search_window)
            step = 2 if size % 2 == 0 else 1
            sampled = [(quick_similarity(pattern, target, p), p) for p in range(lo, hi + 1, step)]
            candidate_set.update(p for _, p in sorted(sampled, reverse=True)[:12])
        scored = []
        for p in candidate_set:
            if p < 0 or p + size > len(target):
                continue
            ctx = context_score(source, target, so, p, size)
            ar = votes[p] / usable if usable else 0.0
            score, parts = approximate_score(pattern, target[p:p + size], ctx, ar, m.get("byte_order"))
            if delta is not None and abs((p - so) - delta) <= 8:
                score = min(1.0, score + 0.07)
                parts["delta_consistent"] = True
            scored.append({"offset": p, "score": round(score, 6), "parts": parts})
        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0] if scored else None
        runner = scored[1] if len(scored) > 1 else None
        gap = best["score"] - runner["score"] if best and runner else (best["score"] if best else 0.0)
        if best and best["score"] >= 0.82 and gap >= 0.06:
            status = "CANDIDATE_HIGH"
        elif best and best["score"] >= 0.64 and gap >= 0.03:
            status = "CANDIDATE_MEDIUM"
        elif best:
            status = "AMBIGUOUS"
        else:
            status = "UNRESOLVED"
        if best:
            evidence += [f"approximate score={best['score']:.3f}", f"runner_gap={gap:.3f}",
                         f"anchor_blocks={usable}"]
            if delta is not None:
                evidence.append(f"dominant_delta={delta:+#x} support={support}")
        results.append({**m, "status": status, "confidence": best["score"] if best else 0.0,
                        "target_offset": best["offset"] if best else None, "target_address": None,
                        "evidence": evidence, "best_score": best["score"] if best else None,
                        "runner_up_score": runner["score"] if runner else None,
                        "candidates": scored[:12]})
    return results, delta, support


CSV_FIELDS = ["object_kind", "name", "status", "definition_status", "confidence", "source_address", "source_offset", "target_address",
              "target_offset", "size_bytes", "dims", "datatype", "byte_order", "factor", "offset",
              "unit", "best_score", "runner_up_score", "axes_migration", "evidence"]


def csv_row(r):
    return {
        "object_kind": r.get("object_kind", "MAP"), "name": r["name"], "status": r["status"],
        "definition_status": r.get("definition_status", ""), "confidence": r["confidence"],
        "source_address": f"0x{r['address']:X}" if r.get("address") is not None else "",
        "source_offset": f"0x{r['source_offset']:X}" if r.get("source_offset") is not None else "",
        "target_address": f"0x{r['target_address']:X}" if r.get("target_address") is not None else "",
        "target_offset": f"0x{r['target_offset']:X}" if r.get("target_offset") is not None else "",
        "size_bytes": r.get("size_bytes"), "dims": "x".join(map(str, r.get("dims") or [])),
        "datatype": r.get("datatype") or "", "byte_order": r.get("byte_order") or "",
        "factor": r.get("factor"), "offset": r.get("offset"), "unit": r.get("unit") or "",
        "best_score": r.get("best_score"), "runner_up_score": r.get("runner_up_score"),
        "axes_migration": json.dumps(r.get("axes_migration") or [], ensure_ascii=False),
        "evidence": " | ".join(r.get("evidence") or []),
    }


def write_csv(path: Path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(csv_row(r))


def run(args):
    source_path = Path(args.source_bin).resolve() if args.source_bin else None
    target_path = Path(args.target_bin).resolve()
    source = source_path.read_bytes() if source_path else None
    target = target_path.read_bytes()
    kind, parser_path, maps, axes = read_definitions(args, source)
    allow_verified = kind != "ols" or args.source_definitions_verified
    results, delta, support = relocate(maps, source, target, args.search_window,
                                       allow_verified=allow_verified)
    axis_results, _, _ = relocate(axes, source, target, args.search_window,
                                  allow_verified=allow_verified, forced_delta=delta, forced_support=support)
    for r in results + axis_results:
        if r["target_offset"] is not None and args.target_base is not None:
            r["target_address"] = r["target_offset"] + args.target_base
    axis_by_ref = {}
    for a in axis_results:
        axis_by_ref[(a.get("name"), a.get("address"))] = a
        axis_by_ref[(a.get("name"), None)] = a
        axis_by_ref[(None, a.get("address"))] = a
    for r in results:
        migrations, pending = [], False
        for ax in r.get("axes") or []:
            ref, addr = ax.get("axis_pts_ref"), parse_int(ax.get("address"))
            matched = axis_by_ref.get((ref, addr)) or axis_by_ref.get((ref, None)) or axis_by_ref.get((None, addr))
            if matched:
                migrations.append({"name": matched["name"], "source_address": matched.get("address"),
                                   "target_address": matched.get("target_address"), "status": matched["status"]})
                pending = pending or matched["status"] != "VERIFIED"
            else:
                migrations.append({"name": ref, "source_address": addr, "target_address": None,
                                   "status": "EMBEDDED_OR_UNRESOLVED"})
                pending = True
        r["axes_migration"] = migrations
        if pending:
            r["definition_status"] = "AXES_PENDING"
        elif (kind in {"ols", "json"}) and not (r.get("axes") or []):
            r["definition_status"] = "AXES_UNKNOWN" if len(r.get("dims") or []) >= 1 else "COMPLETE"
        else:
            r["definition_status"] = "COMPLETE"
    for a in axis_results:
        a["definition_status"] = "COMPLETE" if a["status"] == "VERIFIED" else "PENDING"
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    summary = Counter(r["status"] for r in results)
    report = {
        "schema_version": 1,
        "definitions": str(Path(args.definitions).resolve()),
        "definitions_kind": kind,
        "parser": str(parser_path) if parser_path else None,
        "source_bin": str(source_path) if source_path else None,
        "source_sha256": sha256(source_path) if source_path else None,
        "target_bin": str(target_path),
        "target_sha256": sha256(target_path),
        "source_base": args.source_base,
        "target_base": args.target_base,
        "dominant_delta": delta,
        "dominant_delta_support": support,
        "source_definitions_verified": bool(args.source_definitions_verified),
        "summary": dict(summary),
        "axis_summary": dict(Counter(r["status"] for r in axis_results)),
        "maps": results,
        "axes": axis_results,
    }
    (out / "migration_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out / "migration_report.csv", results + axis_results)
    write_csv(out / "migrated_verified.csv", [r for r in results
                                               if r["status"] == "VERIFIED" and r["definition_status"] == "COMPLETE"])
    write_csv(out / "unresolved.csv", [r for r in results + axis_results
                                        if r["status"] != "VERIFIED" or r.get("definition_status") not in {"COMPLETE", ""}])
    print(f"definitions={len(results)} delta={delta} support={support} summary={dict(summary)}")
    print(f"output={out}")
    return report


def selftest():
    rng = random.Random(20260712)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        source = bytearray(rng.randrange(0, 256) for _ in range(0x1200))
        target = bytearray(rng.randrange(0, 256) for _ in range(0x1800))
        delta = 0x180
        defs = []
        for i, off in enumerate((0x100, 0x240, 0x380)):
            size = 64
            pattern = bytes((17 * i + j * 7 + (j // 5)) & 0xFF for j in range(size))
            source[off:off + size] = pattern
            target[off + delta:off + delta + size] = pattern
            defs.append({"name": f"Exact_{i}", "source_offset": off, "size_bytes": size,
                         "dims": [8, 4], "datatype": "UWORD"})
        off, size = 0x500, 80
        pattern = bytes((j * 3 + (j // 9) * 11) & 0xFF for j in range(size))
        source[off:off + size] = pattern
        changed = bytearray(pattern)
        for j in (5, 17, 33, 57):
            changed[j] = (changed[j] + 3) & 0xFF
        target[off + delta:off + delta + size] = changed
        defs.append({"name": "Approx", "source_offset": off, "size_bytes": size, "dims": [10, 4]})
        off, size = 0x800, 32
        duplicate = bytes((j * 13 + 9) & 0xFF for j in range(size))
        source[off:off + size] = duplicate
        target[0x1100:0x1100 + size] = duplicate
        target[0x1300:0x1300 + size] = duplicate
        defs.append({"name": "Ambiguous", "source_offset": off, "size_bytes": size})
        sp, tp, jp = td / "source.bin", td / "target.bin", td / "defs.json"
        sp.write_bytes(source); tp.write_bytes(target); jp.write_text(json.dumps({"maps": defs}), encoding="utf-8")
        ns = argparse.Namespace(definitions=str(jp), definitions_kind="json", source_bin=str(sp),
                                target_bin=str(tp), source_base=None, target_base=0x800000,
                                out=str(td / "out"), search_window=0x300,
                                a2l_parser=None, ols_parser=None, source_definitions_verified=False)
        report = run(ns)
        by = {m["name"]: m for m in report["maps"]}
        checks = [
            (report["dominant_delta"] == delta, "dominant delta"),
            (all(by[f"Exact_{i}"]["status"] == "VERIFIED" for i in range(3)), "exact verified"),
            (by["Approx"]["status"] in {"CANDIDATE_HIGH", "CANDIDATE_MEDIUM"}, "approx candidate only"),
            (by["Ambiguous"]["status"] == "AMBIGUOUS", "duplicate ambiguous"),
            ((td / "out" / "migrated_verified.csv").exists(), "verified csv"),
        ]
        for ok, label in checks:
            print(("PASS " if ok else "FAIL ") + label)
        gated, _, _ = relocate(defs, bytes(source), bytes(target), 0x300, allow_verified=False)
        gate_ok = all(x["status"] == "CANDIDATE_HIGH" for x in gated[:3])
        checks.append((gate_ok, "unverified OLS-style source cannot auto-verify"))
        print(("PASS " if gate_ok else "FAIL ") + checks[-1][1])
        # no-source downgrade
        ns.source_bin = None; ns.out = str(td / "nosource")
        report2 = run(ns)
        checks.append((all(m["status"] == "NEEDS_SOURCE_BIN" for m in report2["maps"]), "no-source downgrade"))
        print(("PASS " if checks[-1][0] else "FAIL ") + checks[-1][1])
        return 0 if all(x for x, _ in checks) else 1


def main():
    ap = argparse.ArgumentParser(description="Relocate ECU MAP definitions to a new software-version BIN")
    ap.add_argument("--definitions")
    ap.add_argument("--definitions-kind", choices=["auto", "a2l", "ols", "json"], default="auto")
    ap.add_argument("--source-bin")
    ap.add_argument("--target-bin")
    ap.add_argument("--source-base", type=lambda s: int(s, 0))
    ap.add_argument("--target-base", type=lambda s: int(s, 0))
    ap.add_argument("--a2l-parser")
    ap.add_argument("--ols-parser")
    ap.add_argument("--source-definitions-verified", action="store_true",
                    help="OLS/KP source addresses and layouts were independently confirmed in WinOLS")
    ap.add_argument("--search-window", type=lambda s: int(s, 0), default=0x2000)
    ap.add_argument("--out", default="migration_output")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    if not args.definitions or not args.target_bin:
        ap.error("--definitions and --target-bin are required")
    run(args)


if __name__ == "__main__":
    main()

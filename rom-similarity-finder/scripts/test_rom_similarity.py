#!/usr/bin/env python3
"""Behavioral tests for rom_similarity.py."""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import rom_similarity as rs


def deterministic_bytes(size: int, seed: int) -> bytes:
    generator = random.Random(seed)
    return bytes(generator.randrange(1, 255) for _ in range(size))


class FingerprintTests(unittest.TestCase):
    def test_identical_data_scores_one(self) -> None:
        data = deterministic_bytes(8192, 1)
        score = rs.coarse_similarity(rs.fingerprint_bytes(data), rs.fingerprint_bytes(data))
        self.assertEqual(score.jaccard, 1.0)
        self.assertEqual(score.containment, 1.0)

    def test_romhex_mode_scores_identical_data_one(self) -> None:
        data = deterministic_bytes(8192, 12)
        score = rs.coarse_similarity(
            rs.fingerprint_bytes(data, "romhex"),
            rs.fingerprint_bytes(data, "romhex"),
        )
        self.assertEqual(score.jaccard, 1.0)
        self.assertEqual(score.containment, 1.0)

    def test_fast_numpy_and_portable_backends_match(self) -> None:
        data = b"\xff" * 80 + deterministic_bytes(8192, 13) + b"\x00" * 80
        view = rs._trim_padding(data)
        try:
            vectorized = rs._fingerprint_fast_numpy(view)
        except ImportError:
            self.skipTest("NumPy is not installed")
        self.assertEqual(vectorized, rs._fingerprint_fast_python(view))

    def test_leading_and_trailing_padding_is_ignored(self) -> None:
        data = deterministic_bytes(8192, 2)
        padded = b"\xff" * 257 + data + b"\x00" * 513
        self.assertEqual(rs.fingerprint_bytes(data), rs.fingerprint_bytes(padded))

    def test_unrelated_data_has_low_coarse_score(self) -> None:
        left = deterministic_bytes(16384, 3)
        right = deterministic_bytes(16384, 4)
        score = rs.coarse_similarity(rs.fingerprint_bytes(left), rs.fingerprint_bytes(right))
        self.assertLess(score.best, 0.20)

    def test_blob_round_trip(self) -> None:
        original = rs.fingerprint_bytes(deterministic_bytes(8192, 5))
        restored = rs.fingerprint_from_blob(rs.fingerprint_to_blob(original))
        self.assertEqual(original, restored)


class ExactComparisonTests(unittest.TestCase):
    def test_odd_header_shift_is_found(self) -> None:
        small = deterministic_bytes(32768, 6)
        big = deterministic_bytes(76, 7) + small + deterministic_bytes(123, 8)
        result = rs.exact_byte_similarity(small, big)
        self.assertEqual(result.percent, 100.0)
        self.assertEqual(result.offset, 76)

    def test_modified_data_reports_observed_fraction(self) -> None:
        original = bytearray(deterministic_bytes(32768, 9))
        modified = bytearray(original)
        changed = 0
        for index in range(0, len(modified), 1000):
            modified[index] ^= 0x5A
            changed += 1
        result = rs.exact_byte_similarity(bytes(original), bytes(modified))
        expected = 100.0 * (len(original) - changed) / len(original)
        self.assertAlmostEqual(result.percent, expected, places=6)


class IndexIntegrationTests(unittest.TestCase):
    def test_compare_cli_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.bin"
            second = root / "second.bin"
            report = root / "report.json"
            data = deterministic_bytes(8192, 50)
            first.write_bytes(data)
            second.write_bytes(data)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(rs.__file__)),
                    "compare",
                    str(first),
                    str(second),
                    "--json",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["evidence"], "VERIFIED_IDENTICAL_BYTES")

    def test_index_search_ranks_related_rom_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library"
            library.mkdir()
            base = deterministic_bytes(32768, 10)
            related = bytearray(base)
            for index in range(200, 300):
                related[index] ^= 0x11
            (library / "related.bin").write_bytes(related)
            (library / "unrelated.bin").write_bytes(deterministic_bytes(32768, 11))
            needle = root / "needle.bin"
            needle.write_bytes(base)
            database = root / "index.sqlite"

            build = rs.build_index(
                roots=[library],
                db_path=database,
                extensions={".bin"},
                max_bytes=1024 * 1024,
                min_bytes=4096,
                workers=1,
                rebuild=True,
            )
            self.assertEqual(build["indexed"], 2)
            search = rs.search_index(
                needle_path=needle,
                db_path=database,
                min_percent=1.0,
                limit=10,
                exact_top=10,
                max_bytes=1024 * 1024,
            )
            self.assertGreaterEqual(len(search["results"]), 1)
            first = search["results"][0]
            self.assertTrue(str(first["path"]).endswith("related.bin"))
            self.assertGreater(first["exact"]["percent"], 99.0)
            self.assertEqual(first["evidence"], "OBSERVED_PARTIAL_BYTE_MATCH")

    def test_process_workers_build_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"sample_{index}.bin").write_bytes(
                    deterministic_bytes(8192, 20 + index)
                )
            result = rs.build_index(
                roots=[root],
                db_path=root / "parallel.sqlite",
                extensions={".bin"},
                max_bytes=1024 * 1024,
                min_bytes=4096,
                workers=2,
                rebuild=True,
            )
            self.assertEqual(result["indexed"], 3)
            self.assertEqual(result["errors"], [])

    def test_incremental_index_removes_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(deterministic_bytes(8192, 40))
            second.write_bytes(deterministic_bytes(8192, 41))
            database = root / "index.sqlite"
            rs.build_index(
                roots=[root],
                db_path=database,
                extensions={".bin"},
                max_bytes=1024 * 1024,
                min_bytes=4096,
                workers=1,
                rebuild=True,
            )
            second.unlink()
            updated = rs.build_index(
                roots=[root],
                db_path=database,
                extensions={".bin"},
                max_bytes=1024 * 1024,
                min_bytes=4096,
                workers=1,
            )
            self.assertEqual(updated["removed_missing"], 1)

    def test_index_records_and_searches_romhex_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library"
            library.mkdir()
            data = deterministic_bytes(8192, 30)
            candidate = library / "candidate.bin"
            candidate.write_bytes(data)
            needle = root / "needle.bin"
            needle.write_bytes(data)
            database = root / "romhex.sqlite"

            build = rs.build_index(
                roots=[library],
                db_path=database,
                extensions={".bin"},
                max_bytes=1024 * 1024,
                min_bytes=4096,
                workers=1,
                rebuild=True,
                hash_mode="romhex",
            )
            self.assertEqual(build["hash_mode"], "romhex")
            search = rs.search_index(
                needle_path=needle,
                db_path=database,
                min_percent=1.0,
                limit=5,
                exact_top=5,
                max_bytes=1024 * 1024,
            )
            self.assertEqual(search["hash_mode"], "romhex")
            self.assertEqual(search["results"][0]["evidence"], "VERIFIED_IDENTICAL_BYTES")


if __name__ == "__main__":
    unittest.main(verbosity=2)

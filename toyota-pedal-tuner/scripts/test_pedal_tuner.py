#!/usr/bin/env python3
"""Self-tests for pedal_tuner. Run: python scripts/test_pedal_tuner.py"""
import struct
import pedal_tuner as pt

AXIS = [0, 614, 1408, 2560, 5120, 7680, 9344, 10112, 10752, 11520, 12800, 14592, 17562, 21222, 25600, 25600]
S1 = [0, 358, 538, 1152, 2867, 4608, 6144, 6989, 7680, 8576, 10291, 13158, 17024, 21146, 25600, 25600]
S3 = [0, 512, 1280, 2304, 5120, 8192, 10240, 11008, 11776, 12544, 13824, 15360, 17920, 21248, 25600, 25600]
S4 = [0, 256, 410, 717, 1203, 2099, 2739, 3584, 4710, 6093, 8576, 11776, 15872, 19968, 25600, 25600]
FULL = 25600


def _unit(axis, data, length=16):
    b = bytearray()
    for c in axis + data:
        b += struct.pack("<H", c)
    b += struct.pack("<I", length)
    return b


def build_fake_bin(with_lookalike=False):
    """One pedal block: 3 units of [axis][data][len=16].

    with_lookalike appends a 4th unit whose length field is garbage — the exact
    failure mode seen on a real Prado GRJ152 bin, where the bytes right after the
    real block happen to look like axis+curve. The length field is what rejects it.
    """
    buf = bytearray(b"\x11" * 0x400)
    for data in (S1, S3, S4):
        buf += _unit(AXIS, data)
    if with_lookalike:
        buf += _unit(AXIS, S1, length=7995422)
    buf += b"\x22" * 0x200
    return buf


def test_detect_full():
    assert pt.detect_full(build_fake_bin()) == FULL
    print("ok detect_full")


def test_find_units():
    buf = build_fake_bin()
    units = pt.find_units(buf, FULL)
    assert len(units) == 3, units
    assert pt.read_curve(buf, units[0]) == AXIS
    got = [pt.read_curve(buf, a + 0x20) for a in units]
    assert got == [S1, S3, S4], got
    assert units[1] - units[0] == pt.UNIT
    print("ok find_units (axis/data split, stride 0x44)")


def test_lookalike_rejected():
    """Regression guard: a 4th unit with a garbage length field must NOT be adopted."""
    units = pt.find_units(build_fake_bin(with_lookalike=True), FULL)
    assert len(units) == 3, f"lookalike leaked in: {len(units)} units"
    print("ok lookalike rejected by length field")


def _invariants(nv, old, must_lift=True):
    assert nv[0] == 0, "idle must stay 0"
    assert nv[-1] == FULL, "WOT must stay full"
    assert all(nv[i] <= nv[i + 1] for i in range(15)), "must stay monotonic"
    assert all(v <= FULL for v in nv), "must never exceed full"
    if must_lift:
        assert all(nv[i] >= old[i] for i in range(16)), "must never lower a point"


def test_taper_invariants():
    nv = pt.lighten(S1, 0.25, FULL)
    _invariants(nv, S1)
    assert nv[4] == 3503 and nv[6] == 7311, nv     # 与历史结果保持一致
    print("ok taper invariants")


def test_power_invariants():
    for p in (0.90, 0.85, 0.78):
        nv = pt.lighten_power(S1, p, FULL)
        _invariants(nv, S1)
    print("ok power invariants (all presets)")


def test_neutral_is_noop():
    assert pt.lighten(S1, 0.0, FULL) == S1, "K=0 must be a no-op"
    assert pt.lighten_power(S1, 1.0, FULL) == S1, "p=1 must be a no-op"
    print("ok neutral no-op (K=0 / p=1)")


def test_presets_ordered():
    """柔和 < 正常 < 激进：越激进，同一点抬得越多（WOT 除外，恒等）。"""
    curves = {m: pt.lighten_power(S1, pt.PRESETS[m]["p"], FULL)
              for m in ("soft", "normal", "aggressive")}
    for i in range(1, 15):
        assert curves["soft"][i] <= curves["normal"][i] <= curves["aggressive"][i], \
            f"preset ordering broken at index {i}"
    assert all(c[-1] == FULL for c in curves.values())
    print("ok preset ordering soft < normal < aggressive")


def test_aliases():
    for zh, en in (("柔和", "soft"), ("正常", "normal"), ("激进", "aggressive")):
        assert pt.PRESET_ALIASES[zh] == en
    print("ok chinese preset aliases")


if __name__ == "__main__":
    test_detect_full()
    test_find_units()
    test_lookalike_rejected()
    test_taper_invariants()
    test_power_invariants()
    test_neutral_is_noop()
    test_presets_ordered()
    test_aliases()
    print("ALL PASS")

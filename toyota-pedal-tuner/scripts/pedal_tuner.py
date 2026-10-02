#!/usr/bin/env python3
"""
Toyota/Lexus DENSO accelerator-pedal (drive-by-wire) characteristic tuner.

What it does
------------
1. Locate the accelerator-pedal characteristic map block in a DENSO bin.
   These are 1x16 uint16-LE curves, monotonic 0 -> FULL, ending in a doubled
   FULL value, scaled raw/FACTOR = % (Toyota: FULL=25600, FACTOR=256 -> 0..100%).
   They are packed as repeating blocks of  [X-axis 16w][output 16w][len=16].
   The X-axis (pedal position) is the curve that repeats identically; the
   varied curves are the OUTPUT (requested throttle/torque %), one per
   drive-mode / vehicle-speed bin.
2. Decode every curve to engineering % and write a comparison table.
3. Apply a "light throttle" transform to the OUTPUT curves only (axis untouched).
   Two transforms are available; default is power:

       power  new = FULL * (old/FULL)^p           (p<1; lift concentrated at the
                                                   bottom of travel, eats the dead zone)
       taper  new = old * (1 + K*(1 - old/FULL))   (even lift, fades linearly to 0 at WOT)

   Idle (0) stays 0, WOT stays FULL, monotonicity preserved, never >FULL.
   Presets 柔和/正常/激进: power p=0.90/0.85/0.78 (正常/0.85 = default), taper K=0.15/0.25/0.38.
4. Write <name>_Pedal.bin.  Checksum is NOT recomputed -> Auto-Fix before flash.

Verified on: Lexus ES200 2017, DENSO 89663-33L93, pedal block @ 0x54C98-0x552D4.

Usage
-----
  python pedal_tuner.py file.bin                      # power 幂变换(默认, 正常档 p=0.85), auto-detect, write _Pedal.bin + table
  python pedal_tuner.py file.bin --p 0.90             # gentler (柔和档)
  python pedal_tuner.py file.bin --transform taper    # 线性锥度(默认 K=0.25): 全程均匀变轻
  python pedal_tuner.py file.bin --dry-run            # analyse + table only, no bin written
  python pedal_tuner.py file.bin --full 25600 --factor 256
  python pedal_tuner.py file.bin --data-addrs 0x54C98,0x54CDC,...   # override detection
  python pedal_tuner.py file.bin --axis-addr 0x54C78               # override axis curve
"""
import argparse, struct, os, sys
from collections import Counter

# The uint16-LE codec is canonically owned by the sibling denso-bin-engine skill
# (scripts/denso_bin.py, verified on real projects). Prefer it so codec fixes there
# propagate here; fall back to local struct when this skill is distributed alone.
_DENSO_ENGINE_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "denso-bin-engine", "scripts"))
try:
    sys.path.insert(0, _DENSO_ENGINE_SCRIPTS)
    from denso_bin import _unpack as _db_unpack, _pack_into as _db_pack_into

    def u16(buf, off):
        return _db_unpack(buf, off, "<H")

    def w16(buf, off, val):
        _db_pack_into(buf, off, "<H", val)
except ImportError:
    def u16(buf, off):
        return struct.unpack_from("<H", buf, off)[0]

    def w16(buf, off, val):
        struct.pack_into("<H", buf, off, val)
finally:
    if sys.path and sys.path[0] == _DENSO_ENGINE_SCRIPTS:
        sys.path.pop(0)

COLS = 16
WIDTH = 2  # uint16

CHECKSUM_REMINDER = (
    "\n[!] Checksum NOT recalculated. The written bin is INVALID for flashing until you\n"
    "    Auto-Fix the DENSO checksum (ECM Titanium: Tools -> Checksum -> Auto Fix) or equiv.\n"
    "    Keep the original bin as the recovery baseline.\n"
)


def read_curve(buf, off):
    return [u16(buf, off + 2 * k) for k in range(COLS)]


def is_curve(vals, full):
    """A pedal curve: starts at 0, ends exactly at full-scale, non-decreasing, not degenerate.

    Note: some cars (ES200) end with a *doubled* full value, others (Prado GRJ152) do not.
    Requiring the doubled terminal would silently miss the second family, so we only
    require the final point to be full and the curve to carry real shape information.
    """
    return (
        vals[0] == 0
        and vals[-1] == full
        and all(vals[i] <= vals[i + 1] for i in range(COLS - 1))
        and vals[1] != full                 # reject all-flat
        and len(set(vals)) >= 8             # reject degenerate/step arrays
    )


def detect_full(buf):
    """Guess the full-scale endpoint by the most common doubled terminal value
    among monotonic 0-start 16-word windows."""
    term = Counter()
    n = len(buf) - COLS * WIDTH
    for off in range(0, n, 2):
        if u16(buf, off) != 0:
            continue
        vals = read_curve(buf, off)
        if vals[-1] == vals[-2] and vals[-1] > 4096 and all(
            vals[i] <= vals[i + 1] for i in range(COLS - 1)
        ):
            term[vals[-1]] += 1
    if not term:
        return 25600
    # prefer 25600 if present and common, else the mode
    if term.get(25600, 0) >= 4:
        return 25600
    return term.most_common(1)[0][0]


UNIT = 0x44          # [axis 16w][data 16w][u32 len] —— 一个踏板单元
LEN_OFF = 0x40


def u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def find_units(buf, full):
    """按单元结构找踏板块，返回单元起始地址列表（轴在 a，数据在 a+0x20）。

    关键：单元末尾那个 `u32 == 16` 的长度字段是**判别器**。只靠"曲线形态"会吃假阳性——
    Prado GRJ152 上紧邻真块之后就有一段字节恰好长得像 轴+曲线，但它的长度字段是垃圾。
    加上这一条校验，假阳性直接归零，而且不需要靠"哪条曲线出现次数最多"去猜轴。
    """
    def unit_ok(a):
        return (a + UNIT <= len(buf)
                and u32(buf, a + LEN_OFF) == COLS
                and is_curve(read_curve(buf, a), full)
                and is_curve(read_curve(buf, a + 0x20), full))

    best, a, n = [], 0, len(buf) - UNIT
    while a <= n:
        if u16(buf, a) != 0 or not unit_ok(a):
            a += 2
            continue
        run, b = [], a
        while unit_ok(b):
            run.append(b)
            b += UNIT
        if len(run) > len(best):
            best = run
        a = b
    return best


def scan_curves(buf, full):
    """Return list of (offset, vals) for every pedal-shaped curve, scanning /2."""
    out = []
    off = 0
    n = len(buf) - COLS * WIDTH
    while off <= n:
        vals = read_curve(buf, off)
        if is_curve(vals, full):
            out.append((off, vals))
            off += COLS * WIDTH  # jump past this curve
        else:
            off += 2
    return out


def find_block(curves):
    """Group curves into the largest contiguous cluster (the pedal table)."""
    if not curves:
        return []
    best, cur = [], [curves[0]]
    for prev, nxt in zip(curves, curves[1:]):
        gap = nxt[0] - prev[0]
        if gap <= 0x60:  # curves sit within ~one block stride of each other
            cur.append(nxt)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [nxt]
    if len(cur) > len(best):
        best = cur
    return best


def split_axis_data(block):
    """Most-frequent identical curve = shared X-axis (pedal position).
    The rest = output data curves."""
    freq = Counter(tuple(v) for _, v in block)
    axis_vals = list(freq.most_common(1)[0][0])
    axis = [(o, v) for o, v in block if v == axis_vals]
    data = [(o, v) for o, v in block if v != axis_vals]
    return axis_vals, axis, data


# ---------------------------------------------------------------------------
# Two transforms, three presets.
#
# Both share the same guarantees: 0 stays 0, WOT stays FULL, monotonic preserved,
# never exceeds FULL. They differ in WHERE the lift lands:
#
#   power  new = FULL * (old/FULL)**p          p < 1
#          Lift is concentrated at the very bottom of travel — it eats the
#          dead band. Feels like "the pedal starts working sooner".
#          boost(x) = (x/FULL)**(p-1) - 1   →  p=0.85: +99% at 1%, +11% at 50%, 0 at WOT
#
#   taper  new = old * (1 + K*(1 - old/FULL))
#          Lift is spread evenly and fades linearly to WOT. Feels like
#          "the whole pedal got lighter" rather than "the dead zone shrank".
#          boost(x) = K*(1 - x/FULL)         →  K=0.25: +25% at 1%, +12% at 50%, 0 at WOT
#
# Pick `power` when the complaint is "起步肉 / 死区大 / 不跟脚" (most common).
# Pick `taper` when the complaint is "整体都想再轻一点" and the bottom is already fine.
# ---------------------------------------------------------------------------
PRESETS = {
    "soft":       {"p": 0.90, "k": 0.15, "zh": "柔和"},
    "normal":     {"p": 0.85, "k": 0.25, "zh": "正常"},
    "aggressive": {"p": 0.78, "k": 0.38, "zh": "激进"},
}
# accept Chinese names on the command line too
PRESET_ALIASES = {"柔和": "soft", "正常": "normal", "激进": "aggressive",
                  "温和": "soft", "标准": "normal", "运动": "aggressive"}


def _finish(old, new, full):
    """Shared guards: 0 stays 0, clamp to FULL, keep monotonic."""
    out = old[:]
    prev = 0
    for i in range(1, COLS):
        nv = min(int(round(new[i])), full)
        nv = max(nv, prev)
        out[i] = nv
        prev = nv
    out[0] = 0
    return out


def lighten(old, k, full):
    """Linear-taper transform (the original). new = old*(1 + K*(1 - old/FULL))."""
    raw = [0] + [old[i] * (1 + k * (1 - old[i] / float(full))) for i in range(1, COLS)]
    return _finish(old, raw, full)


def lighten_power(old, p, full):
    """Power transform. new = FULL*(old/FULL)**p.  WOT is mathematically exact, not clamped."""
    raw = [0] + [full * (old[i] / float(full)) ** p if old[i] > 0 else 0
                 for i in range(1, COLS)]
    return _finish(old, raw, full)


def apply_transform(old, full, transform, p, k):
    return lighten_power(old, p, full) if transform == "power" else lighten(old, k, full)


def shape_tag(v, factor):
    """Cheap descriptor of a curve's aggressiveness by its early/mid output."""
    p5 = v[4] / factor   # output at the 5th breakpoint
    if p5 < 6:
        return "very-soft/ECO"
    if p5 < 10:
        return "soft"
    if p5 < 16:
        return "normal"
    return "sport/aggressive"


def fmt_row(label, vals, factor=None):
    if factor:
        return "| " + label + " | " + " | ".join(f"{v/factor:.1f}" for v in vals) + " |"
    return "| " + label + " | " + " | ".join(str(v) for v in vals) + " |"


def build_table(axis_vals, data, factor, full, src, transform, p, k, mode):
    zh = PRESETS.get(mode, {}).get("zh", mode)
    if transform == "power":
        law = f"power  new = FULL*(old/FULL)^{p}"
    else:
        law = f"taper  new = old*(1+{k}*(1-old/FULL))"
    L = []
    L.append(f"# DENSO Accelerator-Pedal Maps — {os.path.basename(src)}")
    L.append("")
    L.append(f"- encoding: uint16-LE, 1x16, scaling = raw / {factor} = % (full = {full} = {full/factor:.0f}%)")
    L.append(f"- {len(data)} output curves + shared pedal-position X-axis")
    L.append(f"- preset: **{mode} / {zh}**   transform: `{law}`")
    L.append("- guarantees: 0 stays 0 · WOT stays 100% · monotonic preserved · never exceeds full")
    L.append("")
    L.append("## X-axis — pedal opening %")
    L.append("| | " + " | ".join(str(i + 1) for i in range(COLS)) + " |")
    L.append("|---|" + "|".join([""] * COLS) + "|")
    L.append(fmt_row("raw", axis_vals))
    L.append(fmt_row("pedal%", axis_vals, factor))
    L.append("")
    L.append("## Output curves (requested %) — original vs tuned")
    for off, vals in sorted(data):
        nv = apply_transform(vals, full, transform, p, k)
        L.append(f"### 0x{off:05X}  [{shape_tag(vals, factor)}]")
        L.append("| | " + " | ".join(str(i + 1) for i in range(COLS)) + " |")
        L.append("|---|" + "|".join([""] * COLS) + "|")
        L.append(fmt_row("orig%", vals, factor))
        L.append(fmt_row("tuned%", nv, factor))
        L.append("| lift | " + " | ".join(
            (f"{(n/o-1)*100:+.0f}%" if o else "—") for o, n in zip(vals, nv)) + " |")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Toyota/Lexus DENSO pedal-curve light-throttle tuner")
    ap.add_argument("binfile")
    ap.add_argument("--mode", default="normal",
                    help="soft/normal/aggressive 或 柔和/正常/激进 (default normal)")
    ap.add_argument("--transform", choices=["power", "taper"], default="power",
                    help="power=幂变换(消死区,默认) taper=线性锥度(全程均匀)")
    ap.add_argument("--p", type=float, default=None, help="幂指数，覆盖 preset")
    ap.add_argument("--k", type=float, default=None, help="线性锥度系数，覆盖 preset")
    ap.add_argument("--full", type=int, default=None, help="full-scale raw (default auto/25600)")
    ap.add_argument("--factor", type=float, default=256.0, help="raw/factor=eng (default 256)")
    ap.add_argument("--out", default=None, help="output bin (default <name>_Pedal.bin)")
    ap.add_argument("--export", default=None, help="table path (default <name>_pedal_table.md)")
    ap.add_argument("--data-addrs", default=None, help="comma hex list, override data curves")
    ap.add_argument("--axis-addr", default=None, help="hex, override X-axis curve")
    ap.add_argument("--dry-run", action="store_true", help="analyse only, do not write bin")
    args = ap.parse_args()

    mode = PRESET_ALIASES.get(args.mode, args.mode).lower()
    if mode not in PRESETS:
        print(f"未知档位 {args.mode!r}；可用: soft/normal/aggressive 或 柔和/正常/激进", file=sys.stderr)
        sys.exit(2)
    p = args.p if args.p is not None else PRESETS[mode]["p"]
    k = args.k if args.k is not None else PRESETS[mode]["k"]

    buf = bytearray(open(args.binfile, "rb").read())
    full = args.full or detect_full(buf)
    law = f"power p={p}" if args.transform == "power" else f"taper K={k}"
    print(f"full-scale = {full} (= {full/args.factor:.0f}%)   档位 = {mode}/{PRESETS[mode]['zh']}   变换 = {law}")

    if args.data_addrs:
        data = [(int(a, 16), read_curve(buf, int(a, 16))) for a in args.data_addrs.split(",")]
        axis_vals = read_curve(buf, int(args.axis_addr, 16)) if args.axis_addr else None
    else:
        units = find_units(buf, full)
        if units:
            axis_vals = read_curve(buf, units[0])
            data = [(a + 0x20, read_curve(buf, a + 0x20)) for a in units]
            print(f"块 0x{units[0]:05X}-0x{units[-1] + UNIT:05X}: {len(units)} 个单元"
                  f"（轴+数据+长度字段校验通过），X 轴 @0x{units[0]:05X}")
            bad = [a for a in units if read_curve(buf, a) != axis_vals]
            if bad:
                print(f"  注意: {len(bad)} 个单元的轴与首个不同 —— 该块可能不是共享轴，先 --dry-run 核对")
        else:
            # 回退：没有长度字段的镜像，用形态+众数拆轴（弱，务必 --dry-run 核对）
            block = find_block(scan_curves(buf, full))
            if not block:
                print("未找到踏板曲线。试试 --full / --data-addrs 手动指定。", file=sys.stderr)
                sys.exit(2)
            axis_vals, axis, data = split_axis_data(block)
            print(f"[回退检测] 块 0x{block[0][0]:05X}-0x{block[-1][0]:05X}: "
                  f"{len(data)} 条输出曲线, 轴 x{len(axis)} —— 无长度字段校验，请 --dry-run 核对")

    # comparison table
    if axis_vals is None:
        axis_vals = [0] * COLS
    table = build_table(axis_vals, data, args.factor, full, args.binfile,
                        args.transform, p, k, mode)
    exp = args.export or os.path.splitext(args.binfile)[0] + f"_pedal_{mode}.md"
    open(exp, "w", encoding="utf-8").write(table)
    print(f"[written] {exp}")

    if args.dry_run:
        print("dry-run: bin not modified.")
        return

    changed = 0
    for off, vals in data:
        nv = apply_transform(vals, full, args.transform, p, k)
        for i in range(COLS):
            if nv[i] != vals[i]:
                changed += 1
            w16(buf, off + 2 * i, nv[i])
    out = args.out or os.path.splitext(args.binfile)[0] + f"_Pedal_{mode}.bin"
    open(out, "wb").write(buf)

    # 交付前自检 —— 这三条错一条就别刷
    ok_mono = ok_wot = ok_zero = True
    for off, vals in data:
        nv = [u16(buf, off + 2 * i) for i in range(COLS)]
        if any(nv[i + 1] < nv[i] for i in range(COLS - 1)):
            ok_mono = False
        if nv[-1] != full:
            ok_wot = False
        if nv[0] != 0:
            ok_zero = False
    print(f"[written] {out}  ({len(data)} curves, {changed} cells changed)")
    print("自检: 单调 %s   WOT=满量程 %s   起点=0 %s"
          % ("✔" if ok_mono else "✘", "✔" if ok_wot else "✘", "✔" if ok_zero else "✘"))
    if not (ok_mono and ok_wot and ok_zero):
        print("!! 自检未通过，不要刷这个文件 —— 多半是 --full/--factor 或 --data-addrs 给错了")
    print(CHECKSUM_REMINDER)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scale_convert.py — raw ↔ 工程值批量换算（winols-scaling-converter 技能的执行脚本）。

整条轴/整张表手工换算容易错一位、错一个符号；本脚本把 WinOLS 约定
    phys = raw × factor + offset
的正反换算、hex 字节串解码、两已知点反解 factor/offset 做成可验证的批量操作。

用法:
    # raw -> 工程值（逗号/空格分隔，支持 0x 前缀）
    python scale_convert.py to-phys --factor 0.75 --offset -128 --values "0,64,128,192,255"

    # 工程值 -> raw（自动按数据类型钳位并提示越界）
    python scale_convert.py to-raw --factor 0.023438 --dtype u8 --values "0.5,1.0,3.0"

    # hex 字节串直接解码成工程值（--dtype 定宽度/符号，--endian 定字节序）
    python scale_convert.py decode --hex "00 20 00 40 FF 7F" --dtype u16 --endian le --factor 0.001526

    # 两个已知点反解 factor/offset：raw1:phys1 raw2:phys2
    python scale_convert.py solve --points "0:-128 255:63.25"

    python scale_convert.py --selftest

纪律（doctrine）:
  * 不确定 scaling 时不要写 ECU——先用 solve/decode 在纯软件层验证一个已知点。
  * 本脚本只做线性换算；查表/分段/多项式换算不适用，须回技能正文人工处理。
  * 换算结果服务于定义确认，字节写盘交 bin 引擎，写盘后 checksum 交 checksum-crc-engine。
"""
import sys, struct, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DTYPES = {
    "u8":  ("B", 1, 0, 0xFF),
    "s8":  ("b", 1, -0x80, 0x7F),
    "u16": ("H", 2, 0, 0xFFFF),
    "s16": ("h", 2, -0x8000, 0x7FFF),
    "u32": ("I", 4, 0, 0xFFFFFFFF),
    "s32": ("i", 4, -0x80000000, 0x7FFFFFFF),
    "f32": ("f", 4, None, None),
    "f64": ("d", 8, None, None),
}


def to_phys(raw, factor, offset):
    return raw * factor + offset


def to_raw(phys, factor, offset, dtype=None):
    """返回 (raw, clamped)。整型时四舍五入并按类型钳位。"""
    if factor == 0:
        raise ValueError("factor 不能为 0")
    r = (phys - offset) / factor
    if dtype and dtype in DTYPES and DTYPES[dtype][2] is not None:
        lo, hi = DTYPES[dtype][2], DTYPES[dtype][3]
        ri = int(round(r))
        clamped = ri < lo or ri > hi
        return max(lo, min(hi, ri)), clamped
    return r, False


def parse_values(s):
    out = []
    for tok in s.replace(",", " ").split():
        out.append(int(tok, 0) if tok.lower().startswith("0x") else float(tok))
    return out


def decode_hex(hexstr, dtype, endian="le"):
    data = bytes.fromhex(hexstr.replace(",", " ").replace("0x", " ").replace(" ", ""))
    fmt_ch, width, _, _ = DTYPES[dtype]
    if len(data) % width:
        raise ValueError("字节数 %d 不是 %s 宽度 %d 的整数倍" % (len(data), dtype, width))
    n = len(data) // width
    e = "<" if endian == "le" else ">"
    return list(struct.unpack(e + str(n) + fmt_ch, data))


def solve_two_points(r1, p1, r2, p2):
    """由 (raw1,phys1),(raw2,phys2) 解 phys = raw*factor + offset。"""
    if r1 == r2:
        raise ValueError("两个 raw 值相同，无法解线性方程")
    factor = (p2 - p1) / (r2 - r1)
    offset = p1 - r1 * factor
    return factor, offset


def fmt_num(v):
    if isinstance(v, float):
        return ("%.6f" % v).rstrip("0").rstrip(".")
    return str(v)


def print_pairs(raws, physes, extra=None):
    w = max(len(fmt_num(r)) for r in raws) + 2
    print("%-*s %-14s %s" % (w, "raw", "phys", extra or ""))
    print("-" * (w + 16 + (len(extra or ""))))
    for i, (r, p) in enumerate(zip(raws, physes)):
        hexs = ""
        if isinstance(r, (int,)) and r >= 0:
            hexs = "(0x%X)" % r
        print("%-*s %-14s %s" % (w, fmt_num(r), fmt_num(p), hexs))


def cmd_to_phys(a):
    raws = parse_values(a.values)
    physes = [to_phys(r, a.factor, a.offset) for r in raws]
    print("phys = raw × %g %+g   [%s]" % (a.factor, a.offset, a.unit or "?unit"))
    print_pairs(raws, physes)


def cmd_to_raw(a):
    physes = parse_values(a.values)
    out, warn = [], []
    for p in physes:
        r, clamped = to_raw(p, a.factor, a.offset, a.dtype)
        out.append(r)
        if clamped:
            warn.append(p)
    print("raw = (phys %+g×-1... ) / %g -> %s" % (a.offset, a.factor, a.dtype or "float"))
    print("phys = raw × %g %+g   [%s]" % (a.factor, a.offset, a.unit or "?unit"))
    print_pairs(out, physes)
    if warn:
        print("[!] 越界已钳位的工程值: %s —— 该数据类型装不下，检查 scaling 或换类型"
              % ", ".join(fmt_num(x) for x in warn))


def cmd_decode(a):
    raws = decode_hex(a.hex, a.dtype, a.endian)
    physes = [to_phys(r, a.factor, a.offset) for r in raws]
    print("%s %s ×%g %+g  [%s]" % (a.dtype, a.endian.upper(), a.factor, a.offset,
                                   a.unit or "?unit"))
    print_pairs(raws, physes)
    lo, hi = min(physes), max(physes)
    print("phys 值域: %s .. %s   —— 与物理量常识核对（离谱=scaling/字节序/类型错）"
          % (fmt_num(lo), fmt_num(hi)))


def cmd_solve(a):
    pts = []
    for tok in a.points.replace(",", " ").split():
        r, p = tok.split(":")
        pts.append((float(int(r, 0) if r.lower().startswith("0x") else float(r)), float(p)))
    if len(pts) < 2:
        raise ValueError("至少两个点: raw1:phys1 raw2:phys2")
    factor, offset = solve_two_points(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
    print("phys = raw × %.10g %+.10g" % (factor, offset))
    inv = 1.0 / factor if factor else float("inf")
    print("即 factor=%.10g offset=%.10g （倒数 1/factor=%.6g，供对照 Divisor 型工具）"
          % (factor, offset, inv))
    if len(pts) > 2:
        print("其余点校验:")
        for r, p in pts[2:]:
            calc = to_phys(r, factor, offset)
            ok = abs(calc - p) <= max(1e-6, abs(p) * 0.005)
            print("  raw=%s -> phys=%s (期望 %s) %s"
                  % (fmt_num(r), fmt_num(calc), fmt_num(p), "ok" if ok else "MISMATCH"))
    print("[!] 反解结果必须再用第三个已知点或传感器表交叉验证后才可用于写入。")


def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("ok " if cond else "FAIL ") + msg)
        ok = ok and cond

    chk(to_phys(128, 0.75, -128) == -32.0, "to_phys 128*0.75-128=-32")
    r, c = to_raw(-32.0, 0.75, -128, "s8")
    chk(r == 128 - 256 or r == 127, "to_raw s8 越界钳位")  # (−32+128)/0.75=128 > s8 max
    chk(c is True, "越界标志置位")
    r2, c2 = to_raw(20.0, 0.75, -128, "u8")
    chk(r2 == 197 and not c2, "to_raw u8 (20+128)/0.75≈197")
    vals = decode_hex("00 20 00 40", "u16", "le")
    chk(vals == [0x2000, 0x4000], "decode u16 LE")
    vals_be = decode_hex("00 20 00 40", "u16", "be")
    chk(vals_be == [0x0020, 0x0040], "decode u16 BE")
    f, o = solve_two_points(0, -128.0, 255, 63.25)
    chk(abs(f - 0.75) < 1e-9 and abs(o + 128) < 1e-9, "solve 两点反解 0.75/-128")
    lam = decode_hex("80 00", "u16", "be")[0] * 0.0078125
    chk(abs(lam - 256.0) < 1e-9, "λ 换算示例(0x8000 BE ×1/128)")
    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="raw <-> phys linear scaling batch converter")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, need_scale=True):
        if need_scale:
            p.add_argument("--factor", type=float, required=True)
            p.add_argument("--offset", type=float, default=0.0)
            p.add_argument("--unit", default=None)

    p1 = sub.add_parser("to-phys", help="raw 列表 -> 工程值")
    common(p1)
    p1.add_argument("--values", required=True)

    p2 = sub.add_parser("to-raw", help="工程值列表 -> raw（按 dtype 钳位）")
    common(p2)
    p2.add_argument("--values", required=True)
    p2.add_argument("--dtype", choices=sorted(DTYPES), default=None)

    p3 = sub.add_parser("decode", help="hex 字节串 -> 工程值")
    common(p3)
    p3.add_argument("--hex", required=True)
    p3.add_argument("--dtype", choices=sorted(DTYPES), required=True)
    p3.add_argument("--endian", choices=["le", "be"], default="le")

    p4 = sub.add_parser("solve", help="两已知点反解 factor/offset")
    p4.add_argument("--points", required=True, help='如 "0:-128 255:63.25 [更多点做校验]"')

    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.cmd == "to-phys":
        cmd_to_phys(a)
    elif a.cmd == "to-raw":
        cmd_to_raw(a)
    elif a.cmd == "decode":
        cmd_decode(a)
    elif a.cmd == "solve":
        cmd_solve(a)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

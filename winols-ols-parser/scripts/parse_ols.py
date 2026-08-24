#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_ols.py — 从 WinOLS 工程文件 (.ols / .kp) 中提取标定 MAP 定义。

WinOLS 的 .ols/.kp 是专有二进制工程格式：文件头是明文(长度前缀 ASCII 串)，
工程主体含「元素(map)定义树」——每个 map 一条记录，含命名、地址、维度、
factor/offset。本脚本逆向这套结构，把命名+地址+维度+换算抠出来，
等效于一份「已套好的 DAMOS」。

用法:
    python parse_ols.py PROJECT.ols
    python parse_ols.py PROJECT.ols --bin ORIGINAL.bin        # 交叉核对地址处的 raw 值域
    python parse_ols.py PROJECT.ols --range 0x180000-0x1FFFFF # 手动指定标定区地址范围
    python parse_ols.py PROJECT.ols --json out.json           # 同时输出 JSON

注意:
  * WinOLS 在这些工程里把 map 地址存为「bin 文件偏移」(已在 JLR MED17.8.3 上验证)；
    若你的平台不是，请用 --bin 交叉核对后再信。
  * 部分工程主体是压缩的——若提不到命名，说明该工程 map 树被压缩，请直接用 WinOLS 打开。
  * factor/offset 为「尽力而为」探测，必须再经 winols-scaling-converter 或 WinOLS 属性确认。
"""
import sys, struct, json, argparse, re

try:
    sys.stdout.reconfigure(encoding="utf-8")  # 避免 Windows 控制台中文乱码
except Exception:
    pass

# ---- WinOLS 头部里属于工程元数据、不是 map 名的串 ----
HEADER_SKIP = {
    "WinOLS File", "Passenger car", "Complete binary file", "OLS 5.0 (Windows)",
    "Engine", "Eprom", "English", "Original", "Hexdump", "My maps", "Tables",
    "Potential maps", "Version 1", "intern", "US", "IV", "NOCS",
    # WinOLS 工程里的「功能组/文件夹」名，不是 map：
    "Air control", "Engine torque", "Injection system", "Spark Advance",
    "Volumetric system", "Volumetric pressure",
}


def is_txt(b):
    return all(0x20 <= c < 0x7f or c == 0x09 for c in b)


def lp_strings(buf):
    """返回所有 u32 长度前缀的 ASCII 串 (offset, end, text) —— WinOLS 串存储格式。"""
    N = len(buf)
    out = []
    i = 0
    while i < N - 4:
        L = buf[i] | (buf[i + 1] << 8) | (buf[i + 2] << 16) | (buf[i + 3] << 24)
        if 1 <= L <= 200 and i + 4 + L <= N:
            seg = buf[i + 4:i + 4 + L]
            if is_txt(seg) and sum(1 for c in seg if c > 0x20) >= max(2, L * 0.6):
                out.append((i, i + 4 + L, seg.decode("latin1")))
                i += 4 + L
                continue
        i += 1
    return out


def u32le(buf, o):
    return buf[o] | (buf[o + 1] << 8) | (buf[o + 2] << 16) | (buf[o + 3] << 24)


def u16le(buf, o):
    return buf[o] | (buf[o + 1] << 8)


def cal_addrs_after(buf, pos, lo, hi, window=700, maxn=2):
    """名字之后窗口内，落在标定地址区 [lo,hi] 的前若干个 u32 = 该 map 的 起始/结束 地址。
    跳过 0x1000 对齐的值——那些是工程里的字段常量(大小/计数/段基址)，不是真实 map 地址。"""
    res = []
    N = len(buf)
    for k in range(pos, min(pos + window, N - 4)):
        v = u32le(buf, k)
        if lo <= v <= hi and (v & 0xFFF) != 0:
            res.append(v)
            if len(res) >= maxn:
                break
    return res


def auto_range(buf, strs):
    """没给 --range 时，从「描述串后面的非对齐 u32(真实 map 地址)」自动推断标定地址区间。"""
    cand = []
    for (s, e, t) in strs:
        tt = t.strip()
        if len(tt) < 8 or not (" " in tt or "(" in tt):
            continue
        for k in range(e, min(e + 700, len(buf) - 4)):
            v = u32le(buf, k)
            if 0x100000 <= v < len(buf) and (v & 0xFFF) != 0:  # 非对齐 = 像真实地址
                cand.append(v)
                break
    if not cand:
        return (0x100000, len(buf))
    cand.sort()
    n = len(cand)
    lo = cand[max(0, int(n * 0.05))]
    hi = cand[min(n - 1, int(n * 0.95))]
    lo = (lo // 0x10000) * 0x10000
    hi = ((hi // 0x10000) + 1) * 0x10000
    return (lo, hi)


def probe_doubles(buf, pos, window=300):
    """名字记录窗口内的 IEEE double，尽力而为找 factor / 显示量程 —— 仅供参考。"""
    out = []
    N = len(buf)
    seen = set()
    for k in range(pos, min(pos + window, N - 8)):
        try:
            dv = struct.unpack("<d", buf[k:k + 8])[0]
        except Exception:
            continue
        if dv != 0 and 1e-6 < abs(dv) < 1e6 and dv == dv:  # not NaN
            key = round(abs(dv), 6)
            if key not in seen:
                seen.add(key)
                out.append(round(dv, 8))
    return out[:6]


def parse(path, rng=None, binpath=None):
    buf = open(path, "rb").read()
    strs = lp_strings(buf)
    header = [t for (_, _, t) in strs[:14]]

    if rng:
        lo, hi = rng
    else:
        lo, hi = auto_range(buf, strs)

    bin_buf = open(binpath, "rb").read() if binpath else None

    maps = []
    seen = set()
    for (s, e, t) in strs:
        tt = t.strip()
        if len(tt) < 8:
            continue
        if tt in HEADER_SKIP or tt.endswith(".ols") or tt.endswith(".kp"):
            continue
        if not (" " in tt or "(" in tt):
            continue
        # 跳过工程头/车型/内存段等非 map 描述
        if tt in ("Land Rover", "Range Rover") or tt.startswith("0 (Original"):
            continue
        if re.match(r"^\d+\.\d+\s+[A-Za-z]", tt):      # 排量描述 如 "3.0 TDV6"
            continue
        if re.match(r"^\d+:\s*[0-9A-Fa-f]+-[0-9A-Fa-f]+", tt):  # 内存段 "1: 180000-1FDFFF"
            continue
        addrs = cal_addrs_after(buf, e, lo, hi, 700, 8)
        if not addrs:
            continue
        # 优先找「size 合理(2..4096B)的相邻地址对」= map 起始/结束；找不到则取首个候选
        start, end, size = addrs[0], None, None
        for i in range(len(addrs) - 1):
            d2 = addrs[i + 1] - addrs[i]
            if 2 <= d2 <= 4096:
                start, end, size = addrs[i], addrs[i + 1], d2
                break
        rawlo = rawhi = None
        if bin_buf and size and 2 <= size <= 4096 and start + size <= len(bin_buf):
            vals = [u16le(bin_buf, start + 2 * i) for i in range(size // 2)]
            rawlo, rawhi = min(vals), max(vals)
        key = (tt, start)
        if key in seen:
            continue
        seen.add(key)
        maps.append({
            "name": tt, "address": start, "end": end, "size_bytes": size,
            "raw_min": rawlo, "raw_max": rawhi,
            "scale_doubles": probe_doubles(buf, e),
        })

    return {"file": path, "size": len(buf), "header": header,
            "cal_range": [lo, hi], "map_count": len(maps), "maps": maps}


def report(res):
    print("=" * 78)
    print("WinOLS 工程解析:", res["file"], "(%d bytes)" % res["size"])
    print("头部元数据:", " / ".join(res["header"][:10]))
    print("标定地址区(推断): 0x%06X - 0x%06X" % tuple(res["cal_range"]))
    print("识别到的命名 MAP:", res["map_count"])
    print("=" * 78)
    print("%-50s %-10s %-8s %s" % ("MAP 命名", "地址", "字节", "raw 值域(若给 --bin)"))
    print("-" * 78)
    for m in res["maps"]:
        addr = "0x%06X" % m["address"]
        size = str(m["size_bytes"]) if m["size_bytes"] else "?"
        rng = ("%d..%d" % (m["raw_min"], m["raw_max"])) if m["raw_min"] is not None else ""
        print("%-50s %-10s %-8s %s" % (m["name"][:50], addr, size, rng))
    print("-" * 78)
    print("提醒: 地址=文件偏移(需用 --bin 或 WinOLS 验证); factor/offset 必须经",
          "winols-scaling-converter / WinOLS 属性确认; checksum 写后交 checksum-crc-engine。")


def main():
    ap = argparse.ArgumentParser(description="Parse WinOLS .ols/.kp project for map definitions")
    ap.add_argument("ols")
    ap.add_argument("--bin", dest="binpath", default=None)
    ap.add_argument("--range", dest="rng", default=None, help="e.g. 0x180000-0x1FFFFF")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    rng = None
    if a.rng:
        p = a.rng.replace(" ", "").split("-")
        rng = (int(p[0], 0), int(p[1], 0))
    res = parse(a.ols, rng=rng, binpath=a.binpath)
    report(res)
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("JSON ->", a.json)


if __name__ == "__main__":
    main()

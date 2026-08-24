#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winols-noread-remover / noread_remove.py
----------------------------------------
自动检测并去除 WinOLS/EVC 在 ECU bin 文件里写入的 NOREAD（再导入禁令 / MSG2383）保护页脚。

设计原则（安全第一）：
  1. 只处理"明文"页脚（NOREAD / EVC.EVC / Tag: 等可见标记）。
  2. 标记必须位于"空闲填充区"（前方有一段单一字节的连续填充，如 0xC3 / 0xFF / 0x00）。
     —— 若标记嵌在活动数据里，判定为"隐藏式/结构式水印"，拒绝改动并提示人工/EVC 插件处理。
  3. 还原成该分区的原生填充字节，绝不触碰活动 MAP 数据 → ECU 自身 checksum 不受影响。
  4. 永远先备份原文件。

用法:
  python3 noread_remove.py <input.bin> [output.bin]
  python3 noread_remove.py <input.bin> --scan      # 只扫描、不修改
"""
import sys, os, shutil
from collections import Counter

# 明文标记（按特异性排序，越靠前越可信）
MARKERS = [b"NOREAD", b"EVC.EVC", b"EVC\x00\x00"]
SECONDARY = [b"Tag:", b"WinOLS", b".OLS"]

FILL_CANDIDATES = {0xC3, 0xFF, 0x00, 0xAA, 0x55}  # 常见空闲填充
PRE_WINDOW   = 256     # 标记前用于判定填充的窗口
MIN_PRE_RUN  = 32      # 标记前至少这么多连续填充字节，才认定其处于空闲区
TERM_RUN     = 1024    # 这么长的连续填充视为页脚结束（终端 padding），避免被页脚内部小间隙提前截断
MAX_BLOCK    = 8192    # 页脚最大跨度安全上限，超过则判异常拒绝处理


def find_markers(d):
    hits = []
    for m in MARKERS:
        i = 0
        while True:
            i = d.find(m, i)
            if i < 0:
                break
            hits.append((i, m))
            i += 1
    return sorted(set(hits))


def detect_local_fill(d, pos):
    """返回 (fill_byte, pre_run_len)。pre_run_len = 标记前连续同一字节的长度。"""
    start = max(0, pos - PRE_WINDOW)
    pre = d[start:pos]
    if not pre:
        return None, 0
    cand = pre[-1]
    run = 0
    for b in reversed(pre):
        if b == cand:
            run += 1
        else:
            break
    return cand, run


def block_end(d, start, fill):
    """从 start 向后找页脚结束位置：遇到 >=TERM_RUN 的连续填充(任一 FILL_CANDIDATES)即结束。"""
    n = len(d)
    last_nonfill = start
    i = start
    fillset = FILL_CANDIDATES | {fill}
    while i < n:
        if d[i] in fillset:
            # 测量这段填充
            j = i
            while j < n and d[j] in fillset:
                j += 1
            if (j - i) >= TERM_RUN or j >= n:
                break          # 进入终端 padding，页脚到此为止
            i = j              # 内部小间隙，跳过继续
        else:
            last_nonfill = i
            i += 1
        if i - start > MAX_BLOCK:
            break
    return last_nonfill + 1


def process(path, out=None, scan_only=False):
    d = bytearray(open(path, "rb").read())
    orig = bytes(d)
    print(f"[i] 文件: {os.path.basename(path)}  大小: {len(d)} (0x{len(d):X})")

    hits = find_markers(d)
    if not hits:
        print("[!] 未发现明文 NOREAD/EVC 标记。")
        print("    → 很可能是新版【隐藏式/加密 EVC 水印】，不在明文里，本脚本不处理。")
        print("    → 需用 EVC 原版插件 / 原始 mod / decrypt 软件处理，切勿盲目改字节。")
        return 1

    print(f"[i] 命中明文标记 {len(hits)} 处:")
    for pos, m in hits:
        print(f"    0x{pos:06X}  {m.decode('latin1')!r}")

    # 把同一页脚里的多个标记聚成块（按位置排序，落在已识别页脚内的标记静默归并）
    blocks = []
    for pos, m in sorted(hits):
        # 已被某个已识别页脚覆盖 → 同一页脚的次级标记，静默跳过
        if any(blk['start'] <= pos < blk['end'] for blk in blocks):
            continue
        fill, prerun = detect_local_fill(d, pos)
        if fill is None or prerun < MIN_PRE_RUN or fill not in FILL_CANDIDATES:
            print(f"[X] 0x{pos:06X} {m.decode('latin1')!r} 前方不是空闲填充区"
                  f"(prerun={prerun}, fill={fill})。")
            print("    → 标记疑似嵌在活动数据中（隐藏式/结构式），为安全起见拒绝改动该处。")
            continue
        bstart, bend = pos, block_end(d, pos, fill)
        # 合并相邻/重叠块
        merged = False
        for blk in blocks:
            if not (bend <= blk['start'] or bstart >= blk['end']):
                blk['start'] = min(blk['start'], bstart)
                blk['end']   = max(blk['end'], bend)
                merged = True
                break
        if not merged:
            blocks.append({'start': bstart, 'end': bend, 'fill': fill})

    if not blocks:
        print("[!] 没有可安全处理的明文页脚（全部被判为嵌入活动数据）。未做改动。")
        return 2

    for blk in blocks:
        s, e, f = blk['start'], blk['end'], blk['fill']
        print(f"[+] 可清理页脚: 0x{s:06X}–0x{e-1:06X} ({e-s} 字节) → 还原为原生填充 0x{f:02X}")

    if scan_only:
        print("[i] --scan 模式，仅扫描，未修改。")
        return 0

    for blk in blocks:
        s, e, f = blk['start'], blk['end'], blk['fill']
        d[s:e] = bytes([f]) * (e - s)

    # 备份 + 写出
    if out is None:
        root, ext = os.path.splitext(path)
        out = root + "_NOREAD_removed" + (ext or ".bin")
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"[i] 已备份原文件 → {bak}")
    open(out, "wb").write(d)

    # 校验
    nd = bytes(d)
    remaining = [m.decode('latin1') for m in MARKERS if m in nd]
    diffs = [i for i in range(len(orig)) if orig[i] != nd[i]]
    print("-" * 60)
    print(f"[✓] 输出: {out}")
    print(f"[✓] 大小不变: {len(orig) == len(nd)} ({len(nd)})")
    print(f"[✓] 改动字节数: {len(diffs)}")
    if diffs:
        print(f"[✓] 改动范围: 0x{min(diffs):06X}–0x{max(diffs):06X}")
    print(f"[✓] 残留明文标记: {remaining if remaining else '无'}")
    print("[i] 该页脚位于空闲填充区，不属于 ECU 固件被校验的数据 → ECU 自身 checksum 不受影响。")
    print("[i] 建议：把输出文件在 WinOLS 中作为【全新原始文件 import】，导出时再跑一遍 checksum 模块确认。")
    return 0 if not remaining else 3


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__); return 1
    scan = "--scan" in args
    args = [a for a in args if a != "--scan"]
    inp = args[0]
    out = args[1] if len(args) > 1 else None
    if not os.path.isfile(inp):
        print(f"[X] 找不到文件: {inp}"); return 1
    return process(inp, out, scan_only=scan)


if __name__ == "__main__":
    sys.exit(main())

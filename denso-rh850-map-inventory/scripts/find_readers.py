#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 1+2：平台侦察 + 查表例程(reader)候选

阶段 1  --recon
    确认是不是 V850/RH850、基址、入口、代码区范围。这步不过关就别往下走。

阶段 2  (默认)
    扫全文件里 `MOV imm32, rX` 后面近距离跟一条 `JARL` 的配对，把 JARL 目标做直方图。
    被大量【不同常数】指向的目标就是 reader 候选。

    ⚠ 候选不等于 reader。必须逐个反汇编看开头十几条再判定：
        python v850dis.py <bin> <候选地址> 0x60
      真 reader：读描述符头(ld.hu 取 cols)、算行距、搜轴、插值、DIVQ
      假阳性：CAN 打包(sst.b + andi 0x23/0x50/0x8C + tst1/set1)、
              参数访问器(andi 0xC000 + 指针表)、消息队列(stsr sr5 + ei/di)

用法:
    python find_readers.py fw.bin --recon
    python find_readers.py fw.bin
    python find_readers.py fw.bin --top 40 --window 52
"""
import argparse
import collections
import math
import re
import struct
import sys


def sx(v, n):
    m = 1 << (n - 1)
    return (v ^ m) - m


def load(p):
    return open(p, "rb").read()


def recon(buf):
    N = len(buf)
    h = lambda a: struct.unpack_from("<H", buf, a)[0]
    print("文件大小 : 0x%X (%d)" % (N, N))

    print("\n--- 平台字符串 ---")
    hit = False
    for tok in (b"RX-OSEK850", b"OSEK", b"Renesas", b"RH850", b"V850", b"DENSO", b"TOYOTA"):
        i = buf.find(tok)
        if i >= 0:
            print("  0x%06X  %s" % (i, tok.decode()))
            hit = True
    if not hit:
        print("  未命中已知平台字符串 —— 先用 multi-vendor-ecu-engine 定核与字节序")

    print("\n--- 复位向量 / 中断向量表 ---")
    hw0, hw1 = h(0), h(2)
    if (hw0 >> 6) & 0x1F == 0x1E:
        disp = sx(((hw0 & 0x3F) << 16) | hw1, 22)
        print("  0x0000: %04X %04X = JR  → 入口 0x%06X" % (hw0, hw1, disp))
        print("  ⇒ V850/RH850 形态，Flash 基址 = 0x00000000")
    else:
        print("  0x0000: %04X %04X —— 不是 JR，可能不是本平台" % (hw0, hw1))
    stub = sum(1 for a in range(0x10, 0x200, 0x10) if h(a) == 0x0780 and h(a + 2) == 0)
    print("  16 字节间距的 `JR .` 未用向量: %d 个%s" % (stub, "  （典型 RH850 向量表）" if stub > 3 else ""))

    print("\n--- ASCII 零件号候选 ---")
    for m in re.finditer(rb"[0-9]{5}-[0-9A-Z]{5}", buf[:0x200000]):
        print("  0x%06X  %s" % (m.start(), m.group().decode()))

    print("\n--- 分区熵（32 KiB 块，H<3 多为填充/数据区）---")
    for blk in range(0, N, 0x8000):
        c = buf[blk:blk + 0x8000]
        if not c:
            break
        f = collections.Counter(c)
        ent = -sum((v / len(c)) * math.log2(v / len(c)) for v in f.values())
        if ent < 3.0 or blk % 0x40000 == 0:
            print("  0x%06X  H=%.2f  FF=%4.1f%%  00=%4.1f%%"
                  % (blk, ent, c.count(0xFF) * 100 / len(c), c.count(0) * 100 / len(c)))


def scan(buf, window, top):
    N = len(buf)
    h = lambda a: struct.unpack_from("<H", buf, a)[0]
    w = lambda a: struct.unpack_from("<I", buf, a)[0]

    def jarl_at(a):
        """a 处是不是 JARL？是则返回目标。注意排除 LD.BU（hw2 bit0=1）"""
        if a + 4 > N:
            return None
        hw = h(a)
        if ((hw >> 6) & 0x1F) != 0x1E:
            return None
        hw2 = h(a + 2)
        if hw2 & 1:                       # LD.BU，不是调用
            return None
        if ((hw >> 11) & 0x1F) == 0:      # JR，不是 JARL
            return None
        return a + sx(((hw & 0x3F) << 16) | hw2, 22)

    pairs = []
    for a in range(0, N - 6, 2):
        hw = h(a)
        if not (0x0620 <= hw <= 0x063F):          # MOV imm32, reg1
            continue
        const = w(a + 2)
        if const >= N:                            # 常数必须指向镜像内部
            continue
        k = a + 6
        while k < a + 6 + window:
            t = jarl_at(k)
            if t is not None:
                if 0 <= t < N:
                    pairs.append((const, t, a))
                break
            k += 2

    tgt = collections.Counter(t for _, t, _ in pairs)
    dist = collections.defaultdict(set)
    for c, t, _ in pairs:
        dist[t].add(c)

    print("找到 %d 组 (MOV imm32 → JARL) 配对，命中 %d 个不同目标\n" % (len(pairs), len(tgt)))
    print("%-10s %8s %8s   %s" % ("目标", "配对数", "不同常数", "判定提示"))
    print("-" * 78)
    for t, n in tgt.most_common(top):
        d = len(dist[t])
        hint = "★ 强候选，去反汇编确认" if d >= 30 else ("候选" if d >= 8 else "")
        print("  0x%06X %8d %8d   %s" % (t, n, d, hint))
    print("\n下一步：对每个 ★ 候选跑")
    print("    python v850dis.py <bin> <地址> 0x60")
    print("看开头是不是「读描述符头 → 算行距 → 搜轴 → 插值」。参见 references/reader-layouts.md")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="DENSO/RH850 平台侦察与 reader 候选")
    ap.add_argument("bin")
    ap.add_argument("--recon", action="store_true", help="只做阶段1（平台/基址/入口/熵）")
    ap.add_argument("--window", type=int, default=52, help="MOV 之后多少字节内找 JARL（默认52）")
    ap.add_argument("--top", type=int, default=25, help="打印前 N 个候选")
    args = ap.parse_args()
    data = load(args.bin)
    if args.recon:
        recon(data)
    else:
        scan(data, args.window, args.top)

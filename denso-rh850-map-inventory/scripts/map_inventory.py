#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 3：全表扫描 —— 按已确认的 reader 布局，把所有代码可达的表验证出来，写成 CSV。

核心纪律：**没通过结构校验的不进清单。** 校验 = 维度合理 + 轴单调 + 整块落在文件内。
宁可漏，不可错 —— 清单是后面一切判断的地基。

用法:
    python map_inventory.py fw.bin \
        --reader 0x06590C=A --reader 0x066B10=B --reader 0x065A80=C \
        --reader 0x064DC4=D --reader 0x065444=E --reader 0x0654C4=F \
        --reader 0x064EB0=G \
        -o tables_from_code.csv

布局代号见 references/reader-layouts.md。若某个 reader 的布局和这里预置的都不符，
先反汇编它、把布局加进 CHECKS 再扫。

输出列:
    desc      描述符地址
    reader    布局代号
    bits      8 / 16
    cols,rows 维度
    data      ★ 第一格【数据单元】地址（A/C/F 已跳过行首 Y 轴表头）
    xaxis     X 轴地址
    xmin,xmax X 轴原始值域（未换算）
    ymin,ymax Y 轴原始值域（未换算）
    shift_or_dim3   A/E = 右移位数；B = 第三轴点数；其余 0
    end       表末地址
    ncall     ★ 调用者数 = 影响面
    call1     第一个调用点
"""
import argparse
import collections
import csv
import struct
import sys


def sx(v, n=16):
    m = 1 << (n - 1)
    return (v ^ m) - m


def mono(v):
    """非递减 —— 不要用严格递增：末尾有重复值的轴很常见，严格递增会误杀"""
    return all(v[i + 1] >= v[i] for i in range(len(v) - 1)) and v[-1] > v[0]


class Scanner:
    def __init__(self, buf):
        self.b = buf
        self.N = len(buf)

    def h(self, a):
        return struct.unpack_from("<H", self.b, a)[0]

    def s(self, a):
        return struct.unpack_from("<h", self.b, a)[0]

    def u8(self, a):
        return self.b[a]

    # ---------- 各 reader 布局的结构校验 ----------
    def check(self, d, st):
        try:
            if st == "D":                                     # 1D uint8
                c = self.u8(d)
                if not (3 <= c <= 64) or d + 1 + 2 * c > self.N:
                    return None
                ax = list(self.b[d + 1:d + 1 + c])
                if not mono(ax):
                    return None
                return dict(cols=c, rows=1, xa=d + 1, data=d + 1 + c, sh=0, bits=8,
                            x0=ax[0], x1=ax[-1], y0=0, y1=0, end=d + 1 + 2 * c)

            if st == "F":                                     # 2D uint8, 行内嵌Y轴
                c, r = self.u8(d), self.u8(d + 1)
                if not (3 <= c <= 64 and 2 <= r <= 64):
                    return None
                pit, dat = c + 1, d + 2 + c
                if dat + r * pit > self.N:
                    return None
                ax = list(self.b[d + 2:d + 2 + c])
                if not mono(ax):
                    return None
                yv = [self.u8(dat + i * pit) for i in range(r)]
                if not mono(yv):
                    return None
                return dict(cols=c, rows=r, xa=d + 2, data=dat + 1, sh=0, bits=8,
                            x0=ax[0], x1=ax[-1], y0=yv[0], y1=yv[-1], end=dat + r * pit)

            if st in ("E", "G"):                              # 1D int16 / uint16
                c, sh = self.h(d), self.h(d + 2)
                if not (3 <= c <= 64) or d + 4 + 4 * c > self.N:
                    return None
                if st == "E" and sh > 15:
                    return None
                f = (lambda a: self.s(a)) if st == "E" else (lambda a: self.h(a))
                ax = [f(d + 4 + 2 * i) for i in range(c)]
                if not mono(ax):
                    return None
                return dict(cols=c, rows=1, xa=d + 4, data=d + 4 + 2 * c,
                            sh=sh if st == "E" else 0, bits=16,
                            x0=ax[0], x1=ax[-1], y0=0, y1=0, end=d + 4 + 4 * c)

            c, r, e = self.h(d), self.h(d + 2), self.h(d + 4)   # A / B / C
            if not (2 <= c <= 64 and 2 <= r <= 128):
                return None
            ya = None
            if st == "A":
                if e > 15:
                    return None
                xa, sgn, pit, dat = d + 6, True, c * 2 + 2, d + 6 + c * 2
            elif st == "C":
                xa, sgn, pit, dat, e = d + 4, False, c * 2 + 2, d + 4 + c * 2, 0
            elif st == "B":
                if not (1 <= e <= 32):
                    return None
                xa, sgn, pit = d + 8, True, c * 2
                ya = d + 8 + c * 2
                dat = ya + r * 2 + e * 2
            else:
                return None
            if dat + r * pit > self.N:
                return None
            ax = [self.s(xa + 2 * i) if sgn else self.h(xa + 2 * i) for i in range(c)]
            if not mono(ax):
                return None
            yv = ([self.s(ya + 2 * i) for i in range(r)] if st == "B"
                  else [self.s(dat + i * pit) for i in range(r)])
            if not mono(yv):
                return None
            return dict(cols=c, rows=r, xa=xa, sh=e, bits=16,
                        data=dat + (2 if st in "AC" else 0),
                        x0=ax[0], x1=ax[-1], y0=yv[0], y1=yv[-1], end=dat + r * pit)
        except Exception:
            return None

    # ---------- 扫描调用点 ----------
    def pairs(self, readers, window=52):
        out = collections.defaultdict(list)
        for a in range(0, self.N - 6, 2):
            hw = self.h(a)
            if not (0x0620 <= hw <= 0x063F):
                continue
            const = struct.unpack_from("<I", self.b, a + 2)[0]
            if const >= self.N:
                continue
            k = a + 6
            while k < a + 6 + window:
                if k + 4 > self.N:
                    break
                hh = self.h(k)
                if ((hh >> 6) & 0x1F) == 0x1E:
                    hh2 = self.h(k + 2)
                    if not (hh2 & 1) and ((hh >> 11) & 0x1F):
                        t = k + sx(((hh & 0x3F) << 16) | hh2, 22)
                        if t in readers:
                            out[(const, readers[t])].append(a)
                    break
                k += 2
        return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="DENSO/RH850 代码驱动全表扫描")
    ap.add_argument("bin")
    ap.add_argument("--reader", action="append", required=True,
                    metavar="ADDR=LETTER", help="例: --reader 0x06590C=A （可重复）")
    ap.add_argument("--window", type=int, default=52)
    ap.add_argument("-o", "--out", default="tables_from_code.csv")
    args = ap.parse_args()

    readers = {}
    for spec in args.reader:
        a, _, letter = spec.partition("=")
        letter = letter.strip().upper()
        if letter not in "ABCDEFG":
            sys.exit("未知布局代号 %r，见 references/reader-layouts.md" % letter)
        readers[int(a, 0)] = letter

    sc = Scanner(open(args.bin, "rb").read())
    pairs = sc.pairs(readers, args.window)

    rows, rejected = [], collections.Counter()
    for (d, st), sites in pairs.items():
        i = sc.check(d, st)
        if not i:
            rejected[st] += 1
            continue
        rows.append(dict(desc="0x%05X" % d, reader=st, bits=i["bits"],
                         cols=i["cols"], rows=i["rows"], data="0x%05X" % i["data"],
                         xaxis="0x%05X" % i["xa"], xmin=i["x0"], xmax=i["x1"],
                         ymin=i["y0"], ymax=i["y1"], shift_or_dim3=i["sh"],
                         end="0x%05X" % i["end"], ncall=len(sites),
                         call1="0x%05X" % sites[0]))
    rows.sort(key=lambda r: int(r["desc"], 16))
    if not rows:
        sys.exit("零命中 —— 先确认 reader 地址和布局代号是否正确")

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        wr.writeheader()
        wr.writerows(rows)

    cnt = collections.Counter(r["reader"] for r in rows)
    print("按 reader 统计：")
    for k in sorted(cnt):
        print("   %s : %4d 张   (另有 %d 个配对未通过结构校验，已剔除)" % (k, cnt[k], rejected[k]))
    print("   " + "-" * 46)
    print("   合计 %d 张   2D/3D %d 张   1D %d 张"
          % (len(rows),
             sum(v for k, v in cnt.items() if k in "ABCF"),
             sum(v for k, v in cnt.items() if k in "DEG")))

    ov = collections.Counter(r["ncall"] for r in rows)
    print("\n调用者数分布（= 影响面）: %s" % dict(sorted(ov.items())[:8]))
    hi = sorted((r for r in rows if r["ncall"] >= 3), key=lambda r: -r["ncall"])[:10]
    if hi:
        print("\n影响面最大的表（调用者 ≥3，动手前先看这些）：")
        for r in hi:
            print("   %-9s rd=%s %3dx%-3d data=%-9s Xraw %6s..%-7s calls=%d"
                  % (r["desc"], r["reader"], r["cols"], r["rows"], r["data"],
                     r["xmin"], r["xmax"], r["ncall"]))

    flat = [r for r in rows if r["xmin"] == r["xmax"]]
    print("\nCSV → %s" % args.out)
    print("\n提醒：本清单是【代码可达的表】，不是文件里所有的表。")
    print("      走 movhi+movea 或指针表分派的表扫不到 —— 补漏交 blind-map-finder，")
    print("      并把它的轴判据从『严格递增』放宽到『非递减』。")


if __name__ == "__main__":
    main()

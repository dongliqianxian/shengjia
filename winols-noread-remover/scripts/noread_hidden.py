#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winols-noread-remover / noread_hidden.py
========================================
去除 WinOLS/EVC 的【隐藏式 / 加密式】NOREAD 再导入禁令水印
（WinOLS 5.x 报错 MSG1872 / MSG2383: "marked by another customer with a
 re-import ban (NOREAD, encrypted / hidden)"）。

与同目录 noread_remove.py 的分工
--------------------------------
  noread_remove.py  →  明文页脚水印（bin 里能直接搜到 "NOREAD"/"EVC.EVC"）
  noread_hidden.py  →  隐藏水印：没有任何明文，payload 被【隐写】进真实标定数据的
                       某一个字节车道（byte lane），靠 ±小幅扰动携带比特。

隐藏水印的物理指纹（本脚本的判据）
----------------------------------
WinOLS 挑一段【平滑/单调】的标定数据（典型是线性化表、传感器转换曲线、轴），
只改其中 uint16 的【某一个字节车道】(小端=低字节 / 大端=高字节)，写入
"原值 + 小的有符号偏移"。于是：
  1) 该簇的改动【100% 落在同一奇偶车道】（全偶或全奇偏移）；
  2) 相对物理值的扰动极小（LSB 车道 → 通常 <0.5%），刷写不会出事，肉眼看不出；
  3) 但按【字节】看，原本平滑的曲线被打成锯齿 —— 二阶差分粗糙度暴涨 10~100 倍。
真正的调校改动【绝不会】同时满足 (1) 和 (3)：调校要么两个车道都动（值变化大），
要么单车道动但保持平滑（小幅缩放）。

两种运行模式
------------
A. 【差分模式 / 推荐，结论可证】需要同一软件版本的干净原厂 bin：
     python noread_hidden.py --ori 原厂.bin --mod 带NOREAD.bin [-o 输出.bin]
   逐字节 diff → 聚簇 → 对每簇算「车道纯度 + 粗糙度比」→ 只把判定为水印的簇
   还原成原厂字节。调校改动一个字节都不碰。

B. 【单文件模式 / 只给候选，不自动改】没有原厂 bin 时：
     python noread_hidden.py --mod 带NOREAD.bin --blind
   在整个 bin 里找「单调平滑区里出现单车道锯齿」的区段，列出候选。
   修复需要 --blind-fix，用同车道邻值线性插值重建，属于【推测性修复】，
   必须人工核对后才可用。

铁律
----
  * 没有 --ori 时默认【不写文件】。盲修复会改真实标定数据，必须显式 --blind-fix。
  * 改完的文件校验和必然失效 → 刷写前必须走 WinOLS checksum 模块 /
    ECM Titanium Auto-Fix / 刷写工具的自动校验和。
  * 本脚本只处理隐写水印，不解密、不破解狗、不动 ECU 自身读保护。
"""

import argparse
import os
import shutil
import sys

# ---- 判据阈值（可用命令行覆盖）----------------------------------------------
GAP           = 16     # 聚簇：相距 > GAP 字节视为不同簇
MIN_SLOTS     = 24     # 水印簇至少这么多个改动字节（低于此不足以携带 payload）
LANE_PURITY   = 0.97   # 改动落在同一奇偶车道的比例下限
ROUGH_RATIO   = 4.0    # MOD 粗糙度 / ORI 粗糙度 的下限
ROUGH_ORI_MAX = 3.0    # 原厂该区必须本来就平滑（二阶差分均值上限）
CTX           = 8      # 计算粗糙度时向两侧扩展的字节数


def roughness(seq):
    """平均绝对二阶差分：平滑单调数据 ~0，锯齿噪声 ~几十。"""
    if len(seq) < 3:
        return 0.0
    return sum(abs(seq[i - 1] - 2 * seq[i] + seq[i + 1])
               for i in range(1, len(seq) - 1)) / (len(seq) - 2)


def cluster(idx, gap=GAP):
    if not idx:
        return []
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > gap:
            out.append((s, p))
            s = i
        p = i
    out.append((s, p))
    return out


# ---------------------------------------------------------------- 差分模式 --
def diff_mode(ori_path, mod_path, out_path, apply_fix, args):
    a = open(ori_path, 'rb').read()
    b = bytearray(open(mod_path, 'rb').read())
    if len(a) != len(b):
        print(f"[X] 大小不一致：ORI={len(a)} MOD={len(b)}。必须是同一软件版本的原厂 bin。")
        return 1

    print(f"[i] ORI: {os.path.basename(ori_path)}  MOD: {os.path.basename(mod_path)}  "
          f"size=0x{len(a):X}")
    idx = [i for i in range(len(a)) if a[i] != b[i]]
    if not idx:
        print("[!] 两个文件完全相同。")
        return 1
    cl = cluster(idx)
    print(f"[i] 差异字节 {len(idx)}，聚为 {len(cl)} 簇\n")

    marks, tunes = [], []
    print(f"{'簇范围':<22}{'n':>5}{'车道':>7}{'rgh_ORI':>10}{'rgh_MOD':>10}{'比值':>8}  判定")
    print("-" * 78)
    for s, e in cl:
        cells = [i for i in range(s, e + 1) if a[i] != b[i]]
        ev = sum(1 for i in cells if i % 2 == 0)
        purity = max(ev, len(cells) - ev) / len(cells)
        lane = 'even' if ev >= len(cells) - ev else 'odd'
        lo, hi = max(0, s - CTX), min(len(a), e + 1 + CTX)
        ra, rb = roughness(list(a[lo:hi])), roughness(list(b[lo:hi]))
        ratio = (rb / ra) if ra > 1e-6 else float('inf')
        is_mark = (len(cells) >= args.min_slots
                   and purity >= args.lane_purity
                   and ra <= args.rough_ori_max
                   and ratio >= args.rough_ratio)
        (marks if is_mark else tunes).append((s, e, cells, lane, ra, rb, ratio))
        print(f"0x{s:06X}-0x{e:06X}{len(cells):>5}{lane+f'/{purity*100:.0f}%':>7}"
              f"{ra:>10.2f}{rb:>10.2f}{ratio:>8.2f}  "
              f"{'<== NOREAD 水印' if is_mark else '调校改动'}")

    print("-" * 78)
    if not marks:
        print("[!] 没有任何簇符合【隐藏水印】指纹。可能：")
        print("    - 水印不在这一对文件的差异里（ORI 版本不对？）")
        print("    - 是明文页脚水印 → 改用 noread_remove.py")
        print("    - 是本脚本未覆盖的新式标记 → 需要 EVC 插件处理")
        return 2

    total = sum(len(m[2]) for m in marks)
    print(f"[+] 判定为 NOREAD 隐藏水印：{len(marks)} 簇 / {total} 字节")
    for s, e, cells, lane, ra, rb, ratio in marks:
        print(f"    0x{s:06X}-0x{e:06X}  {len(cells)} 字节  {lane} 车道  "
              f"粗糙度 {ra:.2f} → {rb:.2f} ({ratio:.0f}×)")
    print(f"[i] 其余 {len(tunes)} 簇 / {len(idx)-total} 字节判定为真实调校改动，将【原样保留】。")

    if not apply_fix:
        print("\n[i] --scan 模式，未写文件。")
        return 0

    for s, e, cells, *_ in marks:
        for i in cells:
            b[i] = a[i]

    if out_path is None:
        root, ext = os.path.splitext(mod_path)
        out_path = root + "_NOREAD-removed" + (ext or ".bin")
    bak = mod_path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(mod_path, bak)
        print(f"[i] 已备份 → {bak}")
    open(out_path, 'wb').write(b)

    c = bytes(b)
    d_ori = [i for i in range(len(a)) if a[i] != c[i]]
    print("\n" + "=" * 60)
    print(f"[✓] 输出: {out_path}")
    print(f"[✓] 大小不变: {len(c) == len(a)} (0x{len(c):X})")
    print(f"[✓] 相对 MOD 只改了 {total} 字节（全部为水印车道）")
    if d_ori:
        print(f"[✓] 相对 ORI 仍保留 {len(d_ori)} 字节调校改动 "
              f"(0x{min(d_ori):06X}-0x{max(d_ori):06X})")
    for s, e, cells, *_ in marks:
        lo, hi = max(0, s - CTX), min(len(a), e + 1 + CTX)
        print(f"[✓] 0x{s:06X} 区粗糙度已回到原厂水平: "
              f"{roughness(list(c[lo:hi])):.2f} (ORI {roughness(list(a[lo:hi])):.2f})")
    print("[!] 校验和已失效 → 刷写前务必跑 WinOLS checksum / ECM Titanium Auto-Fix。")
    return 0


# ---------------------------------------------------------------- 盲扫模式 --
def blind_mode(mod_path, out_path, apply_fix, args):
    b = bytearray(open(mod_path, 'rb').read())
    n = len(b)
    print(f"[i] 盲扫: {os.path.basename(mod_path)}  size=0x{n:X}")
    print("[i] 找『整体单调平滑、但单一奇偶车道被打成锯齿』的区段…\n")

    # 隐写载体的必要条件：干净车道本身【单调】(线性化表/轴/转换曲线)，
    # 被注入车道却锯齿。只靠"粗糙度差"会把所有 u16 表的低字节都误报，
    # 因此必须叠加单调性判据。
    def mono_frac(seq):
        if len(seq) < 2:
            return 0.0
        inc = sum(1 for i in range(1, len(seq)) if seq[i] >= seq[i - 1])
        dec = sum(1 for i in range(1, len(seq)) if seq[i] <= seq[i - 1])
        return max(inc, dec) / (len(seq) - 1)

    W = 64          # 滑窗长度（字节）
    STEP = 16
    cands = []
    for off in range(0, n - W, STEP):
        w = list(b[off:off + W])
        if len(set(w)) < 4:                       # 纯填充/常量区跳过
            continue
        ev, od = w[0::2], w[1::2]
        for smooth_l, noisy_l, lane in ((od, ev, 'even'), (ev, od, 'odd')):
            r_s, r_n = roughness(smooth_l), roughness(noisy_l)
            if r_s > args.rough_ori_max:
                continue
            if mono_frac(smooth_l) < 0.98:        # 干净车道必须单调
                continue
            if mono_frac(noisy_l) > 0.80:         # 被注入车道必须已失去单调
                continue
            if r_n < r_s * args.rough_ratio + 3:
                continue
            # 关键去误报判据：普通 u16 表的低字节车道本来就满量程乱跳，与高字节
            # 车道无关；而隐写载体是【u8 单调斜坡】，注入值必须"伪装"成周围的
            # 数值水平，因此与干净车道数值贴近且同向。
            m = min(len(smooth_l), len(noisy_l))
            devs = [abs(noisy_l[i] - smooth_l[i]) for i in range(m)]
            if sum(devs) / m > 32:
                continue
            ms, mn = sum(smooth_l[:m]) / m, sum(noisy_l[:m]) / m
            num = sum((smooth_l[i] - ms) * (noisy_l[i] - mn) for i in range(m))
            den = (sum((smooth_l[i] - ms) ** 2 for i in range(m))
                   * sum((noisy_l[i] - mn) ** 2 for i in range(m))) ** 0.5
            if den == 0 or num / den < 0.5:
                continue
            cands.append((off, lane, r_n / max(r_s, 0.1), r_s, r_n))
            break

    if not cands:
        print("[!] 未找到『单调载体 + 单车道锯齿』特征。可能不是隐写水印，或形态不同。")
        print("    → 强烈建议改用差分模式：拿到同版本原厂 bin 后 --ori 再跑一次。")
        return 2

    # 合并相邻窗口
    merged, cs, ce, lane, sc = [], cands[0][0], cands[0][0] + W, cands[0][1], cands[0][2]
    for off, ln, ratio, *_ in cands[1:]:
        if off <= ce and ln == lane:
            ce = off + W
            sc = max(sc, ratio)
        else:
            merged.append((cs, ce, lane, sc))
            cs, ce, lane, sc = off, off + W, ln, ratio
    merged.append((cs, ce, lane, sc))
    merged.sort(key=lambda x: -x[3])

    print(f"[+] 候选区段 {len(merged)} 处，按可信度降序（仅候选，非结论）：")
    for s, e, ln, sc in merged[:20]:
        print(f"    0x{s:06X}-0x{e:06X}  len={e-s}  被注入车道={ln}  粗糙度比={sc:.0f}×")
    if len(merged) > 20:
        print(f"    …另有 {len(merged)-20} 处可信度更低的候选未列出")
    merged = [(s, e, ln) for s, e, ln, _ in merged]
    print("\n[!] 盲修复 = 用同车道两侧邻值线性插值重建，属于【推测】，会改真实标定数据。")
    print("[!] 只有在拿不到原厂 bin 时才用，且必须人工核对曲线形状。")

    if not (apply_fix and args.blind_fix):
        print("[i] 未写文件（需要同时给 --fix --blind-fix 才会写）。")
        return 0

    for s, e, ln in merged:
        start = s if (s % 2 == 0) == (ln == 'even') else s + 1
        pos = list(range(start, e, 2))
        if len(pos) < 3:
            continue
        lo_v, hi_v = b[pos[0] - 2] if pos[0] >= 2 else b[pos[0]], \
                     b[pos[-1] + 2] if pos[-1] + 2 < n else b[pos[-1]]
        for k, p in enumerate(pos):
            t = k / (len(pos) - 1)
            b[p] = int(round(lo_v + (hi_v - lo_v) * t))
    if out_path is None:
        root, ext = os.path.splitext(mod_path)
        out_path = root + "_NOREAD-blindfix" + (ext or ".bin")
    shutil.copy2(mod_path, mod_path + ".bak")
    open(out_path, 'wb').write(b)
    print(f"[✓] 输出(推测性修复): {out_path}")
    print("[!] 校验和已失效 → 刷写前务必重算。")
    return 0


def main():
    p = argparse.ArgumentParser(
        description="去除 WinOLS/EVC 隐藏式(加密) NOREAD 再导入禁令水印")
    p.add_argument('--ori', help='同软件版本的干净原厂 bin（差分模式，强烈推荐）')
    p.add_argument('--mod', required=True, help='带 NOREAD 的文件')
    p.add_argument('-o', '--out', help='输出文件（默认 <mod>_NOREAD-removed.bin）')
    p.add_argument('--scan', action='store_true', help='只分析，不写文件')
    p.add_argument('--blind', action='store_true', help='无原厂 bin 时的单文件盲扫')
    p.add_argument('--blind-fix', action='store_true', help='允许盲扫模式写出推测性修复')
    p.add_argument('--min-slots', type=int, default=MIN_SLOTS)
    p.add_argument('--lane-purity', type=float, default=LANE_PURITY)
    p.add_argument('--rough-ratio', type=float, default=ROUGH_RATIO)
    p.add_argument('--rough-ori-max', type=float, default=ROUGH_ORI_MAX)
    a = p.parse_args()

    if not os.path.isfile(a.mod):
        print(f"[X] 找不到: {a.mod}")
        return 1
    if a.ori:
        if not os.path.isfile(a.ori):
            print(f"[X] 找不到: {a.ori}")
            return 1
        return diff_mode(a.ori, a.mod, a.out, not a.scan, a)
    if a.blind:
        return blind_mode(a.mod, a.out, not a.scan, a)
    print("[X] 需要 --ori <原厂bin>（差分模式）或 --blind（盲扫模式）。")
    print("    差分模式结论可证、只动水印字节；盲扫只给候选。")
    return 1


if __name__ == '__main__':
    sys.exit(main())

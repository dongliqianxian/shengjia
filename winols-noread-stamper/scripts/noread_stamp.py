#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winols-noread-stamper / noread_stamp.py
=======================================
给【自己交付的】ECU bin 做两件事：

  check   交付前漏标体检 —— 确认 WinOLS 的再导入禁令(NOREAD)真的写进去了，
          并报告 WinOLS 把水印写到了哪张表、把数据打偏了多少。
  stamp   打入【溯源水印】—— 隐写客户号/订单号，物理误差硬上限 1 个 raw count。
  verify  从任意一份可疑文件里读回溯源水印（**不需要原厂 bin**）。
  strip   凭 sidecar 精确还原成打水印之前的样子。

--------------------------------------------------------------------------
本工具【不】伪造 WinOLS 的 NOREAD。
NOREAD 的拦截逻辑在 WinOLS 内部，判据是"标记里解出的客户号 == 本机 EVC 狗的
客户号"。客户号只有 EVC 的狗能签发，所以第三方脚本做不到"别人开不了、你能开"
这种区分。加 NOREAD 只能用你自己的 WinOLS 导出选项 —— 见 SKILL.md。
本工具做的是 NOREAD 覆盖不到的那一半：体检 + 溯源。
--------------------------------------------------------------------------

为什么误差上限定在 1 个 raw count
----------------------------------
实测 WinOLS 在丰田 DENSO 89663-58830 上打的 NOREAD：它挑中了一张 uint8 表
(0x006852-0x006A72，545 格，原厂完美单调)，却按 uint16 只改"低字节"，结果把
每隔一个字节改动了最多 ±14 —— 该表量程只有 0..63，中位相对误差 12%，最坏 -45%，
原厂 0 处下降变成 91 处下降。**WinOLS 的载体挑选在没有定义的平台上是盲猜数据宽度的。**

本工具不重蹈覆辙：
  1) 载体资格只用【高字节车道】判定 —— 我们从不碰高字节，所以资格在打标前后
     逐位不变，verify 时能原样重建候选集，不需要原厂 bin。
  2) 用 |低字节 - 高字节| 的平均距离把"字节复制型的 uint8 斜坡"整类排除掉
     —— 这正是 WinOLS 踩的那个坑。
  3) 只改低字节的【最低位】，每格最多动 1 个 count，且要求 uint16 值 >= 2048
     → 相对误差 <= 1/2048 = 0.049%，是可证明的硬上限。
"""

import argparse
import hashlib
import hmac
import json
import os
import shutil
import struct
import sys

MAGIC_LEN   = 2
PAYLOAD_LEN = 13          # magic2 + ver/copy1 + customer4 + serial4 + crc2
PAYLOAD_BITS = PAYLOAD_LEN * 8
WIN         = 64          # 载体窗口 = 64 个 uint16
COPIES      = 3           # 冗余副本数
MIN_U16     = 2048        # 载体最小 uint16 值 -> 保证 1count 误差 <= 0.049%
MAX_HI      = 245         # 高字节上限，留余量避免接近 0xFFFF 的封顶值
EDGE_SKIP   = 0x4000      # 首尾各跳过 16KB（头部/ID/校验和块）


# ----------------------------------------------------------------- 共用工具 --
def _load_sibling():
    """复用 winols-noread-remover 的 roughness/cluster；取不到就用本地等价实现。"""
    here = os.path.dirname(os.path.abspath(__file__))
    sib = os.path.normpath(os.path.join(here, '..', '..',
                                        'winols-noread-remover', 'scripts'))
    if sib not in sys.path:
        sys.path.insert(0, sib)
    try:
        from noread_hidden import roughness, cluster   # noqa
        return roughness, cluster, True
    except Exception:
        def roughness(seq):
            if len(seq) < 3:
                return 0.0
            return sum(abs(seq[i - 1] - 2 * seq[i] + seq[i + 1])
                       for i in range(1, len(seq) - 1)) / (len(seq) - 2)

        def cluster(idx, gap=16):
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
        return roughness, cluster, False


roughness, cluster, _REUSED = _load_sibling()


def crc16(data):
    """CRC-16/CCITT-FALSE"""
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def keystream(key, n):
    out = b''
    i = 0
    while len(out) < n:
        out += hmac.new(key.encode('utf-8'), b'ks' + struct.pack('>I', i),
                        hashlib.sha256).digest()
        i += 1
    return out[:n]


def magic_of(key):
    return hmac.new(key.encode('utf-8'), b'magic', hashlib.sha256).digest()[:MAGIC_LEN]


# ------------------------------------------------------- 载体资格（核心逻辑）--
def carrier_slots(data, endian='le', exclude=()):
    """
    返回可用低字节偏移的有序列表。

    资格判据【只看高字节车道】，因此打标(只动低字节最低位)前后完全一致，
    verify 才能在没有原厂 bin 的情况下重建同一个候选集。
    """
    lo_off = 0 if endian == 'le' else 1     # 低字节在 uint16 里的字节位置
    hi_off = 1 - lo_off
    n = len(data)
    slots = []
    start = EDGE_SKIP
    end = n - EDGE_SKIP - WIN * 2
    w = WIN * 2

    for base in range(start, end, w):
        if any(base < xe and (base + w) > xs for xs, xe in exclude):
            continue
        hi = [data[base + 2 * k + hi_off] for k in range(WIN)]
        # 低字节一律先把最低位抹掉再参与判定。打标只会翻转最低位（v & 0xFE | bit），
        # 所以 lo 在打标前后逐位相同 —— 资格判定因此是【位精确不变】的，
        # verify 才能在没有原厂 bin 的情况下重建出一模一样的候选集。
        lo = [data[base + 2 * k + lo_off] & 0xFE for k in range(WIN)]

        # (a) uint16 值域：>=2048 保证 1count 误差 <=0.049%；上限留余量避开封顶
        if min(hi) * 256 < MIN_U16 or max(hi) > MAX_HI:
            continue
        # (b) 高字节车道必须平滑（说明这是一张表，不是代码/随机区）
        if roughness(hi) > 3.0:
            continue
        # (c) 不能是填充/常量区
        if len(set(hi)) < 3:
            continue
        # (d) 关键：排除"字节复制型 uint8 斜坡"（WinOLS 踩的坑）。
        #     那种数据 lo 与 hi 几乎相等；真 uint16 表的 lo 与 hi 无关。
        if sum(abs(lo[k] - hi[k]) for k in range(WIN)) / WIN < 40:
            continue
        # (e) 低字节车道本身要够"活"，否则可能是 hi<<8 的对齐数组
        if roughness(lo) < 20:
            continue

        for k in range(WIN):
            slots.append(base + 2 * k + lo_off)
    return slots


def winols_carrier(ori, mod):
    """用差分找出 WinOLS 的 NOREAD 载体（车道纯度 + 粗糙度比），返回 [(s,e),...] 与明细。"""
    if len(ori) != len(mod):
        return [], []
    idx = [i for i in range(len(ori)) if ori[i] != mod[i]]
    hits, info = [], []
    for s, e in cluster(idx):
        cells = [i for i in range(s, e + 1) if ori[i] != mod[i]]
        ev = sum(1 for i in cells if i % 2 == 0)
        purity = max(ev, len(cells) - ev) / len(cells)
        lo, hi = max(0, s - 8), min(len(ori), e + 9)
        ra, rb = roughness(list(ori[lo:hi])), roughness(list(mod[lo:hi]))
        ratio = (rb / ra) if ra > 1e-6 else float('inf')
        if len(cells) >= 24 and purity >= 0.97 and ra <= 3.0 and ratio >= 4.0:
            hits.append((s, e + 1))
            info.append(dict(start=s, end=e, cells=len(cells), ra=ra, rb=rb,
                             ratio=ratio,
                             lane='even' if ev >= len(cells) - ev else 'odd'))
    return hits, info


# --------------------------------------------------------------- check 模式 --
def describe_damage(ori, mod, s, e):
    """把 WinOLS 的水印按 uint8 口径算实际损伤（这才是真实物理影响）。"""
    cells = [(i, ori[i], mod[i]) for i in range(s, e + 1) if ori[i] != mod[i]]
    if not cells:
        return None
    errs = sorted(abs(y - x) / x * 100 for _, x, y in cells if x)
    dec_o = sum(1 for i in range(s + 1, e + 1) if ori[i] < ori[i - 1])
    dec_m = sum(1 for i in range(s + 1, e + 1) if mod[i] < mod[i - 1])
    return dict(n=len(cells), med=errs[len(errs) // 2] if errs else 0,
                worst=errs[-1] if errs else 0, dec_o=dec_o, dec_m=dec_m,
                vmin=min(x for _, x, _ in cells), vmax=max(x for _, x, _ in cells))


def cmd_check(args):
    pairs = []
    if args.batch:
        if not args.ori_dir:
            print('[X] --batch 需要同时给 --ori-dir')
            return 1
        for fn in sorted(os.listdir(args.batch)):
            if not fn.lower().endswith(('.bin', '.ori', '.mod')):
                continue
            cand = os.path.join(args.ori_dir, fn)
            if not os.path.isfile(cand):
                stem = os.path.splitext(fn)[0]
                hit = [f for f in os.listdir(args.ori_dir)
                       if os.path.splitext(f)[0] in stem or stem in os.path.splitext(f)[0]]
                cand = os.path.join(args.ori_dir, hit[0]) if hit else None
            pairs.append((cand, os.path.join(args.batch, fn)))
    else:
        pairs.append((args.ori, args.file))

    print('%-42s %-10s %-24s %s' % ('文件', 'NOREAD', '载体', '按 uint8 口径的实际损伤'))
    print('-' * 108)
    bad = 0
    for orip, modp in pairs:
        name = os.path.basename(modp) if modp else '?'
        if not orip or not os.path.isfile(orip):
            print('%-42s %-10s %s' % (name[:42], '无法判定', '找不到配对的原厂 bin'))
            bad += 1
            continue
        ori = open(orip, 'rb').read()
        mod = open(modp, 'rb').read()
        if len(ori) != len(mod):
            print('%-42s %-10s %s' % (name[:42], '无法判定', '大小不一致，原厂版本不对'))
            bad += 1
            continue
        hits, info = winols_carrier(ori, mod)
        if not hits:
            print('%-42s %-10s %s' % (name[:42], '未检出', '<== 可能漏标，不要发货'))
            bad += 1
            continue
        for h in info:
            d = describe_damage(ori, mod, h['start'], h['end'])
            dmg = ('%d 格  中位 %.0f%%  最坏 %.0f%%  单调性 %d→%d 处下降'
                   % (d['n'], d['med'], d['worst'], d['dec_o'], d['dec_m'])) if d else '-'
            print('%-42s %-10s 0x%06X-0x%06X %-6s %s'
                  % (name[:42], '已带标', h['start'], h['end'], h['lane'], dmg))
            if d and d['worst'] > args.warn_pct:
                print('%-42s %-10s %s' % ('', '',
                      '⚠ 最坏误差超过 %.0f%%：WinOLS 挑中的是一张小量程(uint8?)表，'
                      '这个项目建议关掉自动打标' % args.warn_pct))
    print('-' * 108)
    if bad:
        print('[X] %d 个文件未通过体检 —— 不要发货。' % bad)
    else:
        print('[✓] 全部文件都带 NOREAD 标记。')
    return 1 if bad else 0


# --------------------------------------------------------------- stamp 模式 --
def build_payload(key, customer_id, serial, copy_idx):
    p = bytearray(PAYLOAD_LEN)
    p[0:2] = magic_of(key)
    p[2] = (1 << 4) | (copy_idx & 0x0F)          # version 1 | copy index
    p[3:7] = struct.pack('>I', customer_id & 0xFFFFFFFF)
    p[7:11] = struct.pack('>I', serial & 0xFFFFFFFF)
    p[11:13] = struct.pack('>H', crc16(bytes(p[0:11])))
    ks = keystream(key, PAYLOAD_LEN - 2)
    for i in range(2, PAYLOAD_LEN):
        p[i] ^= ks[i - 2]
    return bytes(p)


def parse_payload(key, raw):
    if len(raw) != PAYLOAD_LEN or raw[0:2] != magic_of(key):
        return None
    p = bytearray(raw)
    ks = keystream(key, PAYLOAD_LEN - 2)
    for i in range(2, PAYLOAD_LEN):
        p[i] ^= ks[i - 2]
    if struct.unpack('>H', bytes(p[11:13]))[0] != crc16(bytes(p[0:11])):
        return None
    return dict(version=p[2] >> 4, copy=p[2] & 0x0F,
                customer_id=struct.unpack('>I', bytes(p[3:7]))[0],
                serial=struct.unpack('>I', bytes(p[7:11]))[0])


def bits_of(b):
    return [(byte >> (7 - k)) & 1 for byte in b for k in range(8)]


def cmd_stamp(args):
    data = bytearray(open(args.file, 'rb').read())
    original = bytes(data)

    exclude = []
    if args.ori:
        ori = open(args.ori, 'rb').read()
        hits, info = winols_carrier(ori, bytes(data))
        for (s, e), h in zip(hits, info):
            exclude.append((max(0, s - 512), min(len(data), e + 512)))
            print('[i] 已锁定 WinOLS 的 NOREAD 载体 0x%06X-0x%06X（%d 格），'
                  '连同前后 512 字节列为禁写区' % (h['start'], h['end'], h['cells']))
        if not hits:
            print('[!] 这个文件里没检出 WinOLS 的 NOREAD 标记。')
            print('    如果它本该带标，先去查导出选项；带不带标都不影响溯源水印写入。')
    else:
        print('[!] 没给 --ori：无法定位并避开 WinOLS 的 NOREAD 载体。')
        print('    强烈建议补上原厂 bin —— 溯源水印压在 NOREAD 载体上会毁掉再导入禁令。')
        if not args.force:
            print('[X] 已中止。确实要不带 --ori 硬写，请加 --force。')
            return 1

    slots = carrier_slots(data, args.endian, exclude)
    need = PAYLOAD_BITS * COPIES
    print('[i] 合格载体槽位 %d 个（需要 %d 个：%d bit × %d 份副本）'
          % (len(slots), need, PAYLOAD_BITS, COPIES))
    if len(slots) < need:
        print('[X] 载体不足。这个文件里符合"高值 uint16 平滑表"条件的区域太少。')
        print('    可以放宽 --copies 到 1 或 2 再试，但抗改动能力会下降。')
        return 1

    if args.dry_run:
        print('[i] --dry-run：不写文件。前 5 段候选：')
        seg, s0, prev = [], slots[0], slots[0]
        for o in slots[1:]:
            if o - prev > 2:
                seg.append((s0, prev)); s0 = o
            prev = o
        seg.append((s0, prev))
        for s, e in seg[:5]:
            print('    0x%06X-0x%06X  %d 槽' % (s, e, (e - s) // 2 + 1))
        print('    …共 %d 段' % len(seg))
        return 0

    # 副本按【地址】铺开，不是按槽位序号 —— 合格载体常常扎堆在一两张大表里，
    # 按序号分只会让三份副本全落在同一块，那块被改动就三份一起丢。
    lo_a, hi_a = slots[0], slots[-1]
    starts, cursor = [], 0
    for c in range(COPIES):
        target = lo_a + (hi_a - lo_a) * c // COPIES
        i = cursor
        while i < len(slots) - PAYLOAD_BITS and slots[i] < target:
            i += 1
        i = max(cursor, min(i, len(slots) - PAYLOAD_BITS))
        starts.append(i)
        cursor = i + PAYLOAD_BITS

    changed = []
    for c, st in enumerate(starts):
        bits = bits_of(build_payload(args.key, args.customer, args.serial, c))
        for j, bit in enumerate(bits):
            off = slots[st + j]
            v = data[off]
            nv = (v & 0xFE) | bit          # 只翻最低位，绝不进位，值最多变 1
            if nv != v:
                changed.append([off, v])
                data[off] = nv

    out = args.out or (os.path.splitext(args.file)[0] + '_traced' +
                       (os.path.splitext(args.file)[1] or '.bin'))
    side = out + '.trace.json'

    # ---- 自检闭环：三条全过才写文件 ----
    ok = True
    # 用打标【之后】的文件重新跑一遍 carrier_slots，证明候选集没被自己的改动挪动
    if carrier_slots(bytes(data), args.endian, exclude) != slots:
        print('[X] 自检失败：打标后候选载体集发生了变化，verify 将无法重建。')
        ok = False
    for c, st in enumerate(starts):
        raw = bytearray(PAYLOAD_LEN)
        for j in range(PAYLOAD_BITS):
            if data[slots[st + j]] & 1:
                raw[j // 8] |= 1 << (7 - j % 8)
        if parse_payload(args.key, bytes(raw)) is None:
            print('[X] 自检失败：副本 %d 读不回来。' % c)
            ok = False
    for xs, xe in exclude:
        if original[xs:xe] != bytes(data[xs:xe]):
            print('[X] 自检失败：禁写区 0x%06X-0x%06X 被动了。' % (xs, xe))
            ok = False
    worst = 0.0
    for off, oldv in changed:
        base = off - (off % 2) if args.endian == 'le' else off - 1
        val = struct.unpack_from('<H' if args.endian == 'le' else '>H',
                                 original, base)[0]
        if val:
            worst = max(worst, 1.0 / val * 100)
    if worst > args.max_pct:
        print('[X] 自检失败：最大相对误差 %.3f%% 超过上限 %.3f%%' % (worst, args.max_pct))
        ok = False
    if not ok:
        print('[X] 未写出任何文件。')
        return 1

    open(out, 'wb').write(bytes(data))
    json.dump(dict(source=os.path.basename(args.file), endian=args.endian,
                   copies=COPIES, customer_id=args.customer, serial=args.serial,
                   changed=changed),
              open(side, 'w'), indent=1)

    print('=' * 68)
    print('[✓] 输出: %s' % out)
    print('[✓] sidecar: %s   （strip 还原要用它，和文件一起存档）' % side)
    print('[✓] 改动 %d 个字节，每个只翻转最低位，值最多变 1' % len(changed))
    print('[✓] 副本落点: %s'
          % ' / '.join('0x%06X' % slots[st] for st in starts))
    print('[✓] 最大相对误差 %.3f%%（上限 %.3f%%）' % (worst, args.max_pct))
    print('[✓] %d 份副本全部回读成功' % COPIES)
    if exclude:
        print('[✓] WinOLS 的 NOREAD 载体逐字节未动')
    print('[i] 交付顺序：WinOLS 导出(带 NOREAD) → 本工具 stamp → 交付。')
    print('[i] stamp 之后不要再回 WinOLS 重新导出，否则水印会被覆盖。')
    return 0


# -------------------------------------------------------------- verify 模式 --
def cmd_verify(args):
    data = open(args.file, 'rb').read()
    slots = carrier_slots(data, args.endian)
    print('[i] 重建候选载体：%d 槽（不需要原厂 bin）' % len(slots))
    if len(slots) < PAYLOAD_BITS:
        print('[X] 候选槽位不足，这个文件里没有可用载体。')
        return 2

    lsb = [data[o] & 1 for o in slots]
    found = {}
    for start in range(0, len(lsb) - PAYLOAD_BITS + 1):
        raw = bytearray(PAYLOAD_LEN)
        for j in range(PAYLOAD_BITS):
            if lsb[start + j]:
                raw[j // 8] |= 1 << (7 - j % 8)
        if raw[0:2] != magic_of(args.key):
            continue
        got = parse_payload(args.key, bytes(raw))
        if got:
            found.setdefault(got['copy'], (start, got))

    if not found:
        print('[X] 没有读到有效水印。可能是：密钥不对 / 这不是你出的文件 /')
        print('    三份副本所在的表都被改过了。')
        return 2

    any_v = list(found.values())[0][1]
    print('=' * 60)
    print('[✓] 命中溯源水印   副本 %d/%d 存活' % (len(found), COPIES))
    print('    客户号   : %d' % any_v['customer_id'])
    print('    订单/序列: %d' % any_v['serial'])
    print('    版本     : %d' % any_v['version'])
    for c in sorted(found):
        print('    副本 %d 起始于第 %d 个槽位（文件偏移 0x%06X）'
              % (c, found[c][0], slots[found[c][0]]))
    if len(found) < COPIES:
        print('[!] 有副本读不回来 —— 这份文件很可能被别人改过了。')
    return 0


# --------------------------------------------------------------- strip 模式 --
def cmd_strip(args):
    side = args.sidecar or (args.file + '.trace.json')
    if not os.path.isfile(side):
        print('[X] 找不到 sidecar: %s' % side)
        print('    没有它就无法精确还原。请用你的干净母版。')
        return 1
    meta = json.load(open(side))
    data = bytearray(open(args.file, 'rb').read())
    n = 0
    for off, oldv in meta['changed']:
        if data[off] != oldv:
            data[off] = oldv
            n += 1
    out = args.out or (os.path.splitext(args.file)[0] + '_clean' +
                       (os.path.splitext(args.file)[1] or '.bin'))
    open(out, 'wb').write(bytes(data))
    print('[✓] 还原 %d 个字节 → %s' % (n, out))
    print('[i] 校验：和打水印前的文件做 md5 比对应完全一致。')
    return 0


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(
        description='ECU bin 交付防护：NOREAD 漏标体检 + 溯源水印')
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('check', help='交付前体检：WinOLS 的 NOREAD 写进去了吗')
    c.add_argument('--ori', help='对应的原厂 bin')
    c.add_argument('--file', help='待交付文件')
    c.add_argument('--batch', help='待交付目录（批量）')
    c.add_argument('--ori-dir', help='原厂 bin 目录（配合 --batch）')
    c.add_argument('--warn-pct', type=float, default=3.0,
                   help='WinOLS 损伤超过这个百分比就告警（默认 3）')
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser('stamp', help='打入溯源水印')
    s.add_argument('--file', required=True, help='WinOLS 导出的待交付 bin')
    s.add_argument('--ori', help='原厂 bin（用于定位并避开 NOREAD 载体，强烈建议给）')
    s.add_argument('--key', required=True, help='你的私有密钥，只有你知道')
    s.add_argument('--customer', type=int, required=True, help='客户编号（整数）')
    s.add_argument('--serial', type=int, default=0, help='订单号或日期，如 20260822')
    s.add_argument('-o', '--out')
    s.add_argument('--endian', choices=['le', 'be'], default='le')
    s.add_argument('--max-pct', type=float, default=0.05, help='相对误差上限（默认 0.05%%）')
    s.add_argument('--dry-run', action='store_true', help='只看载体，不写文件')
    s.add_argument('--force', action='store_true', help='允许不给 --ori 硬写')
    s.set_defaults(fn=cmd_stamp)

    v = sub.add_parser('verify', help='从可疑文件读回水印（不需要原厂 bin）')
    v.add_argument('--file', required=True)
    v.add_argument('--key', required=True)
    v.add_argument('--endian', choices=['le', 'be'], default='le')
    v.set_defaults(fn=cmd_verify)

    t = sub.add_parser('strip', help='凭 sidecar 还原')
    t.add_argument('--file', required=True)
    t.add_argument('--sidecar')
    t.add_argument('-o', '--out')
    t.set_defaults(fn=cmd_strip)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""a2l_diff_attribute.py — diff 两份 bin,把每处改动归属到 A2L 命名标定对象(带边界验证),
并可选地用关键词清单做"功能对象触及数"交叉核对(鉴别 功能关闭 vs DTC屏蔽)。

用法:
  python a2l_diff_attribute.py --ori ORI.bin --mod MOD.bin --a2l file.a2l \
      [--base 0x80000000] [--gap 16] [--keyword "MSA|Msa|StartStop"] [--values]

判读规则(真伪鉴别):
  --keyword 给出的功能命名家族, touched=0 => 该 MOD 没碰任何功能标定 => 是 DTC 屏蔽/假关闭。
  归属结果中 INSIDE=边界已验证; NOT-VERIFIED=对象尺寸未知(共享轴等), 须人工确认。
"""
import argparse, re, bisect, struct, sys

DT = {'UBYTE':1,'SBYTE':1,'UWORD':2,'SWORD':2,'ULONG':4,'SLONG':4,
      'A_UINT64':8,'A_INT64':8,'FLOAT32_IEEE':4,'FLOAT64_IEEE':8}

def parse_a2l(path):
    txt = open(path, 'r', encoding='latin-1').read()
    rl = {}
    for m in re.finditer(r'/begin\s+RECORD_LAYOUT\s+(\S+)(.*?)/end\s+RECORD_LAYOUT', txt, re.S):
        fnc = re.search(r'FNC_VALUES\s+\S+\s+(\w+)', m.group(2))
        rl[m.group(1)] = fnc.group(1) if fnc else None
    chars = {}
    pat = (r'/begin\s+CHARACTERISTIC\s+(\S+)\s+"([^"]*)"\s+(\w+)\s+'
           r'(0x[0-9A-Fa-f]+)\s+(\S+)(.*?)/end\s+CHARACTERISTIC')
    for m in re.finditer(pat, txt, re.S):
        name, desc, typ, addr, depo, body = m.groups()
        dsz = DT.get(rl.get(depo), 0)
        size = None
        if typ == 'VALUE':
            size = dsz
        elif typ == 'VAL_BLK':
            nm = re.search(r'NUMBER\s+(\d+)', body)
            size = int(nm.group(1)) * dsz if nm else None
        elif typ in ('CURVE', 'MAP', 'CUBOID', 'CUBE_4', 'CUBE_5'):
            nums = re.findall(r'/begin\s+AXIS_DESCR.*?NO_AXIS_PTS\s+(\d+)', body, re.S)
            if nums:
                n = 1
                for x in nums: n *= int(x)
                size = n * dsz
        chars[name] = dict(desc=desc, typ=typ, addr=int(addr, 16), size=size)
    return chars

def diff_clusters(x, y, gap):
    diffs = [i for i in range(min(len(x), len(y))) if x[i] != y[i]]
    if not diffs: return []
    out, s, p = [], diffs[0], diffs[0]
    for i in diffs[1:]:
        if i - p > gap: out.append((s, p)); s = i
        p = i
    out.append((s, p))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ori', required=True); ap.add_argument('--mod', required=True)
    ap.add_argument('--a2l', required=True)
    ap.add_argument('--base', default='0x80000000')
    ap.add_argument('--gap', type=int, default=16)
    ap.add_argument('--keyword', default=None)
    ap.add_argument('--values', action='store_true')
    a = ap.parse_args()
    base = int(a.base, 16)

    ori = open(a.ori, 'rb').read(); mod = open(a.mod, 'rb').read()
    if len(ori) != len(mod):
        print(f'WARN: size differs ORI={len(ori)} MOD={len(mod)}', file=sys.stderr)
    chars = parse_a2l(a.a2l)
    objs = sorted((v['addr'], k) for k, v in chars.items())
    addrs = [x for x, _ in objs]

    def attribute(off):
        mem = base + off
        i = bisect.bisect_right(addrs, mem) - 1
        if i < 0: return None
        addr, n = objs[i]; c = chars[n]
        inside = c['size'] is not None and (mem - addr) < c['size']
        return n, c, mem - addr, inside

    clusters = diff_clusters(ori, mod, a.gap)
    print(f'diff: {len(clusters)} clusters, {sum(e-s+1 for s,e in clusters)} bytes')
    touched = {}
    for s, e in clusters:
        for off in range(s, e + 1):
            r = attribute(off)
            if not r: continue
            n, c, d, inside = r
            rec = touched.setdefault(n, [d, d, inside, s])
            rec[0] = min(rec[0], d); rec[1] = max(rec[1], d)
            rec[2] = rec[2] and inside
    for n, (d0, d1, inside, first_off) in sorted(touched.items(), key=lambda kv: kv[1][3]):
        c = chars[n]
        flag = 'INSIDE' if inside else 'NOT-VERIFIED'
        print(f'{n}  [{c["typ"]} size={c["size"]} {flag}] obj+{d0}..+{d1}')
        print(f'    desc: {c["desc"][:130]}'.encode('ascii', 'replace').decode())
        if a.values and c['typ'] == 'VALUE' and c['size'] in (1, 2, 4):
            off = c['addr'] - base
            fmt = {1: '<B', 2: '<H', 4: '<I'}[c['size']]
            vo = struct.unpack_from(fmt, ori, off)[0]
            vm = struct.unpack_from(fmt, mod, off)[0]
            print(f'    value: ORI 0x{vo:0{2*c["size"]}X} -> MOD 0x{vm:0{2*c["size"]}X}')

    if a.keyword:
        kw = re.compile(a.keyword)
        kws = [(n, v) for n, v in chars.items() if kw.search(n)]
        hit = [n for n, v in kws if n in touched]
        print(f'\nkeyword /{a.keyword}/ : {len(kws)} characteristics, touched by MOD: {len(hit)}')
        for n in sorted(hit): print(f'    TOUCHED: {n}')

if __name__ == '__main__':
    main()

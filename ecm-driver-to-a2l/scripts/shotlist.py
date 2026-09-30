#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 5 - the smallest set of ECM screenshots that lifts the most objects to grade A.

Objects sharing (category, unit, width, ambiguity, 64 KB segment) share one encoding in
practice, so a screenshot of the largest member settles the whole group.
Priority (a screenshot is the user's time - spend it where the file is least sure):
  tier 0  ambiguous byte encodings            - must shoot, the A2L may be wrong
  tier 1  values not integer-like             - factor genuinely unproven
  tier 2  integer-like values (rpm, km/h ...) - factor very likely right, optional
  0/1 switches are never worth a screenshot and are only counted.
Also lists tree gaps, categories with fewer rows than the tree showed, and groups whose [N]
copies could not be located (the 'map 2/N' title bar shows copy 2's address).
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ecmlib as L  # noqa: E402

TIER_TXT = {0: '字节编码多解 / 名字与数值对不上 —— 必须截，A2L 可能是错的',
            1: '数值不是整数量级、factor 未证 —— 优先',
            2: '整数量（转速 / 车速等）、factor 大概率对 —— 可选'}


def tier(o):
    if o.get('ambiguous') or o.get('label_conflict'):
        return 0
    return 1 if o.get('round', 0) < 1 else 2


def plan(objs, tree=None, limit=20):
    cand = [o for o in objs if o.get('grade') == 'C' and o.get('cells')]
    flags = [o for o in cand if o.get('flag')]
    need = [o for o in cand if not o.get('flag')]
    groups = collections.defaultdict(list)
    for o in need:
        groups[(o['cat'], o['unit'], o['w'], bool(o.get('ambiguous')), o['addr'] >> 16)].append(o)
    order = sorted(groups.items(), key=lambda kv: (min(tier(m) for m in kv[1]), -len(kv[1]),
                                                   -max(m['cells'] for m in kv[1])))
    shots = []
    for key, members in order[:limit]:
        rep = max(members, key=lambda x: ((x['r'] > 1 and x['c'] > 1), x['cells']))
        shots.append(dict(key=key, rep=rep, members=members, tier=min(tier(m) for m in members)))
    stride = [o for o in objs if o.get('cells') and not o.get('stride_ok', True)]
    gaps = [o for o in objs if o.get('grade') == 'GAP']
    missing = []
    if tree:
        exp = tree.get('expect') or {}
        by = collections.Counter(r['cat'] for r in tree['rows'])
        missing = [(c, exp[c] - by.get(c, 0)) for c in exp if by.get(c, 0) < exp[c]]
    return dict(shots=shots, stride=stride, gaps=gaps, missing=missing, need=len(need), flags=len(flags),
                groups=len(groups))


def render(p):
    shots = p['shots']
    covered = sum(len(s['members']) for s in shots)
    must = sum(1 for s in shots if s['tier'] < 2)
    out = ["# ECM 截图补拍清单", "",
           f"需要证据的 C 级对象 {p['need']} 个（另有 0/1 开关 {p['flags']} 个，不需要截图），分成 {p['groups']} 组。",
           f"下面 **{len(shots)} 张**覆盖 **{covered}** 个；其中**前 {must} 张**是必须 / 优先的，其余可选。", "",
           "每张必须截到：**标题栏**（地址、`map k/N`、Allowed Values）+ **两个轴** + **至少一整行有变化的数据**。", ""]
    if p['missing'] or p['gaps']:
        out += ["## 1. 驱动树抄录缺口（先补）", ""]
        for c, k in p['missing']:
            out.append(f"- 分类 **{c}**：树里显示的数量比清单多 {k} 个 → 展开该分类重截")
        for o in p['gaps']:
            out.append(f"- `{o['name']}` @0x{o['addr']:06X}：份数 / 维度没截全 → 把窗口拉宽重截这一行")
        out.append("")
    out += ["## 2. 刻度截图（一张解决一组）", ""]
    last = None
    for i, s in enumerate(shots, 1):
        if s['tier'] != last:
            out += [f"**{TIER_TXT[s['tier']]}**", ""]
            last = s['tier']
        cat, unit, w, amb, _ = s['key']
        rep = s['rep']
        tag = f"{cat} / {unit or '无单位'} / {'字节' if w == 1 else '字'}"
        others = [m for m in s['members'] if m is not rep]
        names = '、'.join(f"`{m['name']}`" for m in others[:6]) + (f" 等共 {len(others)} 个" if len(others) > 6 else '')
        out.append(f"{i}. 打开 **{rep['name']}** @0x{rep['addr']:06X}（{rep['r']}×{rep['c']}）— {tag}"
                   + (f"；同组：{names}" if others else ''))
    out.append("")
    if p['stride']:
        out += ["## 3. 副本位置（ECM 标了 N 份，只定位到前几份）", ""]
        for o in p['stride']:
            out.append(f"- **{o['name']}** @0x{o['addr']:06X}：ECM 标 [{o['cnt']}]，确认 {o['copies']} 份 → "
                       f"在 ECM 里翻到 `map {o['copies'] + 1}/{o['cnt']}`，截标题栏（显示该份地址）")
        out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--objects')
    ap.add_argument('--tree')
    ap.add_argument('--out', default='shotlist.md')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    p = plan(L.jload(a.objects), L.jload(a.tree) if a.tree else None, a.limit)
    open(a.out, 'w', encoding='utf-8').write(render(p))
    t = collections.Counter(s['tier'] for s in p['shots'])
    print(f"C objects needing evidence {p['need']} in {p['groups']} groups (+{p['flags']} switches skipped); "
          f"{len(p['shots'])} screenshots cover {sum(len(s['members']) for s in p['shots'])} "
          f"[must {t.get(0, 0)} / priority {t.get(1, 0)} / optional {t.get(2, 0)}]; tree gaps {len(p['gaps'])}; "
          f"missing rows {sum(k for _, k in p['missing'])}; groups with unlocated copies {len(p['stride'])}   -> {a.out}")
    return 0


def selftest():
    def ob(name, addr, grade, unit='factor', w=2, amb=False, rnd=0.0, flag=False, cnt=1, copies=1):
        return dict(cat='T', name=name, unit=unit, addr=addr, r=4, c=4, cells=16, w=w, grade=grade, ambiguous=amb,
                    round=rnd, flag=flag, cnt=cnt, copies=copies, stride_ok=copies >= cnt)
    objs = [ob('a', 0x100, 'C', rnd=1), ob('b', 0x200, 'C', rnd=1), ob('c', 0x300, 'C', rnd=1),
            ob('d', 0x400, 'C', unit='deg BTDC', w=1, amb=True), ob('e', 0x500, 'C', unit='deg BTDC', w=1, amb=True),
            ob('sw', 0x600, 'C', unit='', w=1, flag=True),
            ob('f', 0x700, 'A', cnt=4, copies=2),
            dict(cat='T', name='gap', addr=0x800, grade='GAP', cells=None)]
    tree = {'rows': [{'cat': 'T'}] * 8, 'expect': {'T': 8, 'Rail': 1}}
    p = plan(objs, tree)
    ok = ([(s['tier'], len(s['members'])) for s in p['shots']] == [(0, 2), (2, 3)] and p['flags'] == 1
          and len(p['stride']) == 1 and len(p['gaps']) == 1 and p['missing'] == [('Rail', 1)])
    p2 = plan([dict(o, grade='A') for o in objs[:6]])
    ok2 = p2['shots'] == []
    print(f"  ambiguous first, switches skipped, gaps / copies / missing category: {ok}   "
          f"negative control (all A -> no shots): {ok2}")
    print("selftest:", "PASS" if ok and ok2 else "FAIL")
    return 0 if ok and ok2 else 1


if __name__ == '__main__':
    sys.exit(main())

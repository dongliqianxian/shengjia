#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 4 - write the A2L (and optionally an HTML inventory), then self-check it.

Naming    identifier       <ECM name>__<short>[_cK]      (A2L identifiers cannot hold '(' ')')
          LONG_IDENTIFIER  "<ECM name> (<short>) [copy k/N] - <function> | RxC dtype xfactor unit
                            | range lo..hi | evidence G"
          short = ECM MAP_ID from ecm30.db, or --short names.json {"0xADDR": "KF_NAME"}
Grouping  ROOT GROUP per ECM category, SUB_GROUP per family (name without '#N')
Display   physical FORMAT "%10.2", pure index axes "%6.0"; COEFFS keep full precision
Limits    the data type's representable physical range (ECM's 'Allowed Values'), never the
          current data min/max - a limit sitting on the current value blocks raising a limiter
Axes      stored array found by closure.py   -> COM_AXIS + AXIS_PTS
          evenly spaced, not stored          -> FIX_AXIS_PAR_DIST
          displayed but not located          -> /begin FIX_AXIS_PAR_LIST ... /end FIX_AXIS_PAR_LIST
          ECM's own '--' 1..n index / unknown -> index FIX_AXIS_PAR_DIST (unknown = UNRESOLVED)
"""
import argparse
import collections
import hashlib
import html
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ecmlib as L  # noqa: E402

IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_.\[\]]*$')


def _off(o):
    return (('+' if o >= 0 else '') + L.num(o)) if o else ''


class Emitter:
    def __init__(self, clo, buf):
        self.clo, self.buf = clo, buf
        self.cm, self.cmdef = collections.OrderedDict(), collections.OrderedDict()
        self.axpts = collections.OrderedDict()
        self.rls = set()
        self.stat = collections.Counter()

    def cm_for(self, f, o, unit, index=False):
        key = (repr(float(f)), repr(float(o)), unit or '', index)
        if key in self.cm:
            return self.cm[key]
        nm = 'CM_INDEX' if index else 'CM_' + L.ident(
            f"{unit or 'raw'}_{L.num(f)}_{L.num(o)}".replace('.', 'p').replace('-', 'n'), 80)
        base, k = nm, 2
        while nm in self.cmdef:
            nm, k = f"{base}_{k}", k + 1
        self.cm[key] = nm
        self.cmdef[nm] = (float(f), float(o), unit or '-', index)
        return nm

    def axis_descr(self, obj, which, n, ind='    '):
        c = self.clo.get(obj['addr'])
        ax = ((c or {}).get('axes') or {}).get(which) if c else None
        if ax and ax.get('n') == n and ax['kind'] != 'ecm_index':
            unit = (c.get('xunit') if which == 'x' else c.get('yunit')) or '-'
            if ax['kind'] == 'stored':
                key = (ax['addr'], ax['w'], ax['sg'], n, repr(ax['factor']), repr(ax['offset']))
                if key not in self.axpts:
                    self.axpts[key] = ('AX_' + L.ident(f"{unit}_{n}_{ax['addr']:06X}", 60), dict(ax, unit=unit))
                nm = self.axpts[key][0]
                cm = self.cm_for(ax['factor'], ax['offset'], unit)
                lo, hi = L.phys_range(ax['w'], ax['sg'], ax['factor'], ax['offset'])
                self.stat['stored'] += 1
                return [f"{ind}/begin AXIS_DESCR COM_AXIS NO_INPUT_QUANTITY {cm} {n} {L.num(lo)} {L.num(hi)}",
                        f"{ind}  AXIS_PTS_REF {nm}", f"{ind}/end AXIS_DESCR"]
            cm = self.cm_for(1.0, 0.0, unit)
            vals = ax['values']
            lo, hi = min(vals), max(vals)
            head = f"{ind}/begin AXIS_DESCR FIX_AXIS NO_INPUT_QUANTITY {cm} {n} {L.num(lo)} {L.num(hi)}"
            if ax['kind'] == 'fix_dist':
                self.stat['fix_dist'] += 1
                return [head, f"{ind}  FIX_AXIS_PAR_DIST {L.num(ax['start'])} {L.num(ax['step'])} {n}",
                        f"{ind}/end AXIS_DESCR"]
            self.stat['fix_list'] += 1
            return [head, f"{ind}  /begin FIX_AXIS_PAR_LIST " + " ".join(L.num(v) for v in vals) +
                    " /end FIX_AXIS_PAR_LIST", f"{ind}/end AXIS_DESCR"]
        start = 0.0
        if ax and ax.get('n') == n and ax['kind'] == 'ecm_index':
            self.stat['ecm_index'] += 1
            start = ax.get('start', 0.0)
        else:
            self.stat['index'] += 1
        cm = self.cm_for(1.0, 0.0, '-', index=True)
        return [f"{ind}/begin AXIS_DESCR FIX_AXIS NO_INPUT_QUANTITY {cm} {n} {L.num(start)} {L.num(start + n - 1)}",
                f"{ind}  FIX_AXIS_PAR_DIST {L.num(start)} 1 {n}", f"{ind}/end AXIS_DESCR"]

    def characteristic(self, o, k, short):
        r, c, w, sg = o['r'], o['c'], o['w'], o['sg']
        size = o['cells'] * w
        a = o['addr'] + k * size
        ctype = 'MAP' if (r > 1 and c > 1) else ('CURVE' if r * c > 1 else 'VALUE')
        cm = self.cm_for(o['factor'], o['offset'], o['unit'])
        self.rls.add((w, sg))
        lo, hi = L.phys_range(w, sg, o['factor'], o['offset'])
        v = L.read_vals(self.buf, a, o['cells'], w, sg) or [0]
        pmin, pmax = min(v) * o['factor'] + o['offset'], max(v) * o['factor'] + o['offset']
        nm = f"{L.ident(o['name'])}__{L.ident(short, 40)}" + (f"_c{k + 1}" if o['copies'] > 1 else '')
        copy = f" [copy {k + 1}/{o['cnt']}]" if o['cnt'] > 1 else ''
        amb = ''
        if o.get('ambiguous'):
            alts = ', '.join(f"{'s' if z['sg'] else 'u'}{w * 8} x{L.num(z['factor'])}{_off(z['offset'])}"
                             for z in o.get('alts', []))
            amb = ' AMBIGUOUS-ENCODING' + (f' (alternatives: {alts})' if alts else '')
        if o.get('label_conflict'):
            amb += ' LABEL-CONFLICT (no scaling fits the name)'
        li = (f"{o['name']} ({short}){copy} - {o.get('func', '')} | {r}x{c} {L.DT_NAME[(w, sg)]} "
              f"x{L.num(o['factor'])}{_off(o['offset'])} {o['unit'] or '-'} "
              f"| range {pmin:.2f}..{pmax:.2f} | evidence {o['grade']}{amb}").replace('"', "'")
        lines = [f"  /begin CHARACTERISTIC {nm}", f'    "{li}"',
                 f"    {ctype} 0x{a:X} RL_{L.DT_NAME[(w, sg)]} 0 {cm} {L.num(lo)} {L.num(hi)}",
                 f'    FORMAT "{L.FMT_PHYS}"']
        if ctype == 'MAP':
            lines += self.axis_descr(o, 'x', c)
            lines += self.axis_descr(o, 'y', r)
        elif ctype == 'CURVE':
            lines += self.axis_descr(o, 'y' if c == 1 else 'x', r * c)
        lines.append("  /end CHARACTERISTIC")
        return nm, a, a + size, "\n".join(lines), (pmin, pmax)


def build(objs, clo, buf, project, title, shortmap, endian='<'):
    E = Emitter(clo, buf)
    chars, spans, meta = [], [], collections.OrderedDict()
    groups = collections.OrderedDict()
    parent = {}
    for o in objs:
        if o.get('grade') == 'GAP' or not o.get('cells'):
            continue
        short = shortmap.get(o['addr']) or o.get('mapid') or 'NA'
        fam = o.get('family') or L.family(o['name'])
        par = o.get('parent') or (o.get('mapid') if o.get('idhow') == 'family' else '')
        if par and (o['cat'], fam) not in parent:
            parent[(o['cat'], fam)] = par
        for k in range(o.get('copies', 1)):
            nm, a, e, txt, prange = E.characteristic(o, k, short)
            chars.append(txt)
            spans.append((a, e, nm))
            meta[nm] = (o, k, prange)
            groups.setdefault(o['cat'], collections.OrderedDict()).setdefault(fam, []).append(nm)
    axobjs = []
    for key, (nm, ax) in E.axpts.items():
        cm = E.cm_for(ax['factor'], ax['offset'], ax['unit'])
        E.rls.add((ax['w'], ax['sg']))
        lo, hi = L.phys_range(ax['w'], ax['sg'], ax['factor'], ax['offset'])
        axobjs.append(f"  /begin AXIS_PTS {nm}\n"
                      f'    "shared axis, {ax["unit"]}, {ax["n"]} pts, located from an ECM screenshot"\n'
                      f"    0x{ax['addr']:X} NO_INPUT_QUANTITY RL_AXIS_{L.DT_NAME[(ax['w'], ax['sg'])]} 0 {cm} "
                      f"{ax['n']} {L.num(lo)} {L.num(hi)}\n"
                      f'    FORMAT "{L.FMT_PHYS}"\n'
                      f"  /end AXIS_PTS")
        spans.append((ax['addr'], ax['addr'] + ax['n'] * ax['w'], nm))
    cms = []
    for nm, (f, o, unit, index) in E.cmdef.items():
        fmt = L.FMT_INDEX if index else L.FMT_PHYS
        cms.append(f'  /begin COMPU_METHOD {nm} "raw*{L.num(f)}{_off(o)}" LINEAR "{fmt}" "{unit}" '
                   f'COEFFS_LINEAR {L.num(f)} {L.num(o)} /end COMPU_METHOD')
    rls = []
    for (w, sg) in sorted(E.rls):
        dt = L.DT_NAME[(w, sg)]
        rls.append(f"  /begin RECORD_LAYOUT RL_{dt} FNC_VALUES 1 {dt} ROW_DIR DIRECT /end RECORD_LAYOUT")
        rls.append(f"  /begin RECORD_LAYOUT RL_AXIS_{dt} AXIS_PTS_X 1 {dt} INDEX_INCR DIRECT /end RECORD_LAYOUT")
    grp = []
    for cat, fams in groups.items():
        cid = L.ident(cat)
        subs = [f"{cid}__{L.ident(fam, 60)}" for fam in fams]
        grp.append(f'  /begin GROUP {cid} "ECM category: {cat}" ROOT\n'
                   f'    /begin SUB_GROUP\n      ' + "\n      ".join(subs) + '\n    /end SUB_GROUP\n  /end GROUP')
        for fam, members in fams.items():
            par = parent.get((cat, fam), '')
            grp.append(f'  /begin GROUP {cid}__{L.ident(fam, 60)} "{fam}' + (f' ({par})' if par else '') + '"\n'
                       f'    /begin REF_CHARACTERISTIC\n      ' + "\n      ".join(members) +
                       '\n    /end REF_CHARACTERISTIC\n  /end GROUP')
    sha = hashlib.sha256(buf).hexdigest()
    bo = 'MSB_LAST' if endian == '<' else 'MSB_FIRST'
    text = f"""ASAP2_VERSION 1 61
/begin PROJECT {L.ident(project)} "{title}"
  /begin HEADER "{title}"
    VERSION "ecm-driver-to-a2l, {len(spans)} objects"
  /end HEADER
  /begin MODULE DIM
    "Addresses are RAW FILE OFFSETS into the file with SHA-256 {sha}.
     Names: <ECM display name>__<short>; every LONG_IDENTIFIER ends with its evidence grade
     (A closed on its own ECM screenshot, B closed family factor, C unit band only).
     Display: physical values FORMAT %10.2, index axes %6.0; COEFFS keep full precision.
     Limits: the data type's representable physical range."
    /begin MOD_COMMON ""
      DEPOSIT ABSOLUTE
      BYTE_ORDER {bo}
      ALIGNMENT_BYTE 1
      ALIGNMENT_WORD 2
      ALIGNMENT_LONG 4
    /end MOD_COMMON
{chr(10).join(cms)}
{chr(10).join(rls)}
{chr(10).join(axobjs)}
{chr(10).join(chars)}
{chr(10).join(grp)}
  /end MODULE
/end PROJECT
"""
    return text, spans, E, meta


def selfcheck(text, spans, clo, rng, E, meta, buf):
    fails, out = 0, []
    names = [n for _, _, n in spans]
    dup = [k for k, v in collections.Counter(names).items() if v > 1]
    bad = [n for n in names if not IDENT_RE.match(n)]
    out.append(f"1 identifiers         : {len(names)} objects, duplicates {len(dup)}, malformed {len(bad)}")
    fails += len(dup) + len(bad)
    oob = [(a, n) for a, e, n in spans if rng and not (rng[0] <= a and e <= rng[1] + 1)]
    out.append(f"2 address range       : " + (f"{rng[0]:#x}-{rng[1]:#x}, outside {len(oob)}" if rng else "not given"))
    for a, n in oob[:5]:
        out.append(f"     0x{a:06X} {n}")
    fails += len(oob)
    sp = sorted(spans)
    ov = [(p, q) for p, q in zip(sp, sp[1:]) if p[1] > q[0]]
    out.append(f"3 overlapping objects : {len(ov)}")
    for p, q in ov[:8]:
        out.append(f"     0x{p[0]:06X}-0x{p[1]:06X} {p[2][:44]}  X  0x{q[0]:06X} {q[2][:44]}")
    fails += len(ov)
    rep_n = rep_ok = 0
    for nm, (o, k, _) in meta.items():
        c = clo.get(o['addr'])
        if k != 0 or not c:
            continue
        rep_n += 1
        tol = 0.5 * 10 ** (-c.get('decimals', 2)) + 1e-9
        v = L.read_vals(buf, o['addr'] + c['start_cell'] * o['w'], len(c['cells']), o['w'], o['sg'])
        got = [x * o['factor'] + o['offset'] for x in v]
        if all(abs(g - float(d)) <= tol for g, d in zip(got, c['cells'])):
            rep_ok += 1
        else:
            out.append(f"     MISMATCH 0x{o['addr']:06X} got {[round(g, 3) for g in got[:6]]} "
                       f"want {c['cells'][:6]}")
    out.append(f"4 screenshot replay   : {rep_ok}/{rep_n} maps reproduce ECM's displayed cells exactly")
    fails += rep_n - rep_ok
    s = E.stat
    out.append(f"5 axes                : stored {s['stored']}  fix_dist {s['fix_dist']}  fix_list {s['fix_list']}"
               f"  ecm_index {s['ecm_index']}  UNRESOLVED {s['index']}")
    cmr = re.findall(r'/begin COMPU_METHOD (\S+) "[^"]*" LINEAR "([^"]+)" "[^"]*" COEFFS_LINEAR (\S+) (\S+)', text)
    badfmt = [n for n, f, _, _ in cmr if f != (L.FMT_INDEX if E.cmdef[n][3] else L.FMT_PHYS)]
    inexact = [n for n, _, a_, b_ in cmr if float(a_) != E.cmdef[n][0] or float(b_) != E.cmdef[n][1]]
    nfmt = text.count(f'FORMAT "{L.FMT_PHYS}"')
    nobj = text.count('/begin CHARACTERISTIC') + text.count('/begin AXIS_PTS')
    out.append(f"6 display / precision : COMPU_METHOD off-rule {len(badfmt)}, COEFFS not round-trip {len(inexact)},"
               f" FORMAT lines {nfmt}/{nobj}")
    fails += len(badfmt) + len(inexact) + (nobj - nfmt)
    viol = 0
    for nm, (o, k, (pmin, pmax)) in meta.items():
        lo, hi = L.phys_range(o['w'], o['sg'], o['factor'], o['offset'])
        if pmin < lo - 1e-9 or pmax > hi + 1e-9:
            viol += 1
    out.append(f"7 limits enclose data : violations {viol}")
    fails += viol
    return fails, out


def write_html(path, objs, clo, title, a2lname):
    def axdesc(o):
        c = clo.get(o['addr'])
        if o['cells'] == 1:
            return '—'
        if not c:
            return '<span class="un">索引轴（未解析）</span>'
        parts = []
        for k in ('x', 'y'):
            ax = (c.get('axes') or {}).get(k)
            if not ax:
                continue
            u = html.escape(c.get('xunit' if k == 'x' else 'yunit') or '')
            kind = {'stored': f"0x{ax.get('addr', 0):06X}", 'fix_dist': '等距', 'ecm_index': '索引',
                    'fix_list': '显示列表'}.get(ax['kind'], ax['kind'])
            parts.append(f"{k.upper()} {u} {kind}")
        return ' / '.join(parts) or '—'

    cats = collections.OrderedDict()
    for o in objs:
        cats.setdefault(o['cat'], []).append(o)
    g = collections.Counter(o['grade'] for o in objs)
    body = []
    for cat, lst in cats.items():
        body.append(f'<tr class="cat"><td colspan="12">{html.escape(cat)} <span>{len(lst)}</span></td></tr>')
        for o in lst:
            if o.get('grade') == 'GAP':
                body.append(f'<tr class="gap"><td>{html.escape(o["name"])}</td><td colspan="11">'
                            f'驱动树里这一行看不清（份数 / 维度缺失）→ 未输出，见补拍清单</td></tr>')
                continue
            dims = f"{o['r']}×{o['c']}" + (f" ×{o['cnt']}" if o['cnt'] > 1 else '')
            if o['cnt'] > 1 and o['copies'] < o['cnt']:
                dims += f"（定位 {o['copies']}）"
            fac = L.num(o['factor']) + (f" {_off(o['offset'])}" if o['offset'] else '')
            cells = [html.escape(o['name']), html.escape(o.get('family', '')),
                     html.escape(o.get('mapid') or '—'), f"0x{o['addr']:06X}", dims,
                     L.DT_NAME[(o['w'], o['sg'])], fac, html.escape(o['unit'] or '—'),
                     f"{o['phy_min']:.2f} … {o['phy_max']:.2f}", axdesc(o),
                     f'<b class="g{o["grade"]}">{o["grade"]}</b>' + (' ⚠' if o.get('ambiguous') else ''),
                     html.escape(o.get('func', ''))]
            body.append('<tr>' + ''.join(f'<td>{x}</td>' for x in cells) + '</tr>')
    doc = f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{font:13px/1.45 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;color:#1d2330;background:#fbfbfa}}
h1{{font-size:20px;margin:0 0 4px}} p.s{{color:#5b6475;margin:0 0 16px}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:5px 8px;border-bottom:1px solid #e6e6e3;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:#f1f1ee;font-weight:600}}
tr.cat td{{background:#1d2330;color:#fff;font-weight:600;padding:8px}} tr.cat span{{opacity:.6;font-weight:400}}
tr.gap td{{color:#9a3b00;background:#fff6ee}} td:nth-child(4),td:nth-child(7),td:nth-child(9){{font-family:Consolas,monospace}}
b.gA{{color:#1a7f37}} b.gB{{color:#9a6700}} b.gC{{color:#b42318}} .un{{color:#8a8f98}}
</style>
<h1>{html.escape(title)}</h1>
<p class="s">A2L：{html.escape(a2lname)} · A {g.get('A', 0)} / B {g.get('B', 0)} / C {g.get('C', 0)}
 / 缺口 {g.get('GAP', 0)} · 地址 = 裸文件偏移 · 数值范围 2 位小数，factor 全精度</p>
<table><tr><th>ECM 名称</th><th>小类（族）</th><th>ECM MAP_ID</th><th>地址</th><th>维度×份数</th><th>类型</th>
<th>factor</th><th>单位</th><th>数值范围</th><th>轴</th><th>证据</th><th>功能</th></tr>
{chr(10).join(body)}
</table>"""
    open(path, 'w', encoding='utf-8').write(doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--objects')
    ap.add_argument('--closure')
    ap.add_argument('--bin')
    ap.add_argument('--out', default='out.a2l')
    ap.add_argument('--html')
    ap.add_argument('--project', default='ECM_DRIVER')
    ap.add_argument('--title', default='')
    ap.add_argument('--range', help='e.g. 0x700100-0x7FF7DF (calibration area, file offsets)')
    ap.add_argument('--short', help='names.json {"0xADDR": "BoschName"} overriding the MAP_ID short name')
    ap.add_argument('--endian', choices=('le', 'be'), default='le')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    L.ENDIAN = '<' if a.endian == 'le' else '>'
    objs = L.jload(a.objects)
    clo = {c['addr']: c for c in (L.jload(a.closure) if a.closure else []) if 'error' not in c}
    buf = open(a.bin, 'rb').read()
    shortmap = {L.as_int(k): v for k, v in (L.jload(a.short).items() if a.short else [])}
    rng = tuple(int(x, 16) for x in a.range.split('-')) if a.range else None
    text, spans, E, meta = build(objs, clo, buf, a.project, a.title or a.project, shortmap, L.ENDIAN)
    open(a.out, 'w', encoding='utf-8').write(text)
    fails, lines = selfcheck(text, spans, clo, rng, E, meta, buf)
    print("=" * 76 + "\nDELIVERY SELF-CHECK\n" + "=" * 76)
    print("\n".join(lines))
    print("=" * 76)
    print(f"RESULT: {'PASS' if fails == 0 else f'FAIL ({fails} problems)'}   -> {a.out}")
    if a.html:
        write_html(a.html, objs, clo, a.title or a.project, os.path.basename(a.out))
        print(f"HTML  -> {a.html}")
    return 0 if fails == 0 else 2


def selftest():
    buf = bytearray(0x400)
    struct.pack_into('<6H', buf, 0x100, 3500, 3500, 3400, 3300, 3200, 3100)
    struct.pack_into('<3H', buf, 0x116, 800, 2000, 4000)
    o = dict(cat='Engine Torque', name='Lim #1', unit='Nm', addr=0x100, cnt=1, r=2, c=3, cells=6, w=2, sg=False,
             factor=0.1, offset=0.0, grade='A', ambiguous=False, copies=1, stride_ok=True, family='Lim',
             mapid='EngPrt_trqLim', parent='', idhow='family', func='test', phy_min=310.0, phy_max=350.0)
    clo = {0x100: dict(addr=0x100, rows=2, cols=3, start_cell=0, cells=[350, 350, 340], decimals=2, w=2,
                       sg=False, factor=0.1, offset=0.0, xunit='RPM', yunit='hPa',
                       axes={'x': dict(kind='stored', n=3, addr=0x116, w=2, sg=False, factor=1.0, offset=0.0,
                                       values=[800, 2000, 4000], dist=10),
                             'y': dict(kind='fix_dist', n=2, start=0.0, step=500.0, values=[0, 500])})}
    text, spans, E, meta = build([o], clo, bytes(buf), 'T', 'T', {})
    fails, _ = selfcheck(text, spans, clo, None, E, meta, bytes(buf))
    ok = (fails == 0 and 'FORMAT "%10.2"' in text and 'AXIS_PTS_REF AX_RPM_3_000116' in text
          and 'FIX_AXIS_PAR_DIST 0 500 2' in text and 'UWORD 0 CM_Nm_0p1_0 0 6553.5' in text)
    text2, spans2, E2, meta2 = build([o, dict(o)], clo, bytes(buf), 'T', 'T', {})
    f2, _ = selfcheck(text2, spans2, clo, None, E2, meta2, bytes(buf))
    print(f"  emit + 7-point self-check clean: {ok}   negative control (duplicate object caught): {f2 > 0}")
    print("selftest:", "PASS" if ok and f2 > 0 else "FAIL")
    return 0 if ok and f2 > 0 else 1


if __name__ == '__main__':
    sys.exit(main())

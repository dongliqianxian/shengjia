#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 1 - validate a transcribed ECM Titanium driver tree against the binary.

Input   tree.tsv : category <TAB> name <TAB> unit <TAB> 0xADDR <TAB> N <TAB> R <TAB> C [<TAB> note]
                   '?' marks a field that could not be read off the screenshot.
                   optional lines  '#CAT<TAB>Engine Torque<TAB>49'  = the count ECM shows per category
Output  tree.json: every row plus structural hints for resolve.py

What this step proves (nothing is guessed):
  * transcription completeness per category - the [49] in the tree is a free checksum
  * odd start address                      -> the object is byte wide
  * exact geometry of the WHOLE group      -> addr + N*R*C*w == next ECM address for one w only
    (k < N matches are recorded but are not proofs: u8 x2 copies == u16 x1 copy in bytes)
  * duplicates, addresses outside the file, unreadable rows (tree gaps)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ecmlib as L  # noqa: E402


def analyse(rows, blen):
    addrs = sorted(set(x['addr'] for x in rows))
    out, problems, seen = [], [], {}
    for x in rows:
        if x['addr'] in seen:
            problems.append(f"duplicate address 0x{x['addr']:06X}: '{seen[x['addr']]}' / '{x['name']}'")
        seen[x['addr']] = x['name']
        if x['addr'] >= blen:
            problems.append(f"0x{x['addr']:06X} '{x['name']}' lies outside the {blen:#x}-byte file")
    for x in rows:
        h = dict(x)
        nxt = next((a for a in addrs if a > x['addr']), None)
        h['next'] = nxt
        h['room'] = (nxt if nxt else blen) - x['addr']
        h['odd'] = bool(x['addr'] & 1)
        h['exact_fit'], h['geom_width'], h['geom_hint'], h['cells'] = [], None, None, None
        if x['cnt'] and x['r'] and x['c']:
            cells = x['r'] * x['c']
            h['cells'] = cells
            if nxt:
                for w in (1, 2):
                    for k in range(1, x['cnt'] + 1):
                        if x['addr'] + k * cells * w == nxt:
                            h['exact_fit'].append((w, k))
                # widths 1/2 only: a u16 scalar followed by a 2-byte gap fakes u32 (measured on
                # MG1CS201). Groups under 4 cells only give a hint - one padding byte fakes the next width.
                full = [w for w in (1, 2) if x['addr'] + x['cnt'] * cells * w == nxt]
                if len(full) == 1:
                    # a proof only for real tables: scalar groups ([6] x 1x1) are exactly where ECM's
                    # [N] stops meaning 'contiguous copies' (measured: LimTq #18/#19 on MG1CS201)
                    if cells >= 4:
                        h['geom_width'] = full[0]
                    else:
                        h['geom_hint'] = full[0]
        else:
            problems.append(f"line {x['line']}: '{x['name']}' @0x{x['addr']:06X} has unreadable N/R/C (tree gap)")
        out.append(h)
    return out, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tree')
    ap.add_argument('--bin')
    ap.add_argument('--out', default='tree.json')
    ap.add_argument('--expect', help='"Engine Torque=49,Rail=1" - counts shown in the ECM tree')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    rows, expect = L.load_tree(a.tree)
    if a.expect:
        for kv in a.expect.split(','):
            k, v = kv.rsplit('=', 1)
            expect[k.strip()] = int(v)
    blen = os.path.getsize(a.bin)
    out, problems = analyse(rows, blen)
    L.jsave({'rows': out, 'expect': expect, 'bin_size': blen, 'problems': problems}, a.out)
    by = {}
    for x in rows:
        by[x['cat']] = by.get(x['cat'], 0) + 1
    print(f"rows {len(rows)}   categories {len(by)}   file {blen:#x} bytes")
    print("category completeness (transcribed / shown in the tree):")
    missing = 0
    for cat in sorted(set(by) | set(expect)):
        got, exp = by.get(cat, 0), expect.get(cat)
        if exp is None:
            flag = ''
        elif got == exp:
            flag = '  OK'
        elif got < exp:
            flag = f'  MISSING {exp - got}'
            missing += exp - got
        else:
            flag = f'  EXTRA {got - exp}'
        print(f"  {cat:32s} {got:4d}" + (f" / {exp}" if exp is not None else '') + flag)
    print(f"width proofs: odd address {sum(1 for x in out if x['odd'])}, "
          f"exact whole-group geometry {sum(1 for x in out if x['geom_width'])}")
    print(f"problems: {len(problems)}   rows missing vs tree counts: {missing}")
    for p in problems:
        print("  - " + p)
    print(f"-> {a.out}")
    return 0


def selftest():
    rows = [dict(line=1, cat='A', name='m1', unit='Nm', addr=0x100, cnt=2, r=2, c=3, note=''),
            dict(line=2, cat='A', name='m2', unit='Nm', addr=0x118, cnt=1, r=1, c=1, note=''),
            dict(line=3, cat='A', name='m3', unit='', addr=0x11B, cnt=1, r=1, c=1, note=''),
            dict(line=4, cat='B', name='gap', unit='', addr=0x200, cnt=None, r=None, c=None, note='')]
    out, prob = analyse(rows, 0x1000)
    ok = (out[0]['geom_width'] == 2 and out[2]['odd'] and any('tree gap' in p for p in prob))
    rows[1]['addr'] = 0x119            # negative control: one byte off -> no proof
    out2, _ = analyse(rows, 0x1000)
    ok2 = out2[0]['geom_width'] is None
    print(f"  whole-group geometry proof found: {ok}   negative control (off by one): {ok2}")
    print("selftest:", "PASS" if ok and ok2 else "FAIL")
    return 0 if ok and ok2 else 1


if __name__ == '__main__':
    sys.exit(main())

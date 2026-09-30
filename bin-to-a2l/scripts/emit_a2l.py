#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 4 of bin-to-a2l: emit a valid, grouped A2L and verify it round-trips.

Input is a SPEC (json) that a human has curated after function judgement:

{
  "project": "ISUZU_RZ4E_MD1CS089",
  "module":  "MD1CS089",
  "base":    "0x80000000",          // added to file offsets; use 0 for file-offset A2L
  "groups": [
    {"name":"Torque","desc":"Torque structure","maps":[
       {"name":"DriverDemand_1","addr":"0x4D45A2","rows":16,"cols":16,
        "data":"NM","x":"RPM","y":"PEDAL","desc":"Driver demand torque 1"}
    ]}
  ]
}

Every descriptor is re-read from the bin and its dims asserted, so a typo in the
spec fails loudly instead of producing a plausible-looking wrong definition.

Conventions applied:
  * COMPU_METHOD LINEAR + COEFFS_LINEAR (unambiguous, unlike RAT_FUNC direction)
  * BYTE_ORDER MSB_LAST for little-endian cores
  * A2L X axis = fastest-varying index = the COLS array (ROW_DIR storage)
  * RPM / index / raw axes print as integers, everything else 2 decimals
  * GROUP / SUB_GROUP / REF_CHARACTERISTIC folder tree
"""
import argparse, json, os, re, struct, sys

# kind -> (factor, unit, display format)
KINDS = {
    "RPM":  (0.5,     "rpm",    "%8.2"),
    "PEDAL":(1/81.92, "%",      "%6.2"),
    "IQ":   (0.01,    "mg/stk", "%6.2"),
    "TEMP": (0.1,     "degC",   "%6.2"),
    "LAMB": (0.01,    "-",      "%6.2"),
    "IDX":  (1.0,     "-",      "%4.0"),
    "RAW":  (1.0,     "-",      "%8.2"),
    "PRESS":(0.1,     "bar",    "%6.2"),   # axis: rail/charge pressure breakpoints
    # Bosch EDC17 states injection quantity as VOLUME; MD1 states it as MASS.
    "MM3":  (0.01,    "mm3/stk", "%8.2"),
    # duty/ratio axes and values using the 8192 = 100% fixed-point convention
    "PCT8192": (1/81.92, "%",   "%8.2"),
    "NM":   (0.1,     "Nm",     "%8.2"),
    "MG":   (0.01,    "mg/stk", "%8.2"),
    "BAR":  (0.1,     "bar",    "%8.2"),
    "HPA":  (1.0,     "hPa",    "%8.2"),
    "DEGC": (0.1,     "degC",   "%8.2"),
    "DEG":  (0.1,     "deg",    "%8.2"),
    "US":   (0.1,     "us",     "%8.2"),
    "LAM":  (0.001,   "-",      "%8.2"),
    "PCT":  (0.1,     "%",      "%8.2"),
}
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*$')


def build(data, spec, base):
    u = lambda o: struct.unpack_from('<H', data, o)[0]
    L = []
    A = L.append
    used = {}
    A('ASAP2_VERSION 1 61')
    A('')
    A('/begin PROJECT %s "%s"' % (spec.get('project', 'ECU'), spec.get('title', '')))
    A('')
    A('  /begin HEADER "%s"' % spec.get('header', 'Calibration definition'))
    A('    VERSION "%s"' % spec.get('version', '1.0'))
    A('  /end HEADER')
    A('')
    A('  /begin MODULE %s "%s"' % (spec.get('module', 'ECU'), spec.get('module_desc', '')))
    A('')
    A('    /begin MOD_COMMON "%s"' % spec.get('mod_common', 'little endian'))
    A('      BYTE_ORDER MSB_LAST')
    A('      ALIGNMENT_BYTE 1')
    A('      ALIGNMENT_WORD 2')
    A('      ALIGNMENT_LONG 4')
    A('    /end MOD_COMMON')
    A('')
    for n, dt in (("RL_VALUE_U", "UWORD"), ("RL_VALUE_S", "SWORD")):
        A('    /begin RECORD_LAYOUT %s' % n)
        A('      FNC_VALUES 1 %s ROW_DIR DIRECT' % dt)
        A('    /end RECORD_LAYOUT')
    for n, dt in (("RL_AXIS_U", "UWORD"), ("RL_AXIS_S", "SWORD")):
        A('    /begin RECORD_LAYOUT %s' % n)
        A('      AXIS_PTS_X 1 %s INDEX_INCR DIRECT' % dt)
        A('    /end RECORD_LAYOUT')
    A('')
    emitted_cm = set()

    def cm(kind):
        f, un, fmt = KINDS[kind]
        nm = "CM_" + kind
        if nm not in emitted_cm:
            emitted_cm.add(nm)
            A('    /begin COMPU_METHOD %s "" LINEAR "%s" "%s"' % (nm, fmt, un))
            A('      COEFFS_LINEAR %s 0' % f)
            A('    /end COMPU_METHOD')
        return nm
    for k in KINDS:
        pass  # emitted lazily below so the file only carries what it uses
    body = []
    axes = {}

    def axis(addr, n, kind, signed):
        key = "AX_%06X" % addr
        if key not in axes:
            axes[key] = True
            f, un, fmt = KINDS[kind]
            rd = '<h' if signed else '<H'
            v = [struct.unpack_from(rd, data, addr + i * 2)[0] for i in range(n)]
            body.append('    /begin AXIS_PTS %s "" 0x%08X NO_INPUT_QUANTITY %s 0 %s %d %.2f %.2f'
                        % (key, addr + base, 'RL_AXIS_S' if signed else 'RL_AXIS_U',
                           cm(kind), n, min(v) * f, max(v) * f))
            body.append('    /end AXIS_PTS')
        return key

    problems = []
    for g in spec['groups']:
        for m in g['maps']:
            nm = m['name']
            if not IDENT.match(nm):
                problems.append("illegal A2L identifier: %s" % nm)
            if len(nm) > 32:
                # ISO C90 identifier limit; INCA and other tools reject longer names.
                problems.append("identifier too long (%d>32 chars): %s" % (len(nm), nm))
            if nm in used:
                problems.append("duplicate name %s (A2L names must be globally unique)" % nm)
            used[nm] = g['name']
            ad = int(str(m['addr']), 0)
            r, c = int(m['rows']), int(m['cols'])
            dk = m['data']
            sgn = bool(m.get('signed', False))
            f, un, fmt = KINDS[dk]
            rdfmt = '<h' if sgn else '<H'
            vals = [struct.unpack_from(rdfmt, data, ad + i * 2)[0] for i in range(r * c)]
            lo, hi = min(vals) * f, max(vals) * f
            rl = 'RL_VALUE_S' if sgn else 'RL_VALUE_U'
            desc = m.get('desc', '').replace('"', "'")
            if r == 1 and c == 1:
                body.append('    /begin CHARACTERISTIC %s "%s" VALUE 0x%08X %s 0 %s %.2f %.2f'
                            % (nm, desc, ad + base, rl, cm(dk), lo, hi))
                body.append('    /end CHARACTERISTIC')
                continue
            if c == 1:
                hdr = ad - (2 + r * 2)
                if u(hdr) != r:
                    problems.append("%s: curve header at 0x%06X says %d, spec says %d"
                                    % (nm, hdr, u(hdr), r))
                ax = axis(hdr + 2, r, m['x'], m.get('x_signed', False))
                body.append('    /begin CHARACTERISTIC %s "%s" CURVE 0x%08X %s 0 %s %.2f %.2f'
                            % (nm, desc, ad + base, rl, cm(dk), lo, hi))
                body.append('      /begin AXIS_DESCR COM_AXIS NO_INPUT_QUANTITY %s %d 0 65535'
                            % (cm(m['x']), r))
                body.append('        AXIS_PTS_REF %s' % ax)
                body.append('      /end AXIS_DESCR')
                body.append('    /end CHARACTERISTIC')
                continue
            hdr = ad - (4 + r * 2 + c * 2)
            if u(hdr) != r or u(hdr + 2) != c:
                problems.append("%s: descriptor at 0x%06X says %dx%d, spec says %dx%d"
                                % (nm, hdr, u(hdr), u(hdr + 2), r, c))
            axX = axis(hdr + 4 + r * 2, c, m['y'], m.get('y_signed', False))
            axY = axis(hdr + 4, r, m['x'], m.get('x_signed', False))
            body.append('    /begin CHARACTERISTIC %s "%s" MAP 0x%08X %s 0 %s %.2f %.2f'
                        % (nm, desc, ad + base, rl, cm(dk), lo, hi))
            for a_, k_, n_ in ((axX, m['y'], c), (axY, m['x'], r)):
                body.append('      /begin AXIS_DESCR COM_AXIS NO_INPUT_QUANTITY %s %d 0 65535'
                            % (cm(k_), n_))
                body.append('        AXIS_PTS_REF %s' % a_)
                body.append('      /end AXIS_DESCR')
            body.append('    /end CHARACTERISTIC')

    L.extend(body)
    A('')
    A('    /begin GROUP %s "%s" ROOT' % (spec.get('root', 'Calibration'),
                                         spec.get('root_desc', '')))
    A('      /begin SUB_GROUP')
    for g in spec['groups']:
        A('        %s' % g['name'])
    A('      /end SUB_GROUP')
    A('    /end GROUP')
    A('')
    for g in spec['groups']:
        A('    /begin GROUP %s "%s"' % (g['name'], g.get('desc', '')))
        A('      /begin REF_CHARACTERISTIC')
        for m in g['maps']:
            A('        %s' % m['name'])
        A('      /end REF_CHARACTERISTIC')
        A('    /end GROUP')
        A('')
    A('  /end MODULE')
    A('/end PROJECT')
    return "\n".join(L), problems, len(used), len(axes)


def roundtrip(text, data, base):
    """re-parse what we just wrote and re-verify every address against the bin"""
    bad = []
    for mm in re.finditer(
            r'/begin CHARACTERISTIC\s+(\S+)\s+"[^"]*"\s+(MAP|CURVE|VALUE)\s+0x([0-9A-Fa-f]+)', text):
        nm, typ, ad = mm.group(1), mm.group(2), int(mm.group(3), 16) - base
        if not (0 <= ad < len(data)):
            bad.append("%s: address 0x%X outside image" % (nm, ad))
    n = len(re.findall(r'/begin CHARACTERISTIC', text))
    if n != len(re.findall(r'/end CHARACTERISTIC', text)):
        bad.append("unbalanced CHARACTERISTIC blocks")
    for tag in ("PROJECT", "MODULE", "GROUP", "AXIS_PTS", "COMPU_METHOD", "RECORD_LAYOUT"):
        if len(re.findall(r'/begin %s\b' % tag, text)) != len(re.findall(r'/end %s\b' % tag, text)):
            bad.append("unbalanced %s blocks" % tag)
    return n, bad


def main():
    ap = argparse.ArgumentParser(description="Emit a grouped, validated A2L")
    ap.add_argument("--bin", dest="binpath")
    ap.add_argument("--spec")
    ap.add_argument("--out")
    ap.add_argument("--base", default=None,
                    help="override spec base; e.g. 0x80000000 or 0")
    ap.add_argument("--also-file-offset", action="store_true",
                    help="additionally emit a base=0 variant next to --out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.binpath and a.spec and a.out):
        ap.error("--bin --spec --out required")

    data = open(a.binpath, 'rb').read()
    spec = json.load(open(a.spec))
    base = int(str(a.base if a.base is not None else spec.get('base', 0)), 0)

    text, problems, nmaps, naxes = build(data, spec, base)
    n, bad = roundtrip(text, data, base)
    print("characteristics: %d   axes: %d" % (nmaps, naxes))
    if problems:
        print("\nSPEC PROBLEMS (fix these - the A2L is NOT trustworthy):")
        for p in problems:
            print("  ! " + p)
    if bad:
        print("\nSTRUCTURE PROBLEMS:")
        for b in bad:
            print("  ! " + b)
    if problems or bad:
        return 2
    open(a.out, 'w', encoding='utf-8').write(text)
    print("wrote %s (%d bytes)  base=0x%X" % (a.out, os.path.getsize(a.out), base))
    if a.also_file_offset:
        t2, p2, _, _ = build(data, spec, 0)
        alt = a.out.replace('.a2l', '_fileoffset.a2l')
        open(alt, 'w', encoding='utf-8').write(t2)
        print("wrote %s (base=0)" % alt)
    print("round-trip: OK (%d characteristics, all blocks balanced, "
          "all addresses inside image)" % n)
    return 0


def selftest():
    buf = bytearray(b'\x00' * 40000)
    off, r, c = 100, 8, 8
    struct.pack_into('<H', buf, off, r); struct.pack_into('<H', buf, off + 2, c)
    for i in range(r):
        struct.pack_into('<H', buf, off + 4 + i * 2, 1600 + i * 400)
    for j in range(c):
        struct.pack_into('<H', buf, off + 4 + r * 2 + j * 2, j * 800)
    dat = off + 4 + r * 2 + c * 2
    for i in range(r * c):
        struct.pack_into('<H', buf, dat + i * 2, 1000 + i * 10)
    spec = dict(project="T", module="M", base=0, groups=[
        dict(name="G1", desc="", maps=[
            dict(name="Map_1", addr=hex(dat), rows=r, cols=c,
                 data="NM", x="RPM", y="IQ", desc="test")])])
    text, problems, nm, na = build(bytes(buf), spec, 0)
    n, bad = roundtrip(text, bytes(buf), 0)
    # negative control: wrong dims must be caught
    spec2 = json.loads(json.dumps(spec)); spec2['groups'][0]['maps'][0]['rows'] = 9
    _, prob2, _, _ = build(bytes(buf), spec2, 0)
    ok = (not problems and not bad and n == 1 and nm == 1 and na == 2 and prob2)
    print("  emit ok=%s problems=%d structure=%d chars=%d axes=%d"
          % (not problems and not bad, len(problems), len(bad), nm, na))
    print("  negative control (wrong dims caught): %s" % bool(prob2))
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

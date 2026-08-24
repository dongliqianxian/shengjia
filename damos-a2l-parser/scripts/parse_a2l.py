#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_a2l.py — 从 A2L (ASAM MCD-2MC / ASAP2) 文本描述文件中提取标定 MAP 定义。

damos-a2l-parser 技能的执行脚本。输出与 winols-ols-parser/scripts/parse_ols.py 同构
（name / address / size_bytes / raw_min / raw_max + A2L 特有的 factor/offset/unit/dims），
下游 ecu-map-analysis / stage1-calibration-matrix / bin 引擎对二者一视同仁。

用法:
    python parse_a2l.py FILE.a2l                      # 摘要 + MAP 清单
    python parse_a2l.py FILE.a2l --filter torque      # 名称/描述子串过滤(不区分大小写)
    python parse_a2l.py FILE.a2l --map KFZW           # 单张 MAP 详情(轴/换算/布局)
    python parse_a2l.py FILE.a2l --json maps.json     # 输出 JSON
    python parse_a2l.py FILE.a2l --bin ORI.bin --base 0x800000   # 交叉自检 raw 值域
    python parse_a2l.py --selftest                    # 内置样例自测

注意（doctrine 输出纪律）:
  * A2L 的 ECU 地址通常不等于 bin 文件偏移——须减去内存段基址。--bin 时不给 --base
    则脚本用 MEMORY_SEGMENT 推荐候选基址，但不擅自认定。
  * RAT_FUNC 只有在纯线性时才折算成 factor/offset（WinOLS 约定 phys=raw*factor+offset）；
    TAB_*/FORM 等非线性一律标 non-linear，不硬编一个错误系数。
  * 本脚本只产定义元数据。字节读写交 bin 引擎，换算复核交 winols-scaling-converter，
    写盘后 checksum 交 checksum-crc-engine。
"""
import sys, re, json, struct, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")  # 避免 Windows 控制台中文乱码
except Exception:
    pass

# ASAP2 数据类型 -> (struct 字符, 字节宽)
DATATYPES = {
    "UBYTE": ("B", 1), "SBYTE": ("b", 1), "UWORD": ("H", 2), "SWORD": ("h", 2),
    "ULONG": ("I", 4), "SLONG": ("i", 4), "A_UINT64": ("Q", 8), "A_INT64": ("q", 8),
    "FLOAT16_IEEE": ("e", 2), "FLOAT32_IEEE": ("f", 4), "FLOAT64_IEEE": ("d", 8),
}

CHAR_TYPES = {"VALUE", "CURVE", "MAP", "CUBOID", "CUBE_4", "CUBE_5", "VAL_BLK", "ASCII"}

_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')


def _read_text(path):
    data = open(path, "rb").read()
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def tokenize(text):
    """去 /* */ 与 // 注释后按空白切词，保留带引号字符串为单 token。"""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return _TOKEN_RE.findall(text)


def _unq(tok):
    if tok and tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
        return tok[1:-1]
    return tok


def _num(tok):
    try:
        return int(tok, 0)
    except (ValueError, TypeError):
        try:
            return float(tok)
        except (ValueError, TypeError):
            return None


class Blk:
    """一个 /begin X ... /end X 块：kind、自身 token（不含子块）、子块列表。"""
    __slots__ = ("kind", "toks", "subs")

    def __init__(self, kind):
        self.kind = kind
        self.toks = []
        self.subs = []

    def sub(self, kind):
        return [b for b in self.subs if b.kind == kind]

    def all_sub(self, kind, acc=None):
        acc = acc if acc is not None else []
        for b in self.subs:
            if b.kind == kind:
                acc.append(b)
            b.all_sub(kind, acc)
        return acc


def parse_blocks(tokens):
    """递归下降建块树。IF_DATA（厂商私有段）整块跳过不进树。"""
    root = Blk("__ROOT__")
    stack = [root]
    i, n = 0, len(tokens)
    skip_depth = 0  # >0 表示在 IF_DATA 内
    while i < n:
        t = tokens[i]
        if t == "/begin" and i + 1 < n:
            kind = tokens[i + 1]
            if skip_depth or kind == "IF_DATA":
                skip_depth += 1
            else:
                b = Blk(kind)
                stack[-1].subs.append(b)
                stack.append(b)
            i += 2
            continue
        if t == "/end" and i + 1 < n:
            if skip_depth:
                skip_depth -= 1
            elif len(stack) > 1:
                stack.pop()
            i += 2
            continue
        if not skip_depth:
            stack[-1].toks.append(t)
        i += 1
    return root


def _kw_after(toks, kw, count=1):
    """块 token 里关键字 kw 之后的 count 个 token。"""
    for i, t in enumerate(toks):
        if t == kw and i + count < len(toks) + 1:
            return toks[i + 1:i + 1 + count]
    return None


def parse_compu_methods(module):
    """COMPU_METHOD -> {name: {unit, kind, factor, offset, note}}
    折算成 WinOLS 约定 phys = raw*factor + offset；非线性不折算。"""
    out = {}
    for b in module.sub("COMPU_METHOD"):
        t = b.toks
        if len(t) < 3:
            continue
        name, ctype = t[0], (t[2] if len(t) > 2 else "")
        unit = _unq(t[4]) if len(t) > 4 else ""
        info = {"unit": unit, "kind": ctype, "factor": None, "offset": None, "note": ""}
        if ctype == "IDENTICAL":
            info["factor"], info["offset"] = 1.0, 0.0
        elif ctype == "LINEAR":
            co = _kw_after(t, "COEFFS_LINEAR", 2)
            if co:
                a, c = _num(co[0]), _num(co[1])   # phys = a*raw + b
                info["factor"], info["offset"] = a, c
        elif ctype == "RAT_FUNC":
            co = _kw_after(t, "COEFFS", 6)
            if co:
                a, bb, c, d, e, f = (_num(x) for x in co)
                # ASAP2: raw = (a*phys^2 + b*phys + c) / (d*phys^2 + e*phys + f)
                if a == 0 and d == 0 and e == 0 and bb not in (0, None) and f is not None:
                    info["factor"] = f / bb          # phys = raw*(f/b) - c/b
                    info["offset"] = -c / bb
                else:
                    info["note"] = "RAT_FUNC 非线性(有理式)，需人工确认"
        else:
            info["note"] = "%s 非线性换算(查表/公式)，需人工确认" % ctype
        out[name] = info
    return out


def parse_record_layouts(module):
    """RECORD_LAYOUT -> {name: {datatype, width, fmt, index_mode}} 只取 FNC_VALUES。"""
    out = {}
    for b in module.sub("RECORD_LAYOUT"):
        t = b.toks
        if not t:
            continue
        name = t[0]
        fv = _kw_after(t, "FNC_VALUES", 3)
        if fv:
            dt = fv[1]
            fmt, width = DATATYPES.get(dt, (None, None))
            out[name] = {"datatype": dt, "width": width, "fmt": fmt,
                         "index_mode": fv[2] if len(fv) > 2 else None}
        else:
            out[name] = {"datatype": None, "width": None, "fmt": None, "index_mode": None}
    return out


def parse_axis_pts(module):
    """AXIS_PTS(共享轴 COM_AXIS 的数据体) -> {name: {address, max_points, conversion}}"""
    out = {}
    for b in module.sub("AXIS_PTS"):
        t = b.toks
        if len(t) < 7:
            continue
        out[t[0]] = {"address": _num(t[2]), "record_layout": t[4],
                     "conversion": t[6] if len(t) > 6 else None,
                     "max_points": _num(t[7]) if len(t) > 7 else None}
    return out


def parse_groups(module):
    """FUNCTION 的 DEF_CHARACTERISTIC + GROUP 的 REF_CHARACTERISTIC -> {char: group}"""
    owner = {}
    for b in module.sub("FUNCTION") + module.sub("GROUP"):
        gname = b.toks[0] if b.toks else "?"
        for db in b.all_sub("DEF_CHARACTERISTIC") + b.all_sub("REF_CHARACTERISTIC"):
            for c in db.toks:
                owner.setdefault(c, gname)
    return owner


def parse_mem_segments(module):
    segs = []
    for mp in module.sub("MOD_PAR"):
        for ms in mp.sub("MEMORY_SEGMENT"):
            t = ms.toks
            # MEMORY_SEGMENT name "" prgtype memtype attr address size ...
            nums = [_num(x) for x in t if _num(x) is not None and isinstance(_num(x), int)]
            addr = size = None
            for i, x in enumerate(t):
                v = _num(x)
                if isinstance(v, int) and v > 0xFF and addr is None:
                    addr = v
                    nxt = _num(t[i + 1]) if i + 1 < len(t) else None
                    size = nxt if isinstance(nxt, int) else None
                    break
            segs.append({"name": t[0] if t else "?", "address": addr, "size": size})
    return segs


def _axis_from_descr(b, axis_pts):
    t = b.toks
    ax = {"attr": t[0] if t else None,
          "input": t[1] if len(t) > 1 else None,
          "conversion": t[2] if len(t) > 2 else None,
          "max_points": _num(t[3]) if len(t) > 3 else None,
          "low": _num(t[4]) if len(t) > 4 else None,
          "high": _num(t[5]) if len(t) > 5 else None,
          "axis_pts_ref": None, "address": None}
    ref = _kw_after(t, "AXIS_PTS_REF", 1)
    if ref:
        ax["axis_pts_ref"] = ref[0]
        if ref[0] in axis_pts:
            ax["address"] = axis_pts[ref[0]]["address"]
    return ax


def parse_characteristics(module, cms, rls, axis_pts, groups):
    maps = []
    for b in module.sub("CHARACTERISTIC"):
        t = b.toks
        if len(t) < 7:
            continue
        name, longid, ctype = t[0], _unq(t[1]), t[2]
        if ctype not in CHAR_TYPES:
            continue
        address = _num(t[3])
        deposit = t[4]
        conversion = t[6] if len(t) > 6 else None
        cm = cms.get(conversion, {})
        rl = rls.get(deposit, {})
        axes = [_axis_from_descr(a, axis_pts) for a in b.sub("AXIS_DESCR")]

        # 维度：MATRIX_DIM > NUMBER > AXIS_DESCR max_points > 类型默认
        dims = None
        md = _kw_after(t, "MATRIX_DIM", 3)
        if md:
            dd = [x for x in (_num(v) for v in md) if isinstance(x, int) and x > 0]
            dims = dd[:2] if len(dd) >= 2 and ctype == "MAP" else dd[:1]
        if dims is None:
            nn = _kw_after(t, "NUMBER", 1)
            if nn and _num(nn[0]):
                dims = [_num(nn[0])]
        if dims is None and axes:
            dims = [a["max_points"] for a in axes if a["max_points"]]
        if not dims:
            dims = [1]
        count = 1
        for d in dims:
            count *= d
        size_bytes = count * rl["width"] if rl.get("width") else None

        bo = _kw_after(t, "BYTE_ORDER", 1)
        maps.append({
            "name": name, "long_id": longid, "type": ctype,
            "address": address,
            "end": (address + size_bytes) if (address is not None and size_bytes) else None,
            "size_bytes": size_bytes,
            "dims": dims, "datatype": rl.get("datatype"),
            "record_layout": deposit,
            "factor": cm.get("factor"), "offset": cm.get("offset"),
            "unit": cm.get("unit", ""), "compu_method": conversion,
            "compu_note": cm.get("note", ""),
            "byte_order": bo[0] if bo else None,
            "group": groups.get(name),
            "axes": axes,
            "raw_min": None, "raw_max": None,
        })
    return maps


def cross_check_bin(maps, bin_buf, base, default_endian="<"):
    """--bin 自检：按 (address-base) 读 raw min/max。落带离谱 = 地址/基址错。"""
    for m in maps:
        fmt_ch, width = DATATYPES.get(m["datatype"] or "", (None, None))
        if m["address"] is None or not fmt_ch or not m["size_bytes"]:
            continue
        off = m["address"] - base
        if off < 0 or off + m["size_bytes"] > len(bin_buf):
            m["compu_note"] = (m["compu_note"] + "; " if m["compu_note"] else "") + \
                "bin 越界(offset=0x%X)——基址可疑" % off
            continue
        endian = default_endian
        if m["byte_order"] == "MSB_FIRST":
            endian = ">"
        elif m["byte_order"] == "MSB_LAST":
            endian = "<"
        n = m["size_bytes"] // width
        try:
            vals = struct.unpack_from(endian + str(n) + fmt_ch, bin_buf, off)
            m["raw_min"], m["raw_max"] = min(vals), max(vals)
        except struct.error:
            pass
    return maps


def parse(path, binpath=None, base=None):
    tokens = tokenize(_read_text(path))
    root = parse_blocks(tokens)
    projects = root.all_sub("PROJECT")
    modules = root.all_sub("MODULE")
    header = []
    if projects and projects[0].toks:
        header.append("PROJECT " + " ".join(_unq(x) for x in projects[0].toks[:2]))
    result = {"file": path, "map_count": 0, "header": header,
              "byte_order": None, "memory_segments": [], "maps": []}
    for module in modules:
        header.append("MODULE " + " ".join(_unq(x) for x in module.toks[:2]))
        for mc in module.sub("MOD_COMMON"):
            bo = _kw_after(mc.toks, "BYTE_ORDER", 1)
            if bo:
                result["byte_order"] = bo[0]
        cms = parse_compu_methods(module)
        rls = parse_record_layouts(module)
        apts = parse_axis_pts(module)
        groups = parse_groups(module)
        result["memory_segments"] += parse_mem_segments(module)
        result["maps"] += parse_characteristics(module, cms, rls, apts, groups)

    result["map_count"] = len(result["maps"])
    if binpath:
        bin_buf = open(binpath, "rb").read()
        endian = ">" if result["byte_order"] == "MSB_FIRST" else "<"
        if base is None:
            # 不擅自认定基址：给出 MEMORY_SEGMENT 候选后仍要求显式 --base
            result["base_candidates"] = [s["address"] for s in result["memory_segments"]
                                         if s["address"]]
        else:
            result["bin_base"] = base
            cross_check_bin(result["maps"], bin_buf, base, endian)
    return result


def report(res, flt=None, detail=None):
    maps = res["maps"]
    if flt:
        fl = flt.lower()
        maps = [m for m in maps if fl in m["name"].lower() or fl in (m["long_id"] or "").lower()
                or fl in (m["group"] or "").lower()]
    print("=" * 100)
    print("A2L 解析:", res["file"])
    if res["header"]:
        print("头部:", " | ".join(res["header"]))
    if res.get("byte_order"):
        print("BYTE_ORDER:", res["byte_order"],
              "(MSB_LAST=小端, MSB_FIRST=大端)")
    if res["memory_segments"]:
        segs = ", ".join("%s@0x%X" % (s["name"], s["address"])
                         for s in res["memory_segments"] if s["address"])
        print("内存段:", segs)
    if res.get("base_candidates") is not None:
        print("[!] 给了 --bin 但没给 --base。候选基址:",
              ", ".join("0x%X" % b for b in res["base_candidates"]) or "(A2L 无内存段记录)",
              "—— 确认后用 --base 重跑，勿擅自认定。")
    print("CHARACTERISTIC 总数: %d   本次显示: %d" % (res["map_count"], len(maps)))
    print("=" * 100)
    if detail:
        for m in maps:
            if m["name"] != detail:
                continue
            print(json.dumps(m, ensure_ascii=False, indent=2))
            return
        print("未找到 MAP:", detail)
        return
    print("%-28s %-6s %-10s %-9s %-8s %-11s %-9s %-10s %s" %
          ("MAP", "类型", "地址", "维度", "数据型", "factor", "offset", "单位", "raw 值域/备注"))
    print("-" * 100)
    for m in maps:
        addr = "0x%X" % m["address"] if m["address"] is not None else "?"
        dims = "x".join(str(d) for d in m["dims"])
        fac = ("%g" % m["factor"]) if m["factor"] is not None else "非线性"
        ofs = ("%g" % m["offset"]) if m["offset"] is not None else "-"
        tail = ""
        if m["raw_min"] is not None:
            tail = "%g..%g" % (m["raw_min"], m["raw_max"])
        elif m["compu_note"]:
            tail = m["compu_note"]
        print("%-28s %-6s %-10s %-9s %-8s %-11s %-9s %-10s %s" %
              (m["name"][:28], m["type"], addr, dims, m["datatype"] or "?",
               fac, ofs, (m["unit"] or "")[:10], tail))
    print("-" * 100)
    print("提醒: A2L 地址通常≠bin 偏移(须减段基址); 非线性换算须人工确认;",
          "字节读写交 bin 引擎; 写盘后 checksum 交 checksum-crc-engine。")


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

_SAMPLE_A2L = r'''
ASAP2_VERSION 1 61
/begin PROJECT Demo "selftest project"
/begin MODULE ECU "MED17 demo"
  /begin MOD_COMMON "" BYTE_ORDER MSB_LAST /end MOD_COMMON
  /begin MOD_PAR ""
    /begin MEMORY_SEGMENT Data "" DATA FLASH INTERN 0x800000 0x10000 -1 -1 -1 -1 -1
    /end MEMORY_SEGMENT
  /end MOD_PAR
  /begin COMPU_METHOD CM_ZW "spark" RAT_FUNC "%6.2f" "deg"
    COEFFS 0 1.3333333 170.6666 0 0 1
  /end COMPU_METHOD
  /begin COMPU_METHOD CM_ONE "identity" IDENTICAL "%6.2f" "Nm" /end COMPU_METHOD
  /begin COMPU_METHOD CM_LIN "linear" LINEAR "%6.2f" "%" COEFFS_LINEAR 0.1 0 /end COMPU_METHOD
  /begin COMPU_METHOD CM_TAB "verbal" TAB_VERB "%3.0f" "" COMPU_TAB_REF T1 /end COMPU_METHOD
  /begin RECORD_LAYOUT RL_U8 FNC_VALUES 1 UBYTE COLUMN_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_U16 FNC_VALUES 1 UWORD COLUMN_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_AX FNC_VALUES 1 UWORD INDEX_INCR DIRECT /end RECORD_LAYOUT
  /begin AXIS_PTS AX_RPM "rpm axis" 0x800010 NO_INPUT RL_AX 100.0 CM_ONE 4 0.0 8000.0
  /end AXIS_PTS
  /begin CHARACTERISTIC KFZW "base spark map" MAP 0x800100 RL_U8 200.0 CM_ZW -30.0 60.0
    /begin IF_DATA XCP junk tokens here /end IF_DATA
    /begin AXIS_DESCR COM_AXIS N CM_ONE 4 0.0 8000.0
      AXIS_PTS_REF AX_RPM
    /end AXIS_DESCR
    /begin AXIS_DESCR STD_AXIS RL CM_LIN 3 0.0 200.0 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC SCALAR_T "torque scalar" VALUE 0x800200 RL_U16 100.0 CM_ONE 0 500
  /end CHARACTERISTIC
  /begin CHARACTERISTIC BLK_V "val block" VAL_BLK 0x800300 RL_U16 1.0 CM_LIN 0 100
    NUMBER 6
  /end CHARACTERISTIC
  /begin FUNCTION FN_IGN "ignition"
    /begin DEF_CHARACTERISTIC KFZW /end DEF_CHARACTERISTIC
  /end FUNCTION
/end MODULE
/end PROJECT
'''


def selftest():
    import tempfile, os
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("ok " if cond else "FAIL ") + msg)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        a2l = os.path.join(td, "t.a2l")
        open(a2l, "w", encoding="utf-8").write(_SAMPLE_A2L)
        res = parse(a2l)
        by = {m["name"]: m for m in res["maps"]}
        chk(res["map_count"] == 3, "CHARACTERISTIC 计数=3")
        k = by["KFZW"]
        chk(k["type"] == "MAP" and k["address"] == 0x800100, "KFZW 地址/类型")
        chk(k["dims"] == [4, 3] and k["size_bytes"] == 12, "KFZW 维度 4x3 尺寸 12B")
        # RAT_FUNC: raw=(1.3333*phys+170.67)/1 -> phys=raw*0.75-128
        chk(k["factor"] is not None and abs(k["factor"] - 0.75) < 1e-4
            and abs(k["offset"] + 128) < 0.01, "RAT_FUNC 折算 phys=raw*0.75-128")
        chk(k["axes"][0]["axis_pts_ref"] == "AX_RPM"
            and k["axes"][0]["address"] == 0x800010, "COM_AXIS 解到 AXIS_PTS 地址")
        chk(k["group"] == "FN_IGN", "FUNCTION 分组")
        chk(by["SCALAR_T"]["factor"] == 1.0 and by["SCALAR_T"]["size_bytes"] == 2,
            "IDENTICAL 标量")
        b = by["BLK_V"]
        chk(b["dims"] == [6] and b["size_bytes"] == 12 and b["factor"] == 0.1,
            "VAL_BLK NUMBER=6, LINEAR factor=0.1")
        chk(res["memory_segments"][0]["address"] == 0x800000, "MEMORY_SEGMENT 基址")
        # --bin 交叉自检: 造 0x800 字节假 bin, KFZW 12 字节填 10..21
        binp = os.path.join(td, "t.bin")
        buf = bytearray(0x800)
        for i in range(12):
            buf[0x100 + i] = 10 + i
        struct.pack_into("<H", buf, 0x200, 400)
        open(binp, "wb").write(bytes(buf))
        res2 = parse(a2l, binpath=binp, base=0x800000)
        by2 = {m["name"]: m for m in res2["maps"]}
        chk(by2["KFZW"]["raw_min"] == 10 and by2["KFZW"]["raw_max"] == 21,
            "--bin 自检 raw 值域 10..21")
        chk(by2["SCALAR_T"]["raw_min"] == 400, "--bin 标量 u16 解码")
        res3 = parse(a2l, binpath=binp)  # 不给 base
        chk(res3.get("base_candidates") == [0x800000], "缺 --base 时给候选不擅断")
    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Parse A2L (ASAP2) for calibration map definitions")
    ap.add_argument("a2l", nargs="?")
    ap.add_argument("--bin", dest="binpath", default=None, help="ORI bin 用于 raw 值域自检")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=None,
                    help="bin 基址(ECU 地址-基址=文件偏移)，如 0x800000")
    ap.add_argument("--filter", default=None, help="名称/描述/分组子串过滤")
    ap.add_argument("--map", dest="detail", default=None, help="单张 MAP 详情")
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not a.a2l:
        ap.error("需要 A2L 文件路径（或 --selftest）")
    res = parse(a.a2l, binpath=a.binpath, base=a.base)
    report(res, flt=a.filter, detail=a.detail)
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("JSON ->", a.json)


if __name__ == "__main__":
    main()

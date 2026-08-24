# -*- coding: utf-8 -*-
# Ghidra script (Jython 2.7 compatible) — mark calibration maps in the listing.
# @category ECU
#
# mark_winols_maps.py — 把 map 定义 JSON 批量标进 Ghidra：createData + label + 注释。
#
# 接受三种 JSON 来源（自动识别）:
#   1. 手写清单:            [{"name","address","rows","cols","width"}, ...]
#   2. parse_ols.py 输出:   {"maps": [{"name","address","size_bytes",...}]}
#   3. parse_a2l.py 输出:   {"maps": [{"name","address","dims","datatype","size_bytes",...}]}
#      （winols-ols-parser / damos-a2l-parser 两个技能的 --json 结果直接可用）
#
# 用法（Ghidra 内，二选一）:
#   A. Script Manager: Manage Script Directories 加入本 scripts 目录 → 运行本脚本 → 弹窗选 JSON。
#   B. Window > Python: exec(open(r"...\mark_winols_maps.py").read())
#
# 用法（Ghidra 外，仅自测归一化逻辑）:
#   python mark_winols_maps.py --selftest
#
# 纪律: 地址标记 ≠ 地址确认。标完仍须按 SKILL.md 输出纪律给 xref 路径 + 轴单调 + 解码落带。

import json
import re

try:
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")  # CPython3 控制台中文；Jython 无此属性，忽略
except Exception:
    pass


def sanitize(name):
    """Ghidra label 只留 [A-Za-z0-9_]；WinOLS/A2L 命名常含空格/括号/单位。"""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return s or "unnamed"


def normalize_maps(obj):
    """把三种来源的 JSON 归一成 [{name,address,rows,cols,width,is_float}]。"""
    if isinstance(obj, dict) and "maps" in obj:
        obj = obj["maps"]
    width_by_dtype = {"UBYTE": 1, "SBYTE": 1, "UWORD": 2, "SWORD": 2,
                      "ULONG": 4, "SLONG": 4, "A_UINT64": 8, "A_INT64": 8,
                      "FLOAT16_IEEE": 2, "FLOAT32_IEEE": 4, "FLOAT64_IEEE": 8}
    out = []
    for m in obj:
        addr = m.get("address")
        if isinstance(addr, str):
            addr = int(addr, 16)
        if addr is None:
            continue
        rows, cols = m.get("rows"), m.get("cols")
        dims = m.get("dims")
        if not rows and dims:
            rows = dims[0] if len(dims) > 0 else 1
            cols = dims[1] if len(dims) > 1 else 1
        dtype = (m.get("datatype") or "").upper()
        width = m.get("width") or width_by_dtype.get(dtype)
        size = m.get("size_bytes")
        if width is None and size and rows:
            n = rows * (cols or 1)
            if n and size % n == 0 and size // n in (1, 2, 4, 8):
                width = size // n
        if width is None and size:
            # parse_ols 无维度信息时按 u16 数组铺满（WinOLS 最常见宽度）
            width, rows, cols = 2, size // 2, 1
        out.append({"name": m.get("name", "?"), "address": addr,
                    "rows": rows or 1, "cols": cols or 1, "width": width or 2,
                    "is_float": "FLOAT" in dtype})
    return out


def _mark_in_ghidra(maps):
    from ghidra.program.model.data import (ArrayDataType, ByteDataType,
                                           UnsignedShortDataType,
                                           UnsignedIntegerDataType,
                                           UnsignedLongLongDataType,
                                           FloatDataType, DoubleDataType)
    listing = currentProgram.getListing()          # noqa: F821 (Ghidra 注入)
    by_width = {1: ByteDataType, 2: UnsignedShortDataType,
                4: UnsignedIntegerDataType, 8: UnsignedLongLongDataType}
    n_data = n_label = 0
    for m in maps:
        addr = toAddr(m["address"])                # noqa: F821
        count = m["rows"] * m["cols"]
        if m["is_float"]:
            base = FloatDataType() if m["width"] == 4 else DoubleDataType()
        else:
            base = by_width.get(m["width"], UnsignedShortDataType)()
        listing.setComment(addr, 0, "MAP %s  %dx%d  w%d" %
                           (m["name"], m["rows"], m["cols"], m["width"]))
        try:
            createData(addr, ArrayDataType(base, count, m["width"]))  # noqa: F821
            n_data += 1
        except Exception:
            pass  # 已有定义/冲突——保留现状，注释和 label 仍然打
        try:
            createLabel(addr, "map_" + sanitize(m["name"]), True)     # noqa: F821
            n_label += 1
        except Exception:
            pass
    print("marked: %d maps (%d data defined, %d labels)" %
          (len(maps), n_data, n_label))


def selftest():
    ok = True

    def chk(cond, msg):
        # Jython 2.7 无 nonlocal——用列表桩在 CPython 下也兼容；这里简单断言
        assert cond, "FAIL " + msg
        print("ok " + msg)

    # 来源 1: 手写清单，hex 字符串地址
    r = normalize_maps([{"name": "KF WG (mbar)", "address": "0x1A0000",
                         "rows": 16, "cols": 12, "width": 2}])
    chk(r[0]["address"] == 0x1A0000 and r[0]["width"] == 2, "手写清单 hex 地址")
    chk(sanitize("KF WG (mbar)") == "KF_WG_mbar", "label 清洗")
    # 来源 2: parse_ols 输出（无维度，按 u16 铺满）
    r = normalize_maps({"maps": [{"name": "Engine torque", "address": 0x180100,
                                  "size_bytes": 384}]})
    chk(r[0]["rows"] == 192 and r[0]["width"] == 2, "parse_ols 无维度按 u16 铺满")
    # 来源 3: parse_a2l 输出（dims + datatype）
    r = normalize_maps({"maps": [{"name": "KFZW", "address": 0x800100,
                                  "dims": [4, 3], "datatype": "UBYTE",
                                  "size_bytes": 12}]})
    chk(r[0]["rows"] == 4 and r[0]["cols"] == 3 and r[0]["width"] == 1,
        "parse_a2l dims/datatype")
    r = normalize_maps({"maps": [{"name": "T", "address": 0x100, "dims": [8],
                                  "datatype": "FLOAT32_IEEE", "size_bytes": 32}]})
    chk(r[0]["is_float"] and r[0]["width"] == 4, "float32 识别")
    print("ALL PASS")


def main():
    try:
        currentProgram  # noqa: F821 — 只有 Ghidra 里存在
        in_ghidra = True
    except NameError:
        in_ghidra = False

    if in_ghidra:
        f = askFile("Select maps JSON (parse_ols/parse_a2l --json output ok)", "Open")  # noqa: F821
        fh = open(f.getAbsolutePath())
        try:
            obj = json.load(fh)
        finally:
            fh.close()
        _mark_in_ghidra(normalize_maps(obj))
    else:
        import sys
        if "--selftest" in sys.argv:
            selftest()
        else:
            print("此脚本在 Ghidra 内运行（Script Manager 或 Window > Python）。"
                  "Ghidra 外仅支持 --selftest。")


# Ghidra 的 Jython 以顶层执行；被当模块 import（如 export 脚本自测）时不跑 main
if __name__ != "mark_winols_maps":
    main()

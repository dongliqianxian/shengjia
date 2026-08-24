# -*- coding: utf-8 -*-
# Ghidra script (Jython 2.7 compatible) — export map labels back to WinOLS.
# @category ECU
#
# export_maps_to_winols.py — 反方向桥：把 Ghidra 里所有 "map_" 前缀的 label
# （连同其定义数据的字节长度）导出为 CSV + JSON，供 WinOLS 手动建图 /
# 与 parse_ols.py、parse_a2l.py 的结果 diff 比对。
#
# 输出两个文件（弹窗选保存路径，自动加后缀）:
#   <名字>.csv  : name;address_hex;size_bytes    （WinOLS 手动建图对照用）
#   <名字>.json : {"maps":[{"name","address","size_bytes"}]}  —— 与
#                 parse_ols.py / parse_a2l.py 输出同构，可回喂 mark_winols_maps.py
#
# 用法（Ghidra 内）: Script Manager 运行，或 Window > Python:
#   exec(open(r"...\export_maps_to_winols.py").read())
# 用法（Ghidra 外）: python export_maps_to_winols.py --selftest
#
# 纪律: 导出的 address 是 Ghidra 内存地址；对回 bin 文件偏移须减镜像基址
# （ghidra-ida-setup 定的 image base），不要直接当文件偏移用。

import json

try:
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")  # CPython3 控制台中文；Jython 无此属性，忽略
except Exception:
    pass

PREFIX = "map_"


def rows_to_csv(rows):
    lines = ["name;address_hex;size_bytes"]
    for r in rows:
        lines.append("%s;0x%X;%d" % (r["name"], r["address"], r["size_bytes"]))
    return "\n".join(lines) + "\n"


def rows_to_json(rows):
    return json.dumps({"maps": rows, "map_count": len(rows)},
                      ensure_ascii=False, indent=2)


def _collect_in_ghidra():
    st = currentProgram.getSymbolTable()      # noqa: F821 (Ghidra 注入)
    listing = currentProgram.getListing()     # noqa: F821
    rows = []
    it = st.getAllSymbols(True)
    while it.hasNext():
        s = it.next()
        name = s.getName()
        if not name.startswith(PREFIX):
            continue
        addr = s.getAddress()
        d = listing.getDataAt(addr)
        rows.append({"name": name[len(PREFIX):],
                     "address": addr.getOffset(),
                     "size_bytes": d.getLength() if d is not None else 0})
    rows.sort(key=lambda r: r["address"])
    return rows


def selftest():
    rows = [{"name": "KFZW", "address": 0x800100, "size_bytes": 12},
            {"name": "KF_WG_mbar", "address": 0x1A0000, "size_bytes": 384}]
    csv = rows_to_csv(rows)
    assert "KFZW;0x800100;12" in csv, "FAIL csv 行格式"
    print("ok csv 行格式")
    obj = json.loads(rows_to_json(rows))
    assert obj["map_count"] == 2 and obj["maps"][1]["address"] == 0x1A0000, "FAIL json 同构"
    print("ok json 同构(可回喂 mark_winols_maps.py)")
    # 回喂验证: mark_winols_maps.normalize_maps 能吃这个输出
    try:
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from mark_winols_maps import normalize_maps
        nm = normalize_maps(obj)
        assert nm[0]["address"] == 0x800100 and nm[0]["rows"] == 6, "FAIL 回喂 normalize"
        print("ok 回喂 normalize_maps (12B -> u16 x6)")
    except ImportError:
        print("skip 回喂验证（mark_winols_maps.py 不在同目录）")
    print("ALL PASS")


def main():
    try:
        currentProgram  # noqa: F821
        in_ghidra = True
    except NameError:
        in_ghidra = False

    if in_ghidra:
        rows = _collect_in_ghidra()
        if not rows:
            print("没有 '%s' 前缀的 label——先用 mark_winols_maps.py 或手工打标。" % PREFIX)
            return
        f = askFile("Save export (writes .csv and .json)", "Save")  # noqa: F821
        base = f.getAbsolutePath()
        for ext in (".csv", ".json"):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
        fh = open(base + ".csv", "w")
        try:
            fh.write(rows_to_csv(rows))
        finally:
            fh.close()
        fh = open(base + ".json", "w")
        try:
            fh.write(rows_to_json(rows))
        finally:
            fh.close()
        print("exported %d maps -> %s.csv / .json" % (len(rows), base))
        print("[!] address 是 Ghidra 内存地址，对回 bin 偏移须减 image base。")
    else:
        import sys
        if "--selftest" in sys.argv:
            selftest()
        else:
            print("此脚本在 Ghidra 内运行。Ghidra 外仅支持 --selftest。")


# Ghidra 的 Jython 以顶层执行；被当模块 import 时不跑 main
if __name__ != "export_maps_to_winols":
    main()

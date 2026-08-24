#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dtc_locate.py — 在 ECU bin 里定位一个 DTC(故障码)的地址,以【无 A2L】字节级定位为主。

dtc-locator 技能的执行脚本。核心认知:P0240 不是以 ASCII "P0240" 存的,而是按
SAE J2012 / ISO 15031-6 编码成 2 字节值(P0240 -> 0x0240)。而 0x0240 是极常见的
字节组合,单点搜索必然满屏误报——所以正解是【找结构】:DTC 配置表是定长记录数组,
真正的信号是"DTC 形值以固定 stride 重复出现"。本脚本自动探测该表,再在表内定位目标码,
按可信度分级输出(表内=高;孤立命中=低,大概率巧合)。

架构:编码层是全球统一标准,与 ECU 无关;只有表的记录结构因平台而异,而 stride 能自动
探测。所以这是【一个通用引擎 + 可选平台 profile】,不是每个 ECU 一个脚本。字节序沿用
multi-vendor-ecu-engine 的核定:TriCore/AURIX(EDC17/MD1/DCM)=小端,SH/PPC=大端。

用法:
    python dtc_locate.py --bin ECU.bin --code P0240                  # 自动:双字节序 + 自动找表
    python dtc_locate.py --bin ECU.bin --code P0240 --endian le      # 只按小端(TriCore/AURIX 默认)
    python dtc_locate.py --bin ECU.bin --code P0240 --profile edc17  # 用平台 profile 定字节序/字段
    python dtc_locate.py --bin ECU.bin --tables                      # 只列出探测到的所有 DTC 表
    python dtc_locate.py --code P0240 --encode-only                  # 只算编码(不需要 bin)
    python dtc_locate.py --selftest

纪律(doctrine):
  * 这是【诊断/定位工具】。定位到的是 DTC 的配置记录(码+debounce+灯行为+状态掩码),
    用于理解某码为何触发、修车、标定验证。
  * 单点命中不是结论——必须落在探测到的 DTC 表(等距 run)里才算高可信;孤立命中标低可信。
  * OEM 内部常用 DFC/内部事件号,P 码经映射表;字节搜命中的是"对外 OBD 码表"。有 A2L 时
    用 damos-a2l-parser 的 DFC 标签交叉确认更准(本脚本聚焦无 A2L 的字节级路径)。
"""
import sys, struct, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LETTERS = ["P", "C", "B", "U"]          # bits15-14
LETTER_BITS = {"P": 0, "C": 1, "B": 2, "U": 3}

# 记录 stride 候选。含 2 字节 OBD 记录(偶数)与 3 字节 UDS 记录(3 及其倍数、奇数)。
# run_through 取"最长升序 run"自动选真 stride:UDS 3 字节表在 stride=3 成最长 run,
# 6 字节 OBD 表在 stride=6 成最长 run——无需预知。
ALL_STRIDES = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 15, 16, 18, 20, 24]

# 平台 profile。**要点(写进输出):字节序跟 MCU 核走,不跟车标**(multi-vendor 原则#1)。
# 但注意:DTC 码是【协议值】,UDS 多按 MSB-first(大端)存储,与平台数据端序无关——
# 所以本工具 DTC 码【默认双序搜索】,profile 的 endian 只是平台【数据层】端序参考,
# 不用来限制码搜索(除非用户显式 --endian)。
PROFILES = {
    # Bosch 柴油
    "edc17":  {"endian": "le", "note": "Bosch EDC17 柴油, TriCore, 数据小端"},
    "md1":    {"endian": "le", "note": "Bosch MD1 柴油, AURIX, 数据小端"},
    # Bosch 汽油
    "med17":  {"endian": "le", "note": "Bosch MED17 汽油, TriCore, 数据小端(VAG EA888 等)"},
    "mevd17": {"endian": "le", "note": "Bosch MEVD17 汽油, TriCore, 数据小端(BMW N/B 系)"},
    "mg1":    {"endian": "le", "note": "Bosch MG1 汽油, AURIX, 数据小端"},
    # Conti/西门子
    "simos":  {"endian": "le", "note": "Conti/西门子 SIMOS 等, TriCore, 数据小端(VAG/福特/现代)"},
    # Delphi
    "dcm":    {"endian": "le", "note": "Delphi DCM(3.7/6.2/7.1AP), TriCore, 数据小端"},
    # DENSO 新代
    "denso_rh850": {"endian": "le", "note": "DENSO 新代 RH850, 数据小端(丰田/雷克萨斯/本田/马自达)"},
    # 大端核
    "sh":     {"endian": "be", "note": "Renesas SH 老件, **大端**(日产/斯巴鲁/三菱/老 DENSO)"},
    "ppc":    {"endian": "be", "note": "Freescale/NXP PowerPC, **大端**(GM/福特/部分 Marelli)"},
    # 自动
    "auto":   {"endian": "both", "note": "自动:数据端序不限,DTC 码双序搜"},
}


# ---------------------------------------------------------------------------
# 编码 / 解码 —— SAE J2012 / ISO 15031-6 (2 字节)
# ---------------------------------------------------------------------------

def encode_dtc(code):
    """'P0240' -> 0x0240。返回 16 位整数。first digit 必须 0-3(2 字节编码约束)。"""
    code = code.strip().upper()
    if len(code) != 5 or code[0] not in LETTER_BITS:
        raise ValueError("DTC 格式应为 字母+4位, 如 P0240 / U0100 (字母∈P C B U): %r" % code)
    try:
        digits = int(code[1:], 16)          # '0240' -> 0x0240
    except ValueError:
        raise ValueError("后 4 位应为十六进制: %r" % code)
    if (digits >> 12) & 0x3 > 3 or (digits & 0xC000):
        raise ValueError("第 1 位数字必须为 0-3(2 字节 DTC 编码约束): %r" % code)
    return (LETTER_BITS[code[0]] << 14) | digits


def decode_dtc(v):
    """0x0240 -> 'P0240'。"""
    v &= 0xFFFF
    return "%s%d%X%X%X" % (LETTERS[(v >> 14) & 3], (v >> 12) & 3,
                           (v >> 8) & 0xF, (v >> 4) & 0xF, v & 0xF)


def uds_3byte_hint(v):
    """UDS(ISO 14229)常见 3 字节 = 高 2 字节码 + 失败类型字节 FTB。给出常见 FTB 示例。"""
    return ["%02X%02X%02X" % ((v >> 8) & 0xFF, v & 0xFF, ftb) for ftb in (0x00, 0x1C, 0x2F, 0x87)]


# ---------------------------------------------------------------------------
# 字节读取 / 搜索
# ---------------------------------------------------------------------------

def _rd16(buf, off, endian):
    return struct.unpack_from("<H" if endian == "le" else ">H", buf, off)[0]


def find_raw_hits(buf, value, endian):
    """目标 2 字节值在文件里的所有出现偏移(按给定字节序)。"""
    pat = struct.pack("<H" if endian == "le" else ">H", value)
    hits, i = [], buf.find(pat)
    while i != -1:
        hits.append(i)
        i = buf.find(pat, i + 1)
    return hits


def _is_dtc_shaped(v):
    """粗筛:非 0、非 0xFFFF。真/假由 run 结构判定,不靠单值。"""
    return v != 0x0000 and v != 0xFFFF


def detect_tables(buf, endian, min_run=6, strides=None):
    """探测 DTC 配置表。DTC 表的本质特征是【码按数值递增排序】的定长记录数组——
    所以 run 的扩展条件是"下一个 slot 也是 DTC 形值 且 严格大于前一个",这一条既锁定
    真实记录 stride(把"码+配置字节"混读的假 stride 一步排除),又天然分离不同子系统段
    (跨字母/跨段回落处自然断开)。返回 [{start,stride,count,letters,score}],按 score 降序。

    注:未按数值排序的表会被切成若干升序子段——每段仍是真实区域,足以定位目标码。"""
    n = len(buf)
    strides = strides or ALL_STRIDES
    found = []
    for stride in strides:
        covered = set()          # 本 stride 下已归属某 run 的起点(防重复扩展,近线性)
        off = 0
        while off + 2 <= n:
            if off in covered:
                off += 1
                continue
            v0 = _rd16(buf, off, endian)
            if not _is_dtc_shaped(v0):
                off += 1
                continue
            vals, k, prev = [v0], off + stride, v0
            while k + 2 <= n:
                v = _rd16(buf, k, endian)
                if not _is_dtc_shaped(v) or v <= prev:   # 递增才是 DTC 表
                    break
                vals.append(v)
                prev = v
                k += stride
            run = len(vals)
            if run >= min_run:
                for j in range(run):
                    covered.add(off + j * stride)
                letters = set((v >> 14) & 3 for v in vals)
                # 全递增已保证。纯/少字母类别的长 run 才像真 DTC 表(别名表多跨字母);
                # 偏移 1 字节的别名 run 会跨 P/C/U,靠字母加分被真表盖过。tie 偏好更大 stride。
                score = run * 4 + (run if len(letters) <= 2 else 0) + stride * 0.01
                found.append({"start": off, "stride": stride, "count": run,
                              "letters": sorted(LETTERS[x] for x in letters),
                              "score": score})
            off += 1                 # 不跳跃:让真表起点(可能紧邻别名 run)也被测到
    found.sort(key=lambda t: -t["score"])
    return found


def run_through(buf, off, stride, endian):
    """以 off 为中心,沿 stride 向两侧扩展"DTC 形值 且 严格升序"的 run。
    直接回答"一条 DTC 升序 run 是否穿过这个精确命中点",天然免疫同 stride 的别名列
    (别名列相位不同,不会正好穿过真码所在偏移)。返回 {start,stride,count,index} 或 None。"""
    n = len(buf)
    v = _rd16(buf, off, endian)
    if not _is_dtc_shaped(v):
        return None
    start, k, prev = off, off - stride, v          # 向前(要求更小)
    while k >= 0:
        vv = _rd16(buf, k, endian)
        if not _is_dtc_shaped(vv) or vv >= prev:
            break
        start, prev, k = k, vv, k - stride
    end, k, prev = off, off + stride, v            # 向后(要求更大)
    while k + 2 <= n:
        vv = _rd16(buf, k, endian)
        if not _is_dtc_shaped(vv) or vv <= prev:
            break
        end, prev, k = k, vv, k + stride
    count = (end - start) // stride + 1
    return {"start": start, "stride": stride, "count": count,
            "index": (off - start) // stride}


def _best_run_through(buf, off, endian, strides):
    best = None
    for s in strides:
        r = run_through(buf, off, s, endian)
        if r and (best is None or r["count"] > best["count"]):
            best = r
    return best


def locate(buf, code=None, raw_value=None, endian=None, profile_name="auto"):
    """定位一个 DTC。可信度由"穿过命中点的升序 run"直接判定,而非全局找表后测归属。

    code:       'P0240' 形式,按 SAE J2012 编码成 2 字节。
    raw_value:  直接给 2 字节原始值(老件 KWP / 厂内部编号,绕过 J2012 编码)。
    endian:     None/'both' = 双序搜索(**推荐**——DTC 码是协议值,UDS 多按 MSB-first/大端存,
                与平台数据端序无关);'le'/'be' = 用户显式限制。
    winning stride==3 判为 UDS 3 字节记录,额外解出 FTB 与 Pxxxx-FF 完整码。"""
    if raw_value is not None:
        value = raw_value & 0xFFFF
        shown = "RAW 0x%04X" % value
    else:
        value = encode_dtc(code)
        shown = code.upper()
    # DTC 码字节序默认双序(见 PROFILES 注:码序是协议序,≠平台数据端序)。
    code_orders = [endian] if endian in ("le", "be") else ["le", "be"]

    out = {"code": shown, "value": value, "value_hex": "0x%04X" % value,
           "bytes_le": "%02X %02X" % (value & 0xFF, (value >> 8) & 0xFF),
           "bytes_be": "%02X %02X" % ((value >> 8) & 0xFF, value & 0xFF),
           "uds_3byte_examples": uds_3byte_hint(value),
           "profile": profile_name, "results": []}

    for e in code_orders:
        for h in find_raw_hits(buf, value, e):
            r = _best_run_through(buf, h, e, ALL_STRIDES)
            if r and r["count"] >= 6:
                conf = "HIGH" if r["count"] >= 12 else "MEDIUM"
                stride = r["stride"]
                rec = buf[h:h + stride]
                res = {"endian": e, "offset": h, "confidence": conf,
                       "table_start": r["start"], "stride": stride,
                       "table_count": r["count"], "record_index": r["index"],
                       "record_bytes": rec.hex(" "), "kind": "OBD2"}
                if stride == 3:                 # 3 字节 UDS 记录: [hi lo ftb]
                    res["kind"] = "UDS3"
                    res["ftb"] = rec[2]
                    res["code_full"] = "%s-%02X" % (decode_dtc(value), rec[2])
                out["results"].append(res)
            else:
                out["results"].append({"endian": e, "offset": h, "confidence": "LOW",
                                       "record_bytes": None, "kind": None})
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    out["results"].sort(key=lambda r: (rank[r["confidence"]], r["offset"]))
    return out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def report(out):
    print("=" * 78)
    print("DTC 定位: %s  =  %s   (小端 %s / 大端 %s)" %
          (out["code"], out["value_hex"], out["bytes_le"], out["bytes_be"]))
    print("UDS 3字节示例(高2字节+FTB):", ", ".join(out["uds_3byte_examples"]))
    print("profile:", out["profile"], "-", PROFILES[out["profile"]]["note"])
    print("=" * 78)
    if not out["results"]:
        print("未在文件中找到该值。可能:该码不在此 ECU / 用了内部 DFC 编号(试 --raw 直接搜) /")
        print("存为 3 字节 UDS 被其它字段打断 / 该 bin 是数据段外的诊断段。")
        return
    hi = [r for r in out["results"] if r["confidence"] in ("HIGH", "MEDIUM")]
    lo = [r for r in out["results"] if r["confidence"] == "LOW"]
    if hi:
        print("★ 落在 DTC 表内的命中(可信):")
        for r in hi:
            order = "大端/协议序(UDS 典型)" if r["endian"] == "be" else "小端/数据序(2字节 OBD 典型)"
            kind = "UDS 3字节" if r.get("kind") == "UDS3" else "2字节 OBD/KWP"
            print("  [%s] 偏移 0x%06X  码序=%s  记录=%s  表@0x%06X stride=%d 共%d条 第%d条"
                  % (r["confidence"], r["offset"], order, kind, r["table_start"],
                     r["stride"], r["table_count"], r["record_index"]))
            print("        记录字节: %s" % r["record_bytes"])
            if r.get("kind") == "UDS3":
                print("        UDS 完整码: %s   (第3字节 0x%02X = 失败类型 FTB)"
                      % (r["code_full"], r["ftb"]))
            print("        (字段顺序因平台而异:码 + [FTB] + debounce/灯/状态掩码;精确含义需 A2L/ghidra 确认)")
    if lo:
        print("孤立命中(低可信,大概率巧合,共 %d 处):" % len(lo))
        for r in lo[:8]:
            print("  [LOW ] 偏移 0x%06X  字节序=%s" % (r["offset"], r["endian"]))
        if len(lo) > 8:
            print("  ... 其余 %d 处略" % (len(lo) - 8))
    print("-" * 78)
    print("提醒: 高可信命中才是 DTC 配置记录;要拿字段含义,用 damos-a2l-parser 对 A2L 的 DFC")
    print("标签交叉确认,或用 ghidra-tricore-disasm 看引用该表的诊断例程。")


def report_tables(buf, endian):
    for e in (["le", "be"] if endian == "both" else [endian]):
        tabs = detect_tables(buf, e)
        print("=== 字节序 %s: 探测到 %d 张候选 DTC 表 ===" % (e, len(tabs)))
        for t in tabs[:15]:
            first = decode_dtc(_rd16(buf, t["start"], e))
            last = decode_dtc(_rd16(buf, t["start"] + (t["count"] - 1) * t["stride"], e))
            print("  @0x%06X stride=%2d 共%3d条 字母%s  %s..%s (score=%.0f)"
                  % (t["start"], t["stride"], t["count"],
                     "".join(t["letters"]), first, last, t["score"]))


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("ok " if cond else "FAIL ") + msg)
        ok = ok and cond

    # 编码/解码往返
    chk(encode_dtc("P0240") == 0x0240, "P0240 -> 0x0240")
    chk(encode_dtc("U0100") == 0xC100, "U0100 -> 0xC100")
    chk(encode_dtc("P1234") == 0x1234, "P1234 -> 0x1234")
    chk(encode_dtc("C1234") == 0x5234, "C1234 -> 0x5234")
    chk(encode_dtc("B0001") == 0x8001, "B0001 -> 0x8001")
    for c in ("P0240", "U0100", "P1234", "C1234", "B0001", "P2563"):
        chk(decode_dtc(encode_dtc(c)) == c, "往返 %s" % c)
    try:
        encode_dtc("P5000"); chk(False, "P5000 应报错(首位>3)")
    except ValueError:
        chk(True, "非法首位数字被拒")

    # 造合成 bin:噪声前缀 + 一张 stride=6 的 DTC 表(24 条升序,含 P0240) + 噪声后缀。
    # 配置字节用真实的非递增模式(状态掩码/灯/debounce 常量),避免人为制造升序别名列。
    import random
    random.seed(1)
    pre = bytes(random.randrange(256) for _ in range(0x200))
    codes = [0x0230 + i for i in range(24)]      # 升序 0x0230..0x0247,P0240 在索引 16
    idx240 = codes.index(0x0240)
    table = bytearray()
    for cv in codes:
        table += struct.pack("<H", cv)                 # 2 字节码(小端)
        table += bytes([0x2F, 0xFF, 0x0A, 0x00])       # 4 字节配置(状态掩码/灯/debounce,恒定)
    post = bytes(random.randrange(256) for _ in range(0x150))
    blob = pre + bytes(table) + post
    table_off = len(pre)
    hit_off = table_off + idx240 * 6

    out = locate(blob, code="P0240", endian="le")     # 2字节 OBD 表(小端存)
    hi = [r for r in out["results"] if r["confidence"] in ("HIGH", "MEDIUM")]
    chk(len(hi) >= 1, "P0240 找到可信命中")
    if hi:
        r = hi[0]
        chk(r["offset"] == hit_off, "命中偏移 = 表起点 + 第%d条 (0x%X)" % (idx240, hit_off))
        chk(r["stride"] == 6, "run 穿过命中点的 stride=6")
        chk(r["confidence"] == "HIGH", "24 条表 -> HIGH 可信")
        chk(r["kind"] == "OBD2", "6字节记录判为 2字节 OBD")
        chk(r["record_bytes"].startswith("40 02"), "记录以小端码 40 02 开头")
        gstart, cnt, st = r["table_start"], r["table_count"], r["stride"]
        chk((hit_off - gstart) % st == 0 and gstart <= table_off
            and gstart + (cnt - 1) * st >= table_off + 23 * 6,
            "run 相位对齐且覆盖真表 24 条")

    # 大端搜同一值应无可信命中(数据是小端存的)
    out_be = locate(blob, code="P0240", endian="be")
    chk(not any(r["confidence"] in ("HIGH", "MEDIUM") for r in out_be["results"]),
        "错字节序(be)无可信命中")

    # [高] 3 字节 UDS 表(MSB-first): [hi lo ftb] 升序,含 P0240;默认双序应从大端命中
    pre2 = bytes(random.randrange(256) for _ in range(0x180))
    ucodes = [0x0230 + i for i in range(20)]
    uidx = ucodes.index(0x0240)
    ftb_240 = (0x10 + uidx) & 0xFF
    ut = bytearray()
    for i, cv in enumerate(ucodes):
        ut += bytes([(cv >> 8) & 0xFF, cv & 0xFF, (0x10 + i) & 0xFF])   # hi lo ftb(协议序)
    ublob = pre2 + bytes(ut) + bytes(random.randrange(256) for _ in range(0x120))
    uoff = len(pre2)
    uout = locate(ublob, code="P0240")                 # 默认双序,不给 endian
    uhi = [r for r in uout["results"] if r["confidence"] in ("HIGH", "MEDIUM")]
    chk(len(uhi) >= 1, "UDS P0240 找到可信命中(默认双序)")
    if uhi:
        r = uhi[0]
        chk(r["endian"] == "be", "UDS 码按大端/协议序命中(平台无关)")
        chk(r["stride"] == 3 and r["kind"] == "UDS3", "识别为 3 字节 UDS 记录")
        chk(r["offset"] == uoff + uidx * 3, "UDS 命中偏移正确")
        chk(r.get("code_full") == "P0240-%02X" % ftb_240, "UDS 完整码 P0240-FTB 解出")

    # [低] KWP/原始码: --raw 直接搜 2 字节值(绕过 J2012 编码)
    rout = locate(blob, raw_value=0x0240)
    rhi = [r for r in rout["results"] if r["confidence"] in ("HIGH", "MEDIUM")]
    chk(rhi and rhi[0]["offset"] == hit_off, "KWP --raw 0x0240 定位到同一地址")
    chk(rout["code"] == "RAW 0x0240", "raw 模式标注")

    # detect_tables 概览应含覆盖真表的 stride=6 表
    tabs = detect_tables(blob, "le")
    chk(any(t["stride"] == 6 and t["start"] <= table_off
            and t["start"] + (t["count"] - 1) * 6 >= table_off + 23 * 6
            and (table_off - t["start"]) % 6 == 0 for t in tabs),
        "detect_tables 概览含覆盖真表的 stride=6 表")

    # 不存在的码
    out2 = locate(blob, code="P0333", endian="le")
    chk(all(r["confidence"] == "LOW" for r in out2["results"]) or not out2["results"],
        "不在表内的码无高可信命中")

    # 新增命名 profile 齐备
    for p in ("med17", "mevd17", "mg1", "simos", "denso_rh850", "ppc"):
        chk(p in PROFILES, "profile 含 %s" % p)

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Locate a DTC address in an ECU bin (no-A2L byte-level focus)")
    ap.add_argument("--bin", dest="binpath", default=None)
    ap.add_argument("--code", default=None, help="如 P0240 / U0100(按 J2012 编码)")
    ap.add_argument("--raw", default=None, help="直接搜 2 字节原始值(老件 KWP/厂内部编号),如 0x1234")
    ap.add_argument("--endian", choices=["le", "be", "both"], default=None,
                    help="限制码字节序;默认双序(DTC 码是协议值,与平台数据端序无关)")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="auto",
                    help="平台数据端序参考(仅信息;不限制码搜索)")
    ap.add_argument("--tables", action="store_true", help="只列出探测到的 DTC 表")
    ap.add_argument("--encode-only", action="store_true", help="只算编码,不读 bin")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())

    if a.encode_only:
        if not a.code:
            ap.error("--encode-only 需要 --code")
        v = encode_dtc(a.code)
        print("%s = 0x%04X   小端 %02X %02X   大端 %02X %02X"
              % (a.code.upper(), v, v & 0xFF, (v >> 8) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))
        print("UDS 3字节示例:", ", ".join(uds_3byte_hint(v)))
        return

    if not a.binpath:
        ap.error("需要 --bin(或用 --encode-only / --selftest)")
    buf = open(a.binpath, "rb").read()
    # 码搜索默认双序;--tables 概览用 profile 的数据端序(表探测读的是数据布局)。
    endian = a.endian or "both"
    data_endian = a.endian or PROFILES[a.profile]["endian"]

    if a.tables:
        report_tables(buf, data_endian)
        return
    if a.raw is not None:
        report(locate(buf, raw_value=int(a.raw, 0), endian=endian, profile_name=a.profile))
        return
    if not a.code:
        ap.error("需要 --code 或 --raw(或用 --tables)")
    report(locate(buf, code=a.code, endian=endian, profile_name=a.profile))


if __name__ == "__main__":
    main()

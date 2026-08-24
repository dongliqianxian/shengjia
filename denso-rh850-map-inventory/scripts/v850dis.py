#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V850E / RH850 最小反汇编器 —— 只覆盖读 DENSO 查表代码所需的子集。

设计原则：**绝不猜助记符**。不认识的扩展子操作码打印 `.ext32 <hex>`，但仍然
消耗正确的字节数，这样对齐不会崩。

自检：一个函数必须以 `jmp [lp]`（halfword 0x007F）结束。反汇编到函数尾却落不到它，
说明中途长度判断错了 —— 输出里会标出来。

用法:
    python v850dis.py <bin> <起始地址hex> [长度hex]
    python v850dis.py fw.bin 0x06590C 0x80

作模块:
    from v850dis import Dis
    d = Dis(open("fw.bin","rb").read())
    for line in d.run(0x06590C, 0x06598C, "READER A"):
        print(line)
"""
import struct
import sys

REG = ["r0", "r1", "r2", "sp", "gp", "tp"] + ["r%d" % i for i in range(6, 30)] + ["ep", "lp"]

COND = {0x0: "bv", 0x1: "bl", 0x2: "be", 0x3: "bnh", 0x4: "bn", 0x5: "br", 0x6: "blt",
        0x7: "ble", 0x8: "bnv", 0x9: "bnl", 0xA: "bne", 0xB: "bh", 0xC: "bp",
        0xD: "bsa", 0xE: "bge", 0xF: "bgt"}

OP_I = {0x00: "mov", 0x01: "not", 0x02: "divh", 0x03: "jmp", 0x04: "satsubr", 0x05: "satsub",
        0x06: "satadd", 0x07: "mulh", 0x08: "or", 0x09: "xor", 0x0A: "and", 0x0B: "tst",
        0x0C: "subr", 0x0D: "sub", 0x0E: "add", 0x0F: "cmp"}
OP_II = {0x10: "mov", 0x11: "satadd", 0x12: "add", 0x13: "cmp",
         0x14: "shr", 0x15: "sar", 0x16: "shl", 0x17: "mulh"}
OP_VI = {0x30: "addi", 0x31: "movea", 0x32: "movhi", 0x33: "satsubi",
         0x34: "ori", 0x35: "xori", 0x36: "andi", 0x37: "mulhi"}

# op6=0x3F 扩展组，键 = hw2 & 0x07FF。只放有把握的。
EXT = {0x0000: ("setf", "cc"), 0x0020: ("ldsr", "sr"), 0x0040: ("stsr", "sr"),
       0x0080: ("shr", "rr"), 0x00A0: ("sar", "rr"), 0x00C0: ("shl", "rr"),
       0x0100: ("trap", "n"), 0x0120: ("halt", "n"), 0x0140: ("reti", "n"),
       0x0160: ("ei/di", "n"), 0x0200: ("sasf", "cc"),
       0x0220: ("mul", "r3"), 0x0222: ("mulu", "r3"),
       0x0280: ("divh", "r3"), 0x0282: ("divhu", "r3"),
       0x02C0: ("div", "r3"), 0x02C2: ("divu", "r3"),
       0x02FC: ("divq", "r3"),          # RH850 扩展，操作数结构同 DIV
       0x0340: ("bsh/bsw/hsw", "r3")}


def s(v, bits):
    m = 1 << (bits - 1)
    return (v ^ m) - m


class Dis:
    def __init__(self, buf):
        self.b = buf

    def h(self, a):
        return struct.unpack_from("<H", self.b, a)[0]

    def w(self, a):
        return struct.unpack_from("<I", self.b, a)[0]

    def one(self, a):
        """返回 (字节长度, 文本)"""
        hw = self.h(a)
        r2 = (hw >> 11) & 0x1F
        op = (hw >> 5) & 0x3F
        r1 = hw & 0x1F
        R1, R2 = REG[r1], REG[r2]

        if op <= 0x0F:                                    # Format I
            if op == 0x03:
                return 2, "jmp      [%s]" % R1
            if op == 0x00 and r1 == 0 and r2 == 0:
                return 2, "nop"
            return 2, "%-8s %s, %s" % (OP_I[op], R1, R2)

        if op <= 0x17:                                    # Format II (imm5)
            m = OP_II[op]
            imm = r1 if op in (0x14, 0x15, 0x16) else s(r1, 5)
            return 2, "%-8s %d, %s" % (m, imm, R2)

        b107 = (hw >> 7) & 0x0F                           # Format III / IV
        if b107 == 0b0110:
            return 2, "sld.b    %d[ep], %s" % (hw & 0x7F, R2)
        if b107 == 0b0111:
            return 2, "sst.b    %s, %d[ep]" % (R2, hw & 0x7F)
        if b107 == 0b1000:
            return 2, "sld.h    %d[ep], %s" % ((hw & 0x7F) << 1, R2)
        if b107 == 0b1001:
            return 2, "sst.h    %s, %d[ep]" % (R2, (hw & 0x7F) << 1)
        if b107 == 0b1010:
            d = (hw & 0x7E) << 1
            return (2, "sst.w    %s, %d[ep]" % (R2, d)) if (hw & 1) else \
                   (2, "sld.w    %d[ep], %s" % (d, R2))
        if b107 == 0b1011:                                # Bcond
            d = s((((hw >> 11) & 0x1F) << 4) | (((hw >> 4) & 0x07) << 1), 9)
            return 2, "%-8s 0x%06X" % (COND[hw & 0xF], a + d)

        if 0x30 <= op <= 0x37:                            # Format VI (imm16)
            if op == 0x31 and r2 == 0:                    # V850E: MOV imm32, reg1
                return 6, "mov      0x%08X, %s" % (self.w(a + 2), R1)
            imm = self.h(a + 2)
            m = OP_VI[op]
            sh = s(imm, 16) if m in ("addi", "movea", "satsubi", "mulhi") else imm
            return 4, "%-8s 0x%04X, %s, %s   ; %d" % (m, imm, R1, R2, sh)

        if 0x38 <= op <= 0x3B:                            # Format VII (load/store disp16)
            imm = self.h(a + 2)
            d = s(imm & 0xFFFE, 16) if op in (0x39, 0x3B) else s(imm, 16)
            if op == 0x38:
                return 4, "ld.b     %d[%s], %s" % (s(imm, 16), R1, R2)
            if op == 0x39:
                return 4, "%-8s %d[%s], %s" % ("ld.w" if imm & 1 else "ld.h", d, R1, R2)
            if op == 0x3A:
                return 4, "st.b     %s, %d[%s]" % (R2, s(imm, 16), R1)
            return 4, "%-8s %s, %d[%s]" % ("st.w" if imm & 1 else "st.h", R2, d, R1)

        if op in (0x3C, 0x3D):                            # JR / JARL —— 或 LD.BU
            hw2 = self.h(a + 2)
            if hw2 & 1:
                # ★ 陷阱：hw2 bit0=1 是 LD.BU，不是 JARL（JARL 位移最低位恒 0）
                disp = s((hw2 & 0xFFFE) | ((hw >> 5) & 1), 16)
                return 4, "ld.bu    %d[%s], %s" % (disp, R1, R2)
            d = s(((hw & 0x3F) << 16) | hw2, 22)
            return (4, "jr       0x%06X" % (a + d)) if r2 == 0 else \
                   (4, "jarl     0x%06X, %s" % (a + d, R2))

        if op == 0x3E:                                    # Format VIII 位操作
            sub = ["set1", "not1", "clr1", "tst1"][(hw >> 14) & 3]
            return 4, "%-8s %d, %d[%s]" % (sub, (hw >> 11) & 7, s(self.h(a + 2), 16), R1)

        # op == 0x3F 扩展组
        hw2 = self.h(a + 2)
        if hw2 & 1:
            return 4, "ld.hu?   %d[%s], %s" % (s(hw2 & 0xFFFE, 16), R1, R2)
        key = hw2 & 0x07FF
        r3 = REG[(hw2 >> 11) & 0x1F]
        if 0x0300 <= key <= 0x031F and not (key & 1):
            return 4, "cmov     %s, %d, %s, %s" % (COND[(key - 0x0300) >> 1], s(r1, 5), R2, r3)
        if 0x0320 <= key <= 0x033F and not (key & 1):
            return 4, "cmov     %s, %s, %s, %s" % (COND[(key - 0x0320) >> 1], R1, R2, r3)
        if key in EXT:
            m, kind = EXT[key]
            if kind == "rr":
                return 4, "%-8s %s, %s" % (m, R1, R2)
            if kind == "r3":
                return 4, "%-8s %s, %s, %s" % (m, R1, R2, r3)
            if kind == "cc":
                return 4, "%-8s %s, %s" % (m, COND.get(r1, "?"), R2)
            if kind == "sr":
                return 4, "%-8s %s, sr%d" % (m, R2, r1)
            return 4, m
        return 4, ".ext32   %04X %04X" % (hw, hw2)

    def run(self, start, end, label=""):
        out = []
        if label:
            out += ["=" * 76, label, "=" * 76]
        a, saw_ret = start, False
        while a < end:
            try:
                n, txt = self.one(a)
            except Exception as e:
                out.append("0x%06X:  <解码失败 %s>" % (a, e))
                break
            raw = " ".join("%02X" % x for x in self.b[a:a + n])
            out.append("0x%06X:  %-14s %s" % (a, raw, txt))
            if txt.startswith("jmp      [lp]"):
                saw_ret = True
                out.append("           ^-- 返回（对齐自检通过）")
            a += n
        if not saw_ret and end - start > 0x40:
            out.append("           (提示: 本段未见 jmp [lp]；若这是完整函数，说明对齐可能有误)")
        return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    data = open(sys.argv[1], "rb").read()
    st = int(sys.argv[2], 0)
    ln = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x60
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(Dis(data).run(st, st + ln, "0x%06X .. 0x%06X" % (st, st + ln))))

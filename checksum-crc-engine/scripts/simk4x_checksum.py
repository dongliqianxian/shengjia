# -*- coding: utf-8 -*-
"""
Kefico SIMK4x (SIMK41 / SIMK43 / SIMK2K) 校验和 verify / repair。

平台：现代-起亚 Kefico，Infineon C166/C167 核，小端，外置 flash（AM29F400 等）。
完整性保护只有数据层 CRC-16，**没有 RSA/secure-boot**——本脚本算完即可刷。

算法（✅ 实战确认，见 ../references/known_checksum_layouts.md）：
    反射 CRC-16，反射多项式 0xA001（= 0x8005 反射），无 final xor
    等价于 crcmod.mkCrcFun(0x18005, initCrc=seed)  —— crcmod 的 rev 默认 True

  ★ 关键点，也是常规穷举扫不到它的原因：
    seed **不是** 0x0000/0xFFFF，而是 init_address 处 2 字节**字节交换**后的值，
    而那 2 字节是文件里的 **ASCII 版本字段**（如标定区 seed 0x3732 来自 "27"）。
    用固定 init 做多项式穷举 → 永远 0 命中。（2026-09-02 途胜 2.7 项目实证）

  结构：cks_address 处 [chk u16 BE][zones u8][pad u8]，其后 N 组 [start u32 LE][end u32 LE]，
        每组步进 8 字节；end 为**闭区间**（实际范围 [start, end+1)）；地址需加 bin_offset。
        多 zone 链式：zone1 用 ASCII seed，zone_n 用 zone_{n-1} 的 CRC 做 seed。
        写回：把最后一个 zone 的 CRC **高低字节交换**后大端写入 cks_address。
        zones == 0 或 0xFF → 该区未使用，跳过。

  镜像类型：靠 identification_flag_address 处是否为 ASCII "OK" 探测。
        **必须探测，不能按控制器名套固定表**（doctrine §13 逆向纪律：
        按名字查固定表会在一半的样本上自信地答错）。

算法来源：开源工具 GKFlasher <https://github.com/Dante383/GKFlasher>
        （flasher/checksum.py，GPL-3.0）。本文件为其算法的独立复刻，
        偏移表沿用其数据；沿用/分发时请遵守 GPL-3.0。

用法：
    python simk4x_checksum.py <file.bin>                # 只校验，不写
    python simk4x_checksum.py <file.bin> -w <out.bin>   # 校验并把不一致的区修正后另存
    python simk4x_checksum.py --selftest                # 校验 CRC 内核
"""
import argparse
import sys

# (类型名, identification_flag_address, [(区名, init_address, cks_address, bin_offset), ...])
CKS_TYPES = [
    ('2mbit', 0xFEFE, [
        ('Boot',        0x003FE4, 0x003EF4, 0),
        ('Calibration', 0x00800C, 0x00FEE0, -0x040000),
        ('Program',     0x010052, 0x010010, -0x040000)]),
    ('4mbit (FL2)', 0x16135, [
        ('Boot',        0x003FE4, 0x003EEC, -0x080000),
        ('MTOS',        0x00C012, 0x00C000, 0),
        ('Calibration', 0x01000C, 0x017EE0, -0x080000),
        ('Program',     0x020052, 0x020010, -0x080000)]),
    ('4mbit', 0x017EFE, [
        ('Boot',        0x003FE4, 0x003EF4, 0),
        ('MTOS',        0x00C012, 0x00C000, 0),
        ('Calibration', 0x01000C, 0x017EE0, -0x080000),
        ('Program',     0x020052, 0x020010, -0x080000)]),
    ('v6 (5WY17)', 0xDEFE, [
        ('Boot',        0x003FE4, 0x003EF4, 0),
        ('Calibration', 0x00800C, 0x00DEE0, -0x080000),
        ('Program',     0x010052, 0x010010, -0x080000)]),
    ('v6 (5WY18+)', 0xEEFE, [
        ('Boot',        0x003FE4, 0x003EF4, 0),
        ('Calibration', 0x00800C, 0x00EEE0, -0x080000),
        ('Program',     0x010052, 0x010010, -0x080000)]),
    ('8mbit', 0x97EFE, [
        ('Boot',        0x003FE4, 0x003EEC, 0),
        ('MTOS',        0x00C012, 0x00C000, 0),
        ('RSW',         0x082014, 0x082000, 0),
        ('Calibration', 0x09000C, 0x097EE0, 0),
        ('Program',     0x0A0052, 0x0A0010, 0)]),
    ('SIMK2K (Elantra 2.0 Beta)', 0x01401E, [
        ('Calibration', 0x01200C, 0x014000, -0x080000),
        ('Program',     0x020052, 0x020010, -0x080000)]),
]


def crc16(data, init):
    """反射 CRC-16 / poly 0xA001 / 无 final xor == crcmod.mkCrcFun(0x18005, initCrc=init)."""
    c = init
    for x in data:
        c ^= x
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c


def _rev(p, start, length):
    b = list(p[start:start + length])
    b.reverse()
    return b


def _cat3(b):
    return ((b[0] << 8 | b[1]) << 8 | b[2])


def detect(payload):
    """按 "OK" 标志探测镜像类型。MTOS 区不存在时剔除。返回 (类型名, regions) 或 (None, None)。"""
    for name, flag, regions in CKS_TYPES:
        if flag + 2 > len(payload) or payload[flag:flag + 2] != b'OK':
            continue
        regs = [r for r in regions
                if r[0] != 'MTOS' or payload[r[1]:r[1] + 2] == b'MT']
        return name, regs
    return None, None


def compute(payload):
    """返回 (类型名, [Region, ...])。Region = dict(name, cks_address, stored, computed, zones, segments)。
    computed 为 None 表示该区未使用（zones 0/0xFF）。"""
    name, regions = detect(payload)
    if name is None:
        raise ValueError('未识别镜像类型：所有 identification flag 处均无 "OK"。'
                         '可能不是 SIMK4x，或是新变体 —— 按 doctrine §13 判 UNCONFIRMED，不要硬套。')
    out = []
    for rname, init_a, cks_a, off in regions:
        zones = payload[cks_a + 2]
        stored = int.from_bytes(payload[cks_a:cks_a + 2], 'big')
        rec = {'name': rname, 'cks_address': cks_a, 'init_address': init_a,
               'bin_offset': off, 'zones': zones, 'stored': stored,
               'computed': None, 'segments': []}
        if zones in (0, 0xFF):
            out.append(rec)
            continue
        cks, za = [], cks_a
        for i in range(zones):
            zs = _cat3(_rev(payload, za + 4, 3)) + off
            ze = _cat3(_rev(payload, za + 8, 3)) + off + 1
            if i == 0:
                ib = _rev(payload, init_a, 2)
                seed = (ib[0] << 8) | ib[1]
            else:
                seed = cks[i - 1]
            v = crc16(payload[zs:ze], seed)
            rec['segments'].append((zs, ze, seed, v))
            cks.append(v)
            za += 8
        rec['computed'] = ((cks[-1] & 0xFF) << 8) | (cks[-1] >> 8)   # 存储时高低字节交换
        out.append(rec)
    return name, out


def repair(payload):
    """就地修正所有不一致的区，返回被修正的 [(区名, cks_address, 旧值, 新值), ...]。"""
    _, regions = compute(payload)
    fixed = []
    for r in regions:
        if r['computed'] is None or r['stored'] == r['computed']:
            continue
        payload[r['cks_address']:r['cks_address'] + 2] = r['computed'].to_bytes(2, 'big')
        fixed.append((r['name'], r['cks_address'], r['stored'], r['computed']))
    return fixed


def _selftest():
    # CRC-16/ARC 标准检查值：poly 0x8005 反射, init 0, 无 xorout, "123456789" -> 0xBB3D
    v = crc16(b'123456789', 0x0000)
    ok = (v == 0xBB3D)
    print(f'[{"OK" if ok else "FAIL"}] CRC 内核: crc16("123456789", init=0) = {v:#06x} (期望 0xbb3d)')
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='Kefico SIMK4x 校验和 verify / repair')
    ap.add_argument('file', nargs='?', help='待校验的 bin/ori/mod')
    ap.add_argument('-w', '--write', metavar='OUT', help='修正后另存到此路径（不给则只校验）')
    ap.add_argument('--selftest', action='store_true', help='校验 CRC 内核后退出')
    a = ap.parse_args()

    if a.selftest:
        sys.exit(_selftest())
    if not a.file:
        ap.error('需要给出待校验文件（或用 --selftest）')

    payload = bytearray(open(a.file, 'rb').read())
    try:
        name, regions = compute(payload)
    except ValueError as e:
        print(f'[!] {e}')
        sys.exit(1)

    print(f'[*] 镜像类型: {name}   大小 {len(payload)} B')
    bad = []
    for r in regions:
        if r['computed'] is None:
            print(f'  [{r["name"]:11s}] zones={r["zones"]:#04x} -> 跳过（未使用）')
            continue
        for i, (zs, ze, seed, v) in enumerate(r['segments']):
            print(f'  [{r["name"]:11s}] zone{i + 1}: 0x{zs:06X}-0x{ze - 1:06X} '
                  f'seed=0x{seed:04X} -> crc=0x{v:04X}')
        ok = r['stored'] == r['computed']
        print(f'  [{r["name"]:11s}] @0x{r["cks_address"]:06X} 存储=0x{r["stored"]:04X} '
              f'计算=0x{r["computed"]:04X}  {"OK" if ok else "MISMATCH"}')
        if not ok:
            bad.append(r)

    if not bad:
        print('[*] 全部一致，无需修正。')
        return
    if not a.write:
        print(f'[!] {len(bad)} 个区不一致，未写入。加 -w <输出路径> 以修正。')
        sys.exit(2)

    for n, addr, old, new in repair(payload):
        print(f'[+] 修正 [{n}] @0x{addr:06X}: {old:#06x} -> {new:#06x}')
    open(a.write, 'wb').write(bytes(payload))
    print(f'[*] 已写入 {a.write}')

    # 交付纪律：回读输出文件重算，不信内存里的结果
    _, again = compute(bytearray(open(a.write, 'rb').read()))
    lines = [f'{r["name"]}={"OK" if r["computed"] is None or r["stored"] == r["computed"] else "FAIL"}'
             for r in again]
    print('[*] 回读复核: ' + ', '.join(lines))


if __name__ == '__main__':
    main()

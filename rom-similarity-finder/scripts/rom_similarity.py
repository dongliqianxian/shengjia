#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rom_similarity.py — 给一个陌生 bin,在本地标定库里找出【字节级相似】的文件。

rom-similarity-finder 技能的执行引擎。解决的问题:
    手上一个不认识的 ROM -> 我库里几万个 bin/ori/mod 里有没有跟它像的?
    有 -> 借现成定义(A2L/DAMOS/OLS 工程/已知表地址),不用从零盲扫。
    没有 -> 才轮到 blind-map-finder。

算法: 单排列 MinHash(one-permutation hashing) + 分桶,跑在字节级 Karp-Rabin 滚动哈希 shingle 上。

  1. W=64 字节窗口滑过【每一个字节位置】,滚动哈希 O(1) 更新 -> 全文件 O(N)。
  2. 每个 shingle 哈希经 splitmix64 雪崩混合,bucket = h mod K (K=128),桶内只留【最小】值。
  3. 指纹 = K 个桶最小值 (K x uint64 = 1KB/文件) + distinct 基数(KMV 估计,误差 ±1.6%)。
  4. Jaccard = 两指纹中【同桶且相等】的占比(只统计双方都非空的桶)。
  5. Containment = |A∩B| / min(|A|,|B|),由 Jaccard 与两边基数反解。

【为什么必须每个字节位置都开窗 —— 这是本算法唯一不可替代的性质】
  同一份 ROM,裸 .bin 与从 .ols 里抽出来的(带容器头)文件【偏移不同】。
  shingle 在每个字节位置都取,所以两边窗口内容仍然重叠 -> 与对齐无关。
  固定步长开窗(4KB/256B)做不到: 几十字节的偏移就能让匹配率掉到零。

【为什么要 containment,不能只看 Jaccard】
  小 ROM 完整包含在大 dump 里(bench 读全片 flash vs 只有标定区的文件)时,
  Jaccard 会被尺寸差异拉低(≈|A|/|B|),但实际是完全匹配。containment 专治这种。

诚实边界(doctrine §13 逆向纪律):
  * MinHash 是【粗筛】,标准误约 8%(K=128)。**它给的是候选,不是结论。**
  * top-N 必须走第二阶段 `verify`: 重新打开、按字节搜索对齐、在重叠区做【精确字节比对】,
    才能给出 "% 相同" 这种可写进结论的数字。筛选与确认分开 —— 同 blind-map-finder 的纪律。
  * 相似 ≠ 定义可直接套用。软件版本不同,同一地址的表可能已挪位/改维度。
    借来的定义仍须经解码自检 + ecu-map-analysis 判功能。
  * **压缩/加密容器(.ols 工程内部、加密 .kp)字节级不可比** —— 必须先用
    winols-ols-parser 抽出内部 ROM 再索引。直接索引 .ols 会得到无意义的低分。

用法:
    python rom_similarity.py index --lib E:/ECU_Tuning_Projects        # 建索引
    python rom_similarity.py index --lib DIR --ext bin,ori,mod,rom     # 指定后缀
    python rom_similarity.py query --bin UNKNOWN.bin --top 20          # 粗筛
    python rom_similarity.py query --bin UNKNOWN.bin --verify 5        # 粗筛+前5名精确确认
    python rom_similarity.py verify --a A.bin --b B.bin                # 只做精确比对
    python rom_similarity.py stats                                     # 看索引状态
    python rom_similarity.py --selftest
"""
import argparse
import io
import os
import sqlite3
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import numpy as np
except ImportError:
    print("!! 需要 numpy:  pip install numpy")
    sys.exit(2)

# ---------------------------------------------------------------- 参数
W = 64                      # shingle 窗口(字节)
K = 128                     # MinHash 桶数
MASK64 = (1 << 64) - 1
MAXU = np.uint64(MASK64)
BASE = 0x100000001B3        # FNV-1a prime,奇数 -> mod 2^64 可逆
MIN_FILE = 4096             # 小于这个不索引(不够开窗)
DEFAULT_EXT = "bin,ori,mod,rom,ecu,ori1,mod1"
DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".claude", "rom_index.sqlite")


# ---------------------------------------------------------------- 哈希核心
def _splitmix64(z):
    """uint64 向量雪崩混合。滚动哈希 mod 2^64 的低位很弱,直接拿去分桶会严重偏斜。"""
    z = z.copy()
    z ^= z >> np.uint64(30)
    z *= np.uint64(0xBF58476D1CE4E5B9)
    z ^= z >> np.uint64(27)
    z *= np.uint64(0x94D049BB133111EB)
    z ^= z >> np.uint64(31)
    return z


def shingle_hashes(data, w=W):
    """
    全部 shingle 的混合哈希,O(N) 无 Python 循环。

    h[i] = Σ_{j<w} b[i+j]·B^(w-1-j)
         = B^(w-1+i) · (C[i+w] − C[i])      其中 C[m] = Σ_{k<m} b[k]·B^(−k)
    全部 mod 2^64(uint64 自然回绕)。B 为奇数故在 mod 2^64 下可逆。
    """
    n = len(data)
    if n < w:
        return np.empty(0, np.uint64)
    b = np.frombuffer(data, dtype=np.uint8).astype(np.uint64)

    binv = np.uint64(pow(BASE, -1, 1 << 64))
    bb = np.uint64(BASE)

    with np.errstate(over="ignore"):
        a = np.full(n, binv, np.uint64); a[0] = np.uint64(1)
        invp = np.multiply.accumulate(a)                 # invp[k] = B^(−k)
        a = np.full(n, bb, np.uint64); a[0] = np.uint64(1)
        fwdp = np.multiply.accumulate(a)                 # fwdp[i] = B^(i)

        C = np.zeros(n + 1, np.uint64)
        np.cumsum(b * invp, out=C[1:])                   # C[m] = Σ_{k<m} b[k]·B^(−k)

        ns = n - w + 1
        cw = np.uint64(pow(BASE, w - 1, 1 << 64))
        h = cw * fwdp[:ns] * (C[w:w + ns] - C[:ns])
    return _splitmix64(h)


KMV_K = 4096            # 基数估计取第 k 小,相对误差 ≈ 1/sqrt(k) ≈ 1.6%


def _cardinality(h, kk=KMV_K):
    """
    distinct shingle 数。用 KMV(k 最小值)估计: n ≈ k·2^64 / (第 k 小的哈希)。

    为什么不精确数: 精确 distinct 要对全部 N 个 uint64 排序,4MB 文件耗时 1.0s,
    占总时间 56%。np.partition 是 O(N),同样文件 0.026s,实测误差 ±1.6% ——
    远小于 MinHash 自身 K=128 带来的 ≈8% 标准误,不构成新的误差来源。

    注意不能用"每桶最小值取倒数再求和"那种估计: E[1/X] ≠ 1/E[X],
    单样本倒数偏差可达数千倍(实测 +11375%)。必须取全局第 k 小。
    """
    if h.size == 0:
        return 0
    if h.size <= kk * 2:                                 # 小数组直接精确算,便宜
        return int(np.unique(h).size)
    part = np.partition(h, kk)
    # 退化检测: KMV 假设取到的是【互不相同】的值。填充区(大片 0xFF/0x00)会让同一个哈希
    # 重复几十万次,此时第 k 小仍是那个值,公式会给出荒谬结果。前 k+1 个里 distinct 太少
    # 就说明重复占主导,退回精确计数(这类文件本身 distinct 就少,unique 也不贵)。
    if np.unique(part[:kk + 1]).size < kk // 2:
        return int(np.unique(h).size)
    mk = float(part[kk])
    if mk <= 0:
        return int(np.unique(h).size)
    return int(kk * (1 << 64) / mk)


def fingerprint(data, w=W, k=K):
    """-> (mins[k] uint64, n_distinct)。空桶为 MAXU。"""
    h = shingle_hashes(data, w)
    mins = np.full(k, MAXU, np.uint64)
    if h.size == 0:
        return mins, 0
    # 桶号用 uint8(k<=256) -> numpy 走基数排序,O(N);对 uint64 全排序要 O(N log N),慢 17 倍
    bs8 = (h % np.uint64(k)).astype(np.uint8)
    order = np.argsort(bs8, kind="stable")
    hs, bs = h[order], bs8[order]
    cnt = np.bincount(bs, minlength=k)
    starts = np.concatenate(([0], np.cumsum(cnt)[:-1]))
    valid = cnt > 0
    if np.any(valid):
        mins[valid] = np.minimum.reduceat(hs, starts[valid])
    return mins, _cardinality(h)


# ---------------------------------------------------------------- 比较
def compare(fa, na, fb, nb):
    """-> dict(jaccard, containment, inter_est, score)"""
    both = (fa != MAXU) & (fb != MAXU)
    nboth = int(np.count_nonzero(both))
    if nboth == 0:
        return dict(jaccard=0.0, containment=0.0, inter_est=0.0, score=0.0, buckets=0)
    match = int(np.count_nonzero((fa == fb) & both))
    j = match / nboth

    # |A∩B| ≈ J·|A∪B| = J·(|A|+|B|)/(1+J)
    inter = j * (na + nb) / (1.0 + j) if j > 0 else 0.0
    small = min(na, nb)
    c = min(1.0, inter / small) if small else 0.0
    return dict(jaccard=j, containment=c, inter_est=inter,
                score=max(j, c), buckets=nboth)


# ---------------------------------------------------------------- 第二阶段:精确确认
def _anchor(buf, size=4096):
    """从 buf 里挑一段【字节多样性高】的锚点,避开 0xFF/0x00 填充区。"""
    n = len(buf)
    if n <= size:
        return 0, buf
    best, best_at = -1, 0
    step = max(1, (n - size) // 64)
    for off in range(0, n - size + 1, step):
        seg = buf[off:off + size]
        div = len(set(seg[::16]))                        # 抽样估多样性,够用且快
        if div > best:
            best, best_at = div, off
        if div >= 200:
            break
    return best_at, buf[best_at:best_at + size]


def verify_pair(a, b, max_probe=64):
    """
    精确确认: 在 b 里搜 a 的锚点 -> 定出偏移 -> 重叠区逐字节比对。
    -> dict(offset, overlap, equal, ratio, aligned)
    """
    if len(a) > len(b):
        r = verify_pair(b, a, max_probe)
        r["swapped"] = True
        return r
    at, anc = _anchor(a)
    best = dict(offset=None, overlap=0, equal=0, ratio=0.0,
                aligned=False, swapped=False)
    start, probes = 0, 0
    while probes < max_probe:
        p = b.find(anc, start)
        if p < 0:
            break
        probes += 1
        start = p + 1
        delta = p - at                                   # b 中对应 a[0] 的位置
        lo = max(0, -delta)
        hi = min(len(a), len(b) - delta)
        if hi <= lo:
            continue
        seg_a = np.frombuffer(a[lo:hi], np.uint8)
        seg_b = np.frombuffer(b[lo + delta:hi + delta], np.uint8)
        eq = int(np.count_nonzero(seg_a == seg_b))
        ov = hi - lo
        if eq > best["equal"]:
            best = dict(offset=delta, overlap=ov, equal=eq,
                        ratio=eq / ov if ov else 0.0, aligned=True, swapped=False)
    if not best["aligned"]:                              # 锚点搜不到 -> 退化为零偏移比对
        ov = min(len(a), len(b))
        if ov:
            sa = np.frombuffer(a[:ov], np.uint8)
            sb = np.frombuffer(b[:ov], np.uint8)
            eq = int(np.count_nonzero(sa == sb))
            best = dict(offset=0, overlap=ov, equal=eq, ratio=eq / ov,
                        aligned=False, swapped=False)
    return best


# ---------------------------------------------------------------- 索引库
def entropy(data, sample=1 << 20):
    """字节熵(bit/byte)。用于识别【加密/压缩】文件 —— 那类文件字节级不可比。

    判据: 接近 8.0 = 已加密或压缩;正常 ECU 固件通常 5.5-7.0。
    实测: EcuTek Stock ROM 全库 7.79-7.91(全部加密,文件名不带 -enc 的也是)。
    """
    if not data:
        return 0.0
    d = np.frombuffer(data[:sample], np.uint8)
    c = np.bincount(d, minlength=256).astype(np.float64)
    c = c[c > 0] / d.size
    return float(-(c * np.log2(c)).sum())


SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime REAL,
  n_distinct INTEGER, w INTEGER, k INTEGER, fp BLOB, indexed_at REAL,
  entropy REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_size ON files(size);
"""


def _db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(files)")}
    if "entropy" not in cols:                             # 旧库平滑升级
        c.execute("ALTER TABLE files ADD COLUMN entropy REAL DEFAULT 0")
        c.commit()
    return c


def cmd_index(a):
    exts = {"." + e.strip().lower().lstrip(".") for e in a.ext.split(",") if e.strip()}
    con = _db(a.db)
    cur = con.cursor()
    have = {r[0]: (r[1], r[2]) for r in cur.execute("SELECT path,size,mtime FROM files")}

    todo = []
    for root, _, names in os.walk(a.lib):
        for nm in names:
            if os.path.splitext(nm)[1].lower() not in exts:
                continue
            p = os.path.join(root, nm)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size < MIN_FILE or (a.max_mb and st.st_size > a.max_mb * 1048576):
                continue
            old = have.get(p)
            if old and abs(old[1] - st.st_mtime) < 1e-6 and old[0] == st.st_size:
                continue                                  # 未变,跳过
            todo.append((p, st.st_size, st.st_mtime))

    print("待索引: %d 个文件 (库中已有 %d)" % (len(todo), len(have)))
    t0, done, batch, hi_ent = time.time(), 0, [], 0
    for p, sz, mt in todo:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            print("  跳过(读失败) %s: %s" % (p, e)); continue
        fp, nd = fingerprint(data, a.w, a.k)
        ent = entropy(data)
        if ent >= 7.5:
            hi_ent += 1
        batch.append((p, sz, mt, nd, a.w, a.k, fp.tobytes(), time.time(), ent))
        done += 1
        if len(batch) >= 50:
            cur.executemany("INSERT OR REPLACE INTO files"
                            "(path,size,mtime,n_distinct,w,k,fp,indexed_at,entropy)"
                            " VALUES(?,?,?,?,?,?,?,?,?)", batch)
            con.commit(); batch = []
            print("  %d/%d  %.1f 文件/秒" % (done, len(todo), done / max(1e-9, time.time() - t0)))
    if batch:
        cur.executemany("INSERT OR REPLACE INTO files"
                        "(path,size,mtime,n_distinct,w,k,fp,indexed_at,entropy)"
                        " VALUES(?,?,?,?,?,?,?,?,?)", batch)
        con.commit()
    el = time.time() - t0
    print("完成: %d 个,用时 %.1fs (%.1f 文件/秒)" % (done, el, done / el if el > 0 else 0))
    if done and hi_ent / done > 0.5:
        print()
        print("  !! 警告: %d/%d (%.0f%%) 的文件字节熵 >= 7.5 —— 疑似【已加密或压缩】。"
              % (hi_ent, done, 100 * hi_ent / done))
        print("     加密文件之间【跨密钥不可比】,查询很可能返回零候选,那不是算法失效。")
        print("     实测: EcuTek Stock ROM 全库熵 7.79-7.91,同族同密钥内部仍可比(相似度 0.9+),")
        print("     但跨车型/跨版本可能因换密钥而完全测不出相似。")
        print("     .ols/.kp 一律先经 winols-ols-parser 抽出内部 ROM 再索引。")
    con.close()
    return 0


def cmd_query(a):
    if not os.path.isfile(a.binpath):
        print("!! 找不到文件: %s" % a.binpath); return 2
    with open(a.binpath, "rb") as f:
        data = f.read()
    con = _db(a.db)
    rows = list(con.execute("SELECT path,size,n_distinct,w,k,fp FROM files"))
    if not rows:
        print("!! 索引为空,先跑: python rom_similarity.py index --lib <目录>"); return 2

    ws = {r[3] for r in rows}
    ks = {r[4] for r in rows}
    if len(ws) > 1 or len(ks) > 1:
        print("!! 索引里混有不同 W/K 参数 %s/%s,请用同一套参数重建" % (ws, ks)); return 2
    w, k = rows[0][3], rows[0][4]
    fq, nq = fingerprint(data, w, k)

    res = []
    for path, size, nd, _w, _k, blob in rows:
        fb = np.frombuffer(blob, np.uint64)
        if fb.size != k:
            continue
        if os.path.abspath(path) == os.path.abspath(a.binpath):
            continue
        r = compare(fq, nq, fb, nd)
        r.update(path=path, size=size)
        res.append(r)
    res.sort(key=lambda x: -x["score"])
    res = [r for r in res if r["score"] >= a.min_score][:a.top]

    print("=" * 100)
    print("粗筛结果 —— 目标 %s (%d B, %d distinct shingles)"
          % (os.path.basename(a.binpath), len(data), nq))
    print("MinHash K=%d W=%d,标准误约 %.0f%% —— 这是候选,不是结论" % (k, w, 100 / (k ** 0.5)))
    e = entropy(data)
    if e >= 7.5:
        print("!! 目标文件熵 %.3f (>=7.5) —— 疑似已加密/压缩。" % e)
        print("   同密钥同族内部仍可比;跨密钥则本方法失效,零候选不代表库里真没有同源文件。")
    print("=" * 100)
    if not res:
        print("  无候选(score >= %.2f)。库里没有相似文件 -> 转 blind-map-finder 盲扫。" % a.min_score)
        con.close(); return 0
    print("%-5s %8s %8s %10s  %s" % ("#", "Jaccard", "含入度", "大小", "文件"))
    for i, r in enumerate(res, 1):
        print("%-5d %7.3f %8.3f %9dK  %s"
              % (i, r["jaccard"], r["containment"], r["size"] // 1024, r["path"]))

    if a.verify:
        print()
        print("=" * 100)
        print("第二阶段:精确字节比对(前 %d 名)" % min(a.verify, len(res)))
        print("=" * 100)
        for i, r in enumerate(res[:a.verify], 1):
            try:
                with open(r["path"], "rb") as f:
                    other = f.read()
            except OSError as e:
                print("%-3d 读失败: %s" % (i, e)); continue
            v = verify_pair(data, other)
            tag = "对齐 offset=%+d" % v["offset"] if v["aligned"] else "未找到锚点(按 0 偏移)"
            print("%-3d %6.2f%% 相同  重叠 %dK  %s"
                  % (i, 100 * v["ratio"], v["overlap"] // 1024, tag))
            print("      %s" % r["path"])
    con.close()
    return 0


def cmd_verify(a):
    with open(a.a, "rb") as f:
        A = f.read()
    with open(a.b, "rb") as f:
        B = f.read()
    v = verify_pair(A, B)
    print("A: %s (%d B)" % (a.a, len(A)))
    print("B: %s (%d B)" % (a.b, len(B)))
    print("对齐: %s   offset=%s" % ("是" if v["aligned"] else "否(锚点未命中)", v["offset"]))
    print("重叠: %d B    相同: %d B    比例: %.4f%%" % (v["overlap"], v["equal"], 100 * v["ratio"]))
    fa, na = fingerprint(A)
    fb, nb = fingerprint(B)
    c = compare(fa, na, fb, nb)
    print("MinHash 粗筛对照: Jaccard=%.3f  含入度=%.3f" % (c["jaccard"], c["containment"]))
    return 0


def cmd_stats(a):
    con = _db(a.db)
    n, tot = con.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files").fetchone()
    print("索引库: %s" % a.db)
    print("文件数: %d    总字节: %.2f GB" % (n, tot / 1073741824))
    if n:
        for w, k, c in con.execute("SELECT w,k,COUNT(*) FROM files GROUP BY w,k"):
            print("  W=%d K=%d : %d 个" % (w, k, c))
    con.close()
    return 0


# ---------------------------------------------------------------- 自测
def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    rng = np.random.default_rng(1234)

    # --- 1. 滚动哈希 == 朴素定义 ---
    raw = rng.integers(0, 256, 300, dtype=np.uint8).tobytes()
    fast = shingle_hashes(raw, 16)
    naive = []
    for i in range(len(raw) - 16 + 1):
        h = 0
        for j in range(16):
            h = (h * BASE + raw[i + j]) & MASK64
        naive.append(h)
    naive = _splitmix64(np.array(naive, np.uint64))
    chk(np.array_equal(fast, naive), "滚动哈希与朴素定义逐个相等 (%d 个 shingle)" % len(naive))

    # --- 2. 相同文件 ---
    big = rng.integers(0, 256, 400_000, dtype=np.uint8).tobytes()
    f1, n1 = fingerprint(big)
    f2, n2 = fingerprint(big)
    c = compare(f1, n1, f2, n2)
    chk(c["jaccard"] == 1.0, "相同文件 Jaccard = 1.000")

    # --- 3. 偏移不变性(本算法存在的理由) ---
    for off in (1, 76, 512, 4096):
        shifted = bytes(rng.integers(0, 256, off, dtype=np.uint8)) + big
        fs, ns = fingerprint(shifted)
        cs = compare(f1, n1, fs, ns)
        chk(cs["containment"] > 0.95,
            "偏移 %-5d 字节后 含入度 %.3f > 0.95 (固定步长开窗会掉到 ~0)" % (off, cs["containment"]))

    # --- 4. 小 ROM 包含在大 dump 里 ---
    small = big[100_000:180_000]
    fsm, nsm = fingerprint(small)
    cc = compare(fsm, nsm, f1, n1)
    chk(cc["containment"] > 0.90, "小 ROM 含入大 dump: 含入度 %.3f > 0.90" % cc["containment"])
    chk(cc["jaccard"] < 0.50, "  同一对的 Jaccard 仅 %.3f —— 只看 Jaccard 会漏掉它" % cc["jaccard"])

    # --- 5. 无关文件 ---
    other = rng.integers(0, 256, 400_000, dtype=np.uint8).tobytes()
    fo, no = fingerprint(other)
    cu = compare(f1, n1, fo, no)
    chk(cu["score"] < 0.05, "无关文件 score %.4f < 0.05" % cu["score"])

    # --- 6. 局部改动(改 2% 字节,模拟同软件版本的调校差异) ---
    arr = np.frombuffer(big, np.uint8).copy()
    idx = rng.choice(arr.size, arr.size // 50, replace=False)
    arr[idx] = rng.integers(0, 256, idx.size, dtype=np.uint8)
    fm, nm = fingerprint(arr.tobytes())
    cm = compare(f1, n1, fm, nm)
    chk(0.05 < cm["jaccard"] < 0.95,
        "改 2%% 字节 -> Jaccard %.3f 落在中间带(可区分于相同/无关)" % cm["jaccard"])

    # --- 7. distinct 计数正确性(填充区应塌缩) ---
    pad = b"\xff" * 100_000
    _, npad = fingerprint(pad)
    chk(npad == 1, "全 0xFF 填充 -> distinct shingle = %d (应为 1)" % npad)

    # --- 8. 第二阶段精确比对 ---
    v = verify_pair(big, bytes(rng.integers(0, 256, 76, dtype=np.uint8)) + big)
    chk(v["aligned"] and v["offset"] == 76 and v["ratio"] > 0.999,
        "精确比对定出 offset=%s ratio=%.4f" % (v["offset"], v["ratio"]))
    v2 = verify_pair(small, big)
    chk(v2["aligned"] and v2["ratio"] > 0.999,
        "小块在大文件中定位: offset=%s ratio=%.4f" % (v2["offset"], v2["ratio"]))

    # --- 9. 空桶处理 ---
    tiny = bytes(rng.integers(0, 256, 200, dtype=np.uint8))
    ft, nt = fingerprint(tiny)
    ct = compare(ft, nt, ft, nt)
    chk(ct["jaccard"] == 1.0 and ct["buckets"] <= K,
        "小文件(%d 桶非空)自比 Jaccard = 1.0" % ct["buckets"])

    # --- 10. 端到端: 索引 + 查询 ---
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        lib = os.path.join(tmp, "lib"); os.makedirs(lib)
        open(os.path.join(lib, "same.bin"), "wb").write(big)
        open(os.path.join(lib, "shift.bin"), "wb").write(b"\x00" * 76 + big)
        open(os.path.join(lib, "junk.bin"), "wb").write(other)
        tgt = os.path.join(tmp, "target.bin"); open(tgt, "wb").write(big)
        db = os.path.join(tmp, "idx.sqlite")

        ns = argparse.Namespace(lib=lib, db=db, ext=DEFAULT_EXT, w=W, k=K, max_mb=0)
        cmd_index(ns)
        con = _db(db)
        cnt = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        con.close()
        chk(cnt == 3, "索引建立: %d 个文件" % cnt)

        con = _db(db)
        rows = list(con.execute("SELECT path,n_distinct,fp FROM files"))
        con.close()
        fq, nq = fingerprint(big)
        scored = []
        for p, nd, blob in rows:
            r = compare(fq, nq, np.frombuffer(blob, np.uint64), nd)
            scored.append((r["score"], os.path.basename(p)))
        scored.sort(reverse=True)
        chk(scored[0][1] in ("same.bin", "shift.bin") and scored[-1][1] == "junk.bin",
            "排序正确: 头名=%s 末名=%s" % (scored[0][1], scored[-1][1]))
        chk(scored[-1][0] < 0.05, "无关文件排最后且分数 %.4f 很低" % scored[-1][0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nselftest: " + ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="ROM 字节级相似度检索(MinHash 粗筛 + 精确确认)")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    pi = sub.add_parser("index", help="扫描目录建指纹索引")
    pi.add_argument("--lib", required=True)
    pi.add_argument("--db", default=DEFAULT_DB)
    pi.add_argument("--ext", default=DEFAULT_EXT)
    pi.add_argument("--w", type=int, default=W)
    pi.add_argument("--k", type=int, default=K)
    pi.add_argument("--max-mb", type=int, default=64, help="跳过超过此大小的文件(0=不限)")

    pq = sub.add_parser("query", help="查库找相似文件")
    pq.add_argument("--bin", dest="binpath", required=True)
    pq.add_argument("--db", default=DEFAULT_DB)
    pq.add_argument("--top", type=int, default=20)
    pq.add_argument("--min-score", type=float, default=0.02)
    pq.add_argument("--verify", type=int, default=0, help="对前 N 名做精确字节比对")

    pv = sub.add_parser("verify", help="两个文件精确字节比对")
    pv.add_argument("--a", required=True)
    pv.add_argument("--b", required=True)

    ps = sub.add_parser("stats", help="索引状态")
    ps.add_argument("--db", default=DEFAULT_DB)

    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "index":
        return cmd_index(a)
    if a.cmd == "query":
        return cmd_query(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "stats":
        return cmd_stats(a)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

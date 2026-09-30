#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ECM Titanium's local catalogue, read-only: categories, map IDs, your project history, and
which driver a bin matches.

How ECM finds a driver (from the DRIVERS schema): each row carries a byte window
[START_MD5, END_MD5) and the MD5 of that window's content. A file matches a driver when the
same window of the file hashes to the same MD5 - an exact content hash, not a file name, not a
size, not a similarity score. So a driver is locked to one software version, which is why its
addresses can be raw file offsets.
The database is opened with mode=ro; nothing is ever written.

  python ecm_db.py --cats
  python ecm_db.py --lookup "Limiter of Maximum torque"
  python ecm_db.py --projects BMW
  python ecm_db.py --match-bin ORI.bin [--brand BMW]
"""
import argparse
import collections
import hashlib
import os
import sqlite3
import sys
import tempfile
import time

DEFAULT_DB = r"C:\ECMTitaniumLauncher3.0\ECMBackup\ecm30.db"


def connect(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.text_factory = lambda z: z.decode('utf-8', 'replace')
    return con


def match_bin(con, data, brand=None):
    q = ("select NOMEFILE, START_MD5, END_MD5, OFFSET_MD5, upper(MD5) from DRIVERS "
         "where END_MD5 <= ? and END_MD5 > START_MD5")
    args = [len(data)]
    if brand:
        q += " and upper(MARCA) = ?"
        args.append(brand.upper())
    idx = collections.defaultdict(lambda: collections.defaultdict(list))
    rows = skipped = 0
    for nm, s, e, off, md5 in con.execute(q, args):
        rows += 1
        if off:                         # semantics of a non-zero OFFSET_MD5 not verified - skip, say so
            skipped += 1
            continue
        idx[(s, e)][md5].append(nm)
    t0, hits = time.time(), []
    for (s, e), m in idx.items():
        h = hashlib.md5(data[s:e]).hexdigest().upper()
        for nm in m.get(h, []):
            hits.append((nm, s, e, h))
    return hits, rows, len(idx), skipped, time.time() - t0


def driver_info(con, nomefile):
    cur = con.execute("select * from DRIVERS where NOMEFILE = ?", (nomefile,))
    cols = [d[0] for d in cur.description]
    r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--cats', action='store_true')
    ap.add_argument('--lookup')
    ap.add_argument('--projects')
    ap.add_argument('--match-bin')
    ap.add_argument('--brand')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    con = connect(a.db)
    if a.cats:
        for i, d in con.execute("select CAT_ID, CAT_DESC from MAPS_CATEG where CAT_LANG='ENU' order by CAT_ID"):
            print(f"  {i:3d}  {d}")
    if a.lookup:
        for r in con.execute("select MAP_ID, MAP_DESC, MAP_TYPE, MAP_PARENT from MAPS_TABLE where MAP_LANG='ENU' "
                             "and MAP_DESC like ? order by MAP_DESC limit 60", (f"%{a.lookup}%",)):
            print("  %-18s %-60s cat=%-3s parent=%s" % r)
    if a.projects:
        for r in con.execute("select PROJECT_ID, PROJECT_NAME, DRV_NAME, ORI_MD5, LAST_MOD_DATE from PROJECTS "
                             "where PROJECT_NAME like ? order by LAST_MOD_DATE desc limit 40", (f"%{a.projects}%",)):
            print("  %5d  %-70s drv=%-9s %s  %s" % r)
    if a.match_bin:
        data = open(a.match_bin, 'rb').read()
        full = hashlib.md5(data).hexdigest().upper()
        seen = list(con.execute("select PROJECT_ID, PROJECT_NAME, DRV_NAME from PROJECTS where upper(ORI_MD5) = ?", (full,)))
        print(f"file {len(data):,} B  full-file MD5 {full}")
        print(f"  opened in ECM before (PROJECTS): {len(seen)}" + ''.join(f"\n    #{p} {n}  driver {d}" for p, n, d in seen))
        hits, rows, nwin, skipped, dt = match_bin(con, data, a.brand)
        print(f"  driver rows considered {rows:,}  distinct windows hashed {nwin:,} in {dt:.1f}s"
              f"  (skipped {skipped} rows with non-zero OFFSET_MD5)")
        print(f"  MATCHES: {len(hits)}")
        for nm, s, e, h in hits[:20]:
            d = driver_info(con, nm) or {}
            print(f"    Z{nm}.DRT  window 0x{s:X}-0x{e:X}  {d.get('MARCA')} {d.get('MODELLO')} | {d.get('MOTORE')} "
                  f"{d.get('CAVALLI')} hp | {d.get('DESCRIZIONE')} | eprom {d.get('T_EPROM')} cks {d.get('CKS')}/{d.get('CKS_FAM')}")
        if not hits:
            print("    none - this catalogue snapshot has no driver whose window hashes like this file.\n"
                  "    That does NOT prove ECM has no driver: the local DB may be an older / partial snapshot.")
    return 0


def selftest():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, 't.db')
    data = bytes(range(256)) * 64
    good = hashlib.md5(data[0x100:0x900]).hexdigest().upper()
    con = sqlite3.connect(db)
    con.execute("create table DRIVERS(NOMEFILE text primary key, MARCA text, MODELLO text, MOTORE text, CAVALLI int,"
                " DESCRIZIONE text, T_EPROM text, CKS text, CKS_FAM text, START_MD5 int, END_MD5 int, OFFSET_MD5 int, MD5 text)")
    con.execute("create table PROJECTS(PROJECT_ID int, PROJECT_NAME text, DRV_NAME text, ORI_MD5 text, LAST_MOD_DATE text)")
    con.execute("insert into DRIVERS values('AAA_0001','BMW','5','B48',252,'test','TC298','x','y',256,2304,0,?)", (good,))
    con.execute("insert into DRIVERS values('AAA_0002','BMW','5','B48',252,'decoy','TC298','x','y',256,2304,0,'00')")
    con.commit()
    con.close()
    c = connect(db)
    hits, *_ = match_bin(c, data, 'BMW')
    ok = [h[0] for h in hits] == ['AAA_0001']
    bad = bytearray(data)
    bad[0x200] ^= 1
    hits2, *_ = match_bin(c, bytes(bad), 'BMW')
    ok2 = hits2 == []
    c.close()
    print(f"  exact window-MD5 match found: {ok}   negative control (one flipped bit -> no match): {ok2}")
    print("selftest:", "PASS" if ok and ok2 else "FAIL")
    return 0 if ok and ok2 else 1


if __name__ == '__main__':
    sys.exit(main())

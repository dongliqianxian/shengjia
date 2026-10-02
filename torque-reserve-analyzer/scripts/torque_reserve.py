#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
torque_reserve.py — 扭矩模型正/逆表 储备(点火储备)静态分析 + 逆表偏置设计器

方法(本对话推导并在 MG1US008 真实表上验证过):
  表内储备 reserve(n,T) = F(n, G(n,T)) - T
    F = 正模型 KFMIOP 类: (rpm, rl%Air) -> 扭矩%
    G = 逆模型 KFMIRL 类: (rpm, 扭矩%)  -> rl%Air
  斜率 slope(n,rl) = dF/drl  (多 1 单位空气能换多少扭矩)
  偏置反算 dG(n,T) = target_reserve_rel * T / slope(工况点)
  闭环验证: 对 G_new 重算 reserve, 应 == 设计目标 (迭代收敛)

诚实边界: 只能看到"表内"储备。上游 η储备 除数、爆震线位置、点火实际离
MBT 多远, 两张表回答不了 —— 须 bin 搜索 η 表 / OBD 空调阶跃实测。

用法:
  python torque_reserve.py analyze  --fwd F.csv --inv G.csv [--out report.txt]
  python torque_reserve.py design   --fwd F.csv --inv G.csv \
      --rpm-lo 1600 --rpm-hi 3600 --t-lo 25 --t-hi 70 \
      --peak-rpm-lo 2000 --peak-rpm-hi 3200 --peak-t-lo 30 --peak-t-hi 50 \
      --peak 5.0 [--iters 3] [--out-inv G_new.csv] [--out report.txt]
  python torque_reserve.py selftest

CSV 格式 (WinOLS 复制粘贴即可, 逗号或制表符分隔):
  第 1 行: 第一格任意(如 RPM), 之后是列轴 (F=Air轴 / G=扭矩轴)
  之后每行: 行首 RPM, 之后是 Z 值
"""
import argparse
import csv
import io
import math
import sys

import numpy as np


# ----------------------------------------------------------------------
# IO
# ----------------------------------------------------------------------
def load_table(path):
    """读 CSV/TSV: 返回 (row_axis[rpm], col_axis, Z[rows,cols])"""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    delim = "\t" if "\t" in text.splitlines()[0] else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    col_axis = np.array([float(x) for x in rows[0][1:] if x.strip() != ""])
    row_axis, z = [], []
    for r in rows[1:]:
        row_axis.append(float(r[0]))
        z.append([float(x) for x in r[1 : 1 + len(col_axis)]])
    return np.array(row_axis), col_axis, np.array(z)


def save_table(path, row_axis, col_axis, z, corner="RPM"):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([corner] + [f"{v:g}" for v in col_axis])
        for i, r in enumerate(row_axis):
            w.writerow([f"{r:g}"] + [f"{v:.3f}" for v in z[i]])


# ----------------------------------------------------------------------
# 核心数学
# ----------------------------------------------------------------------
def interp_row(x_axis, z_row, x):
    """单行 1D 线性插值; 超轴按端点钳位(与 ECU 行为一致)"""
    return float(np.interp(x, x_axis, z_row))


def align_rows(rpm_src, z_src, rpm_dst):
    """若两表 RPM 轴不同, 沿 RPM 方向插值到 dst 轴"""
    if len(rpm_src) == len(rpm_dst) and np.allclose(rpm_src, rpm_dst):
        return z_src
    out = np.empty((len(rpm_dst), z_src.shape[1]))
    for j in range(z_src.shape[1]):
        out[:, j] = np.interp(rpm_dst, rpm_src, z_src[:, j])
    return out


def reserve_map(rpm, t_axis, G, air_axis, F):
    """表内储备: reserve[i,j] = F(rpm_i, G[i,j]) - t_axis[j]  (绝对扭矩%)
       同时返回饱和掩码(G 输出超出 F 的 Air 轴 => 反演病态区, 结果不可信)"""
    res = np.empty_like(G)
    sat = np.zeros_like(G, dtype=bool)
    for i in range(len(rpm)):
        for j, t in enumerate(t_axis):
            air = G[i, j]
            sat[i, j] = air > air_axis[-1] + 1e-9 or air < air_axis[0] - 1e-9
            res[i, j] = interp_row(air_axis, F[i], air) - t
    return res, sat


def slope_map(air_axis, F):
    """dF/drl 沿 Air 轴的中央差分 (每格: 多 1 空气换多少扭矩)"""
    return np.gradient(F, air_axis, axis=1)


def slope_at(air_axis, F_row, slope_row, air):
    return float(np.interp(air, air_axis, slope_row))


def _taper(x, lo_edge, lo_flat, hi_flat, hi_edge):
    """余弦渐变山包截面: edge 外=0, flat 内=1, 之间余弦过渡"""
    if x <= lo_edge or x >= hi_edge:
        return 0.0
    if lo_flat <= x <= hi_flat:
        return 1.0
    if x < lo_flat:
        u = (x - lo_edge) / max(lo_flat - lo_edge, 1e-9)
    else:
        u = (hi_edge - x) / max(hi_edge - hi_flat, 1e-9)
    return 0.5 - 0.5 * math.cos(math.pi * min(max(u, 0.0), 1.0))


def target_reserve_rel(rpm, t_axis, p):
    """目标储备(相对 %): 山包 = peak * taper(rpm) * taper(T)"""
    tgt = np.zeros((len(rpm), len(t_axis)))
    for i, n in enumerate(rpm):
        wn = _taper(n, p["rpm_lo"], p["peak_rpm_lo"], p["peak_rpm_hi"], p["rpm_hi"])
        for j, t in enumerate(t_axis):
            wt = _taper(t, p["t_lo"], p["peak_t_lo"], p["peak_t_hi"], p["t_hi"])
            tgt[i, j] = p["peak"] * wn * wt
    return tgt


def design_offset(rpm, t_axis, G, air_axis, F, tgt_rel, iters=3):
    """dG = tgt_abs/slope, 然后闭环迭代修正到收敛。返回 (G_new, 最终reserve, sat)"""
    slp = slope_map(air_axis, F)
    G_new = G.copy()
    for _ in range(max(1, iters)):
        res, sat = reserve_map(rpm, t_axis, G_new, air_axis, F)
        err = tgt_rel * t_axis[None, :] / 100.0 - res  # 目标(绝对) - 当前(绝对)
        for i in range(len(rpm)):
            for j in range(len(t_axis)):
                if tgt_rel[i, j] <= 1e-9:
                    continue  # 设计区外的格子绝对不动
                s = slope_at(air_axis, F[i], slp[i], G_new[i, j])
                if s > 1e-6 and not sat[i, j]:
                    G_new[i, j] += err[i, j] / s
    res, sat = reserve_map(rpm, t_axis, G_new, air_axis, F)
    return G_new, res, sat


def monotonic_violations(z):
    """逆表每行沿扭矩轴必须严格单调递增; 返回违规 (行,列) 列表"""
    bad = []
    d = np.diff(z, axis=1)
    for i, j in zip(*np.where(d <= 0)):
        bad.append((i, j + 1))
    return bad


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def fmt_map(rpm, cols, z, sat=None, title="", unit=""):
    out = [f"\n== {title} {unit} ==", "RPM\\ " + " ".join(f"{c:>7.0f}" for c in cols)]
    for i, n in enumerate(rpm):
        cells = []
        for j in range(len(cols)):
            mark = "*" if (sat is not None and sat[i, j]) else ""
            cells.append(f"{z[i, j]:>7.2f}{mark}" if not mark else f"{z[i, j]:>6.2f}*")
        out.append(f"{n:>5.0f} " + " ".join(cells))
    if sat is not None and sat.any():
        out.append("(* = 逆表输出超出正表 Air 轴 → 反演病态区, 该格结论不可信)")
    return "\n".join(out)


def analyze_report(rpm, t_axis, G, air_axis, F):
    res, sat = reserve_map(rpm, t_axis, G, air_axis, F)
    slp = slope_map(air_axis, F)
    ok = res[~sat]
    lines = ["扭矩模型静态储备分析", "=" * 60]
    lines.append(f"正表 F: {len(rpm)}x{len(air_axis)}  Air轴 {air_axis[0]:g}..{air_axis[-1]:g}")
    lines.append(f"逆表 G: {len(rpm)}x{len(t_axis)}  扭矩轴 {t_axis[0]:g}..{t_axis[-1]:g}")
    lines.append(f"\n[表内储备] 有效格 |max|={np.abs(ok).max():.2f}%  mean={ok.mean():+.2f}%  "
                 f"病态格 {int(sat.sum())}/{res.size}")
    if np.abs(ok).max() < 0.5:
        lines.append("判定: 两表干净互逆 → 表内储备≈0。原厂储备(若有)在上游η除数, 表看不到。")
    else:
        big = np.abs(np.where(sat, 0, res)) > 0.5
        top2 = np.zeros_like(big); top2[:, -2:] = True
        n_top, n_mid = int((big & top2).sum()), int((big & ~top2).sum())
        lines.append(f"判定: {int(big.sum())} 格 |储备|>0.5% (顶部两列 {n_top} 格 / 其余 {n_mid} 格)")
        if n_mid == 0:
            lines.append("      全部集中在最高两列扭矩 → 高负荷反演病态区的截断伪影(原厂正常现象),")
            lines.append("      中低负荷仍判定为干净互逆、表内储备≈0。")
        else:
            lines.append("      中低负荷区存在错位 → 要么此标定内建了表内储备, 要么是转录/解码错误。")
            lines.append("      先复核这些格子数值; 确认无误则该错位量就是原厂表内储备图。")
    lines.append(fmt_map(rpm, t_axis, res, sat, "表内储备图 reserve(n,T)", "[绝对扭矩%]"))
    lines.append(fmt_map(rpm, air_axis, slp, None, "扭矩灵敏度 dF/drl", "[扭矩%/空气%]"))
    lines.append("\n[灵敏度读法] 值越小, 同样储备要付越多空气。典型: 中负荷~0.5, 高负荷掉到~0.35")
    lines.append("             → 这就是山包在高负荷侧必须收窄的定量理由。")
    return "\n".join(lines), res, sat


def design_report(rpm, t_axis, G, G_new, res_new, sat, tgt_rel):
    # np.where still evaluates both branches; use masked division so the valid
    # 0% request column stays at 0 without emitting divide-by-zero warnings.
    dpct = np.zeros_like(G)
    np.divide(G_new - G, G, out=dpct, where=G > 1e-9)
    dpct *= 100.0
    ach_rel = np.zeros_like(res_new)
    np.divide(res_new, t_axis[None, :] / 100.0, out=ach_rel,
              where=t_axis[None, :] > 1e-9)
    tgt_abs = tgt_rel * t_axis[None, :] / 100.0
    in_design = tgt_rel > 1e-9
    err = np.where(sat | ~in_design, 0.0, res_new - tgt_abs)  # 绝对扭矩%误差, 只看设计区
    bad = monotonic_violations(G_new)
    lines = ["逆表偏置设计结果", "=" * 60]
    lines.append(fmt_map(rpm, t_axis, tgt_rel, None, "设计目标储备", "[相对%]"))
    lines.append(fmt_map(rpm, t_axis, dpct, None, "逆表偏置量 dG/G", "[%]"))
    lines.append(fmt_map(rpm, t_axis, ach_rel, sat, "闭环实得储备", "[相对%]"))
    lines.append(f"\n[闭环验证] 设计区内 实得-目标 最大偏差 {np.abs(err).max():.2f} 绝对扭矩% "
                 f"{'✓ 收敛' if np.abs(err).max() < 0.3 else '✗ 未收敛, 加大 --iters 或检查病态区'}")
    lines.append(f"[单调检查] 逆表每行沿扭矩轴严格递增: "
                 f"{'✓ 通过' if not bad else '✗ 违规格(行,列): ' + str(bad[:8])}")
    lines.append("\n[装车前提醒] 本设计是表内静态结论。装车前须确认:")
    lines.append("  1) 目标区原厂点火不在爆震线上(OBD空调阶跃/爆震回退量) — 否则储备兑现不了")
    lines.append("  2) 峰值>6% 需重新评估油耗/排温代价(~每1%储备≈1%巡航油耗+1.2~1.5°推迟)")
    lines.append("  3) 高负荷列(80%+)与低转(<=1000rpm)保持 0 偏置 — 本工具默认如此, 别手改")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# selftest
# ----------------------------------------------------------------------
def _synth_tables():
    """合成一对严格互逆的表: F = a(n)*rl^0.97 (凹的), G = F^-1"""
    rpm = np.array([800, 1600, 2400, 3200, 4000, 4800, 5600])
    air = np.linspace(10, 220, 43)
    t = np.linspace(8, 100, 14)
    a = 0.60 + 0.10 * np.exp(-((rpm - 3000) / 1800.0) ** 2)  # 扭矩曲线驼峰
    F = np.array([ai * air ** 0.97 for ai in a])  # 每行 220air 处 ≥110 → t轴0..100全程可逆
    G = np.empty((len(rpm), len(t)))
    for i in range(len(rpm)):
        # 数值反演: 对每个目标扭矩找 rl
        G[i] = np.interp(t, F[i], air)
    return rpm, t, G, air, F


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            fails.append(name)

    print("selftest: torque_reserve")
    rpm, t, G, air, F = _synth_tables()

    # 1. 互逆表 → 储备≈0
    res, sat = reserve_map(rpm, t, G, air, F)
    m = np.abs(res[~sat]).max()
    check("互逆表储备≈0", m < 0.25, f"max|res|={m:.3f}")

    # 2. 故意 +5% 错位 → 储备图应报出对应正值
    G_biased = G * 1.05
    res2, sat2 = reserve_map(rpm, t, G_biased, air, F)
    mid = res2[3, 6]  # 中区某格
    check("错位表储备>0 且量级正确", 1.0 < mid < 6.0, f"res={mid:.2f}")

    # 3. 设计山包 → 闭环收敛
    p = dict(rpm_lo=1000, rpm_hi=5200, t_lo=20, t_hi=80,
             peak_rpm_lo=2000, peak_rpm_hi=4000, peak_t_lo=35, peak_t_hi=60, peak=5.0)
    tgt = target_reserve_rel(rpm, t, p)
    G_new, res3, sat3 = design_offset(rpm, t, G, air, F, tgt, iters=3)
    tgt_abs = tgt * t[None, :] / 100.0
    err = np.abs(np.where(sat3 | (tgt <= 1e-9), 0, res3 - tgt_abs)).max()
    check("山包闭环收敛(绝对)<0.3", err < 0.3, f"maxerr={err:.3f}")
    ach = np.where(t[None, :] > 1e-9, res3 / (t[None, :] / 100.0), 0.0)

    # 4. 峰值格实得≈peak
    pk = ach[(rpm >= 2000) & (rpm <= 4000)][:, (t >= 35) & (t <= 60)]
    check("峰值区实得≈5%", abs(pk.mean() - 5.0) < 0.5, f"mean={pk.mean():.2f}")

    # 5. 新表单调
    check("新逆表行单调", not monotonic_violations(G_new))

    # 6. 边缘归零(低转/高负荷不动)
    check("边缘保持原厂", np.allclose(G_new[0], G[0], atol=1e-6)
          and np.allclose(G_new[:, -1], G[:, -1], atol=1e-6))

    # 7. 病态区标记: 把 G 顶到超轴
    G_ov = G.copy()
    G_ov[0, -1] = air[-1] + 50
    _, sat7 = reserve_map(rpm, t, G_ov, air, F)
    check("超轴病态区被标记", bool(sat7[0, -1]))

    print(("selftest: ALL PASS" if not fails else f"selftest: {len(fails)} FAIL {fails}"))
    return 0 if not fails else 1


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_io(p):
        p.add_argument("--fwd", required=True, help="正模型 CSV (KFMIOP 类: 列轴=Air)")
        p.add_argument("--inv", required=True, help="逆模型 CSV (KFMIRL 类: 列轴=扭矩%)")
        p.add_argument("--out", help="报告输出文件(默认打印到屏幕)")

    add_io(sub.add_parser("analyze", help="储备扫描 + 灵敏度图"))

    pd = sub.add_parser("design", help="设计山包偏置并输出新逆表")
    add_io(pd)
    pd.add_argument("--rpm-lo", type=float, default=1200)
    pd.add_argument("--rpm-hi", type=float, default=4800)
    pd.add_argument("--t-lo", type=float, default=20)
    pd.add_argument("--t-hi", type=float, default=80)
    pd.add_argument("--peak-rpm-lo", type=float, default=2000)
    pd.add_argument("--peak-rpm-hi", type=float, default=3200)
    pd.add_argument("--peak-t-lo", type=float, default=30)
    pd.add_argument("--peak-t-hi", type=float, default=50)
    pd.add_argument("--peak", type=float, default=5.0, help="峰值储备 相对%% (默认5, 上限建议6)")
    pd.add_argument("--iters", type=int, default=3)
    pd.add_argument("--out-inv", default="G_new.csv", help="新逆表输出 CSV")

    sub.add_parser("selftest")
    a = ap.parse_args()

    if a.cmd == "selftest":
        sys.exit(selftest())

    rpm_f, air_axis, F = load_table(a.fwd)
    rpm_g, t_axis, G = load_table(a.inv)
    F = align_rows(rpm_f, F, rpm_g)
    rpm = rpm_g

    if a.cmd == "analyze":
        rep, _, _ = analyze_report(rpm, t_axis, G, air_axis, F)
    else:
        if a.peak > 6.0:
            print(f"[警告] --peak {a.peak}% 超过建议上限 6% — 每1%储备≈1%巡航油耗, "
                  f">6%后点火推迟进入催化器加热量级, 且爆震区可能兑现不了。继续执行, 后果自负。")
        p = dict(rpm_lo=a.rpm_lo, rpm_hi=a.rpm_hi, t_lo=a.t_lo, t_hi=a.t_hi,
                 peak_rpm_lo=a.peak_rpm_lo, peak_rpm_hi=a.peak_rpm_hi,
                 peak_t_lo=a.peak_t_lo, peak_t_hi=a.peak_t_hi, peak=a.peak)
        tgt = target_reserve_rel(rpm, t_axis, p)
        rep0, _, _ = analyze_report(rpm, t_axis, G, air_axis, F)
        G_new, res_new, sat = design_offset(rpm, t_axis, G, air_axis, F, tgt, iters=a.iters)
        save_table(a.out_inv, rpm, t_axis, G_new, corner="RPM|T%")
        rep = rep0 + "\n\n" + design_report(rpm, t_axis, G, G_new, res_new, sat, tgt) \
              + f"\n\n新逆表已写入: {a.out_inv}"

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(rep)
        print(f"报告已写入 {a.out}")
    else:
        print(rep)


if __name__ == "__main__":
    main()

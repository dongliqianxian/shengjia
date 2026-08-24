#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
torque_model.py — 扭矩基础控制结构的第一性原理关系与自洽检查(torque-model-builder 技能)。

不是替代 ECU 实际标定表,而是回答"结构对不对、趋势对不对、数量级对不对":
  * 逆扭矩模型(扭矩→负荷)与正扭矩模型(负荷+点火+λ→扭矩)是否自洽(往返≈恒等)。
  * 点火效率随相对 MBT 退角单调下降(MBT 处=1.0)。
  * 扭矩储备 = M(基础点火) − M(当前退后点火) ≥ 0。

诚实边界(doctrine):M_optimal(load,rpm)、点火效率曲线、损失都是【标定表】,这里用通用近似
(点火效率用 MBT 附近钟形近似)。具体数值必须以该 ECU 实际表为准(解码交 bin 引擎,拆解交
ecu-map-analysis)。本脚本服务于教学演示 + 解码后 sanity check。

用法:
    python torque_model.py --selftest
    python torque_model.py roundtrip --torque 300 --rpm 3000   # 逆→正往返自洽
    python torque_model.py spark-eff --dspark -10              # 退 10° 的扭矩效率
    python torque_model.py reserve --m-opt 250 --dspark-base 0 --dspark-now -12
"""
import sys, math, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------------------
# 点火效率 η_spark:相对 MBT 的扭矩比。MBT(dspark=0)=1.0,退角(dspark<0)下降。
# 通用近似:MBT 附近扭矩对点火近似钟形/余弦。真实为标定表,此处仅趋势与数量级。
# ---------------------------------------------------------------------------
def spark_efficiency(dspark_from_mbt, span=45.0):
    """dspark_from_mbt: 相对 MBT 的点火角差(°),0=MBT,负=退后。
    退到 span 度时效率→~0。用余弦近似:η=cos(π/2 * min(|Δ|,span)/span)。
    进角超过 MBT(dspark>0,理论上会爆震)也按对称下降处理并截断。"""
    d = min(abs(dspark_from_mbt), span)
    return max(0.0, math.cos((math.pi / 2.0) * (d / span)))


# ---------------------------------------------------------------------------
# λ 效率 η_lambda:λ≈0.9 附近火焰最快、扭矩最高;λ=1.0 略低;过浓过稀都降。
# 通用近似,峰值在 0.9。
# ---------------------------------------------------------------------------
def lambda_efficiency(lam):
    peak = 0.90
    # 抛物线近似,峰值 1.0 @0.90,两侧下降;限制在合理区间
    eff = 1.0 - 1.8 * (lam - peak) ** 2
    return max(0.0, min(1.0, eff))


# ---------------------------------------------------------------------------
# 最优指示扭矩 M_optimal(load, rpm):MBT + λ 最优时该负荷×转速的指示扭矩。
# 通用近似:与负荷近似成正比,转速给一个缓和的容积效率型修正。真实为标定表。
# ---------------------------------------------------------------------------
def optimal_indicated_torque(load, rpm, k=4.0, rpm_peak=4000.0):
    """load: 相对充气量(0..~2.0,1.0=满充气);返回指示扭矩(Nm,示意量纲)。
    k: 单位负荷扭矩系数(标定相关)。转速修正:峰值扭矩转速附近效率最高。"""
    ve_shape = 1.0 - 0.15 * ((rpm - rpm_peak) / rpm_peak) ** 2   # 缓和钟形
    ve_shape = max(0.6, min(1.0, ve_shape))
    return k * 100.0 * load * ve_shape


# ---------------------------------------------------------------------------
# 损失 M_loss:摩擦 + 泵气 + 附件,把指示扭矩换算到曲轴扭矩。随转速升。
# ---------------------------------------------------------------------------
def loss_torque(rpm, accessory=0.0):
    friction_pumping = 15.0 + 0.004 * rpm      # 示意:随转速线性升
    return friction_pumping + accessory


# ---------------------------------------------------------------------------
# 正扭矩模型:空气(负荷)+点火+λ+损失 → 曲轴扭矩
# ---------------------------------------------------------------------------
def forward_torque(load, rpm, dspark_from_mbt=0.0, lam=0.90, accessory=0.0, k=4.0):
    m_opt = optimal_indicated_torque(load, rpm, k)
    m_ind = m_opt * spark_efficiency(dspark_from_mbt) * lambda_efficiency(lam)
    return m_ind - loss_torque(rpm, accessory)


# ---------------------------------------------------------------------------
# 逆扭矩模型:目标曲轴扭矩 → 需要的负荷(MBT、给定 λ 下)
# ---------------------------------------------------------------------------
def load_for_torque(target_crank_torque, rpm, dspark_from_mbt=0.0, lam=0.90,
                    accessory=0.0, k=4.0):
    """回加损失得指示扭矩,再除以(单位负荷扭矩×点火效率×λ效率)得负荷。"""
    m_ind_needed = target_crank_torque + loss_torque(rpm, accessory)
    denom = optimal_indicated_torque(1.0, rpm, k) * spark_efficiency(dspark_from_mbt) \
        * lambda_efficiency(lam)
    if denom <= 0:
        raise ValueError("分母非正(点火/λ 效率为 0),无法求负荷")
    return m_ind_needed / denom


def torque_reserve(load, rpm, dspark_base, dspark_now, lam=0.90, accessory=0.0, k=4.0):
    """储备 = M(基础点火) − M(当前退后点火)。同负荷下,退点火压住的可瞬时释放扭矩。"""
    m_base = forward_torque(load, rpm, dspark_base, lam, accessory, k)
    m_now = forward_torque(load, rpm, dspark_now, lam, accessory, k)
    return m_base - m_now


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("ok " if c else "FAIL ") + m)
        ok = ok and c

    # 点火效率:MBT=1.0,退角单调下降,退得越多越低
    chk(abs(spark_efficiency(0.0) - 1.0) < 1e-9, "点火效率 MBT(0°)=1.0")
    chk(spark_efficiency(-5) > spark_efficiency(-15) > spark_efficiency(-30),
        "点火效率随退角单调下降")
    chk(0 <= spark_efficiency(-90) <= 0.01, "退角超量程效率→0(截断)")

    # λ 效率:峰值在 0.90
    chk(lambda_efficiency(0.90) >= lambda_efficiency(1.0) and
        lambda_efficiency(0.90) >= lambda_efficiency(0.80), "λ 效率峰值在 0.90")

    # 逆→正往返自洽:给目标扭矩解负荷,再正向算回,应≈原目标
    for tq, rpm in [(120, 1500), (250, 3000), (380, 5000)]:
        load = load_for_torque(tq, rpm)
        back = forward_torque(load, rpm)
        chk(abs(back - tq) < 1e-6, "逆→正往返自洽 @%dNm/%drpm (回算 %.3f)" % (tq, rpm, back))

    # 储备:退点火产生正储备,退得越多储备越大
    load = load_for_torque(200, 2500)      # 先定一个能出 200Nm 的负荷(MBT)
    r12 = torque_reserve(load, 2500, 0, -12)
    r20 = torque_reserve(load, 2500, 0, -20)
    chk(r12 > 0 and r20 > r12, "扭矩储备>0 且随退角增大 (r12=%.1f r20=%.1f)" % (r12, r20))

    # 结构性:同负荷退点火后实际扭矩下降(快通道降扭原理)
    m_mbt = forward_torque(load, 2500, 0)
    m_ret = forward_torque(load, 2500, -12)
    chk(m_ret < m_mbt, "同负荷退点火→实际扭矩下降(快通道)")

    # 损失:转速越高损失越大(摩擦+泵气)
    chk(loss_torque(6000) > loss_torque(1000), "损失随转速升")

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Torque-based structure first-principles relations")
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--selftest", action="store_true")

    p1 = sub.add_parser("roundtrip", help="逆→正往返自洽")
    p1.add_argument("--torque", type=float, required=True)
    p1.add_argument("--rpm", type=float, required=True)
    p1.add_argument("--lam", type=float, default=0.90)
    p1.add_argument("--dspark", type=float, default=0.0)

    p2 = sub.add_parser("spark-eff", help="相对 MBT 退角的扭矩效率")
    p2.add_argument("--dspark", type=float, required=True)

    p3 = sub.add_parser("reserve", help="扭矩储备 = M(基础点火)−M(退后点火)")
    p3.add_argument("--m-opt", type=float, default=None, help="(可选)直接给最优扭矩对应负荷")
    p3.add_argument("--load", type=float, default=None)
    p3.add_argument("--rpm", type=float, default=2500)
    p3.add_argument("--dspark-base", type=float, default=0.0)
    p3.add_argument("--dspark-now", type=float, required=True)

    a = ap.parse_args()
    if a.selftest or a.cmd is None:
        sys.exit(selftest())

    if a.cmd == "roundtrip":
        load = load_for_torque(a.torque, a.rpm, a.dspark, a.lam)
        back = forward_torque(load, a.rpm, a.dspark, a.lam)
        print("目标扭矩 %.1f Nm @%.0f rpm (λ=%.2f, Δspark=%.0f)" %
              (a.torque, a.rpm, a.lam, a.dspark))
        print("→ 逆扭矩模型解出负荷 = %.4f (相对充气量)" % load)
        print("→ 正扭矩模型回算扭矩 = %.2f Nm  (往返误差 %.2e)" % (back, abs(back - a.torque)))
        print("说明:逆模型定负荷目标(空气模型据此实现),正模型据实际空气估扭矩用于监控。")
    elif a.cmd == "spark-eff":
        e = spark_efficiency(a.dspark)
        print("相对 MBT 点火 Δ=%.0f° → 点火效率 η_spark=%.4f (扭矩为 MBT 的 %.1f%%)"
              % (a.dspark, e, e * 100))
    elif a.cmd == "reserve":
        load = a.load if a.load is not None else 1.0
        r = torque_reserve(load, a.rpm, a.dspark_base, a.dspark_now)
        print("负荷=%.3f @%.0f rpm: 基础点火 Δ=%.0f° vs 当前 Δ=%.0f°" %
              (load, a.rpm, a.dspark_base, a.dspark_now))
        print("→ 扭矩储备 = %.1f Nm (拉回基础点火可瞬时释放;怠速稳定/CAT 加热/换挡补扭用)" % r)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
control_loop.py — 闭环控制逻辑的第一性原理演示与自洽检查(closed-loop-control 技能)。

不是替代 ECU 实际增益/权限表,而是回答"行为/趋势对不对":
  * 燃油 trim 归账:把闭环长期顶着的修正搬回基础表,让 LTFT 回 ~0(前馈/反馈分解)。
  * PID 阶跃行为:良好整定 vs 高积分无抗饱和(过冲/windup)、权限饱和(clamp)。
  * 自适应快→慢环迁移:快环(STFT)修正如何被慢环(LTFT)吸收;清值后快环重新顶起。

诚实边界(doctrine):PID 用一阶滞后 plant 的通用演示模型,不是你 ECU 的实际增益/plant;
trim 归账假设纯乘性燃油修正,且必须先排除传感器/MAF/进气泄漏误差(空气优先)。
具体增益/权限/使能条件是标定表:解码交 bin 引擎、拆解交 ecu-map-analysis、命名交 oem-strategy-library。

用法:
    python control_loop.py --selftest
    python control_loop.py reconcile --base 3.0 --ltft 12 --stft 3
    python control_loop.py pid --setpoint 0.7 --kp 2 --ki 3 --kd 0.1
    python control_loop.py adapt --bias 8
"""
import sys, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------------------
# 1. 燃油 trim 归账:前馈/反馈分解 —— 把闭环修正搬回基础表
# ---------------------------------------------------------------------------
def reconcile_fuel_trim(base_value, stft_pct, ltft_pct):
    """总修正=STFT+LTFT(%);trim>0=ECU 在加油=基础偏稀=基础上调。
    返回 (新基础, 总修正比例, 搬移后预期残差~0)。"""
    total = (stft_pct + ltft_pct) / 100.0
    new_base = base_value * (1.0 + total)
    return new_base, total * 100.0


def trim_is_healthy(stft_pct, ltft_pct, thresh=3.0):
    """doctrine 第⑪步:闭环修正应 <3%。返回是否健康 + 总修正。"""
    total = abs(stft_pct + ltft_pct)
    return total < thresh, stft_pct + ltft_pct


# ---------------------------------------------------------------------------
# 2. PID 阶跃(一阶滞后 plant),含抗饱和 + 输出权限 clamp
# ---------------------------------------------------------------------------
def simulate_pid(setpoint, kp, ki, kd, steps=400, dt=0.02, tau=0.25,
                 out_min=0.0, out_max=1.0, plant_gain=1.0, anti_windup=True):
    """返回轨迹 [(t, actual, out)]。plant: actual += (plant_gain*out - actual)*dt/tau。
    稳态需 out≈setpoint/plant_gain,由积分项提供(err→0 时 P、D 项→0)。"""
    actual, integral, prev_err = 0.0, 0.0, setpoint
    trace = []
    for i in range(steps):
        err = setpoint - actual
        integral += err * dt
        deriv = (err - prev_err) / dt
        raw = kp * err + ki * integral + kd * deriv
        out = max(out_min, min(out_max, raw))
        # 抗饱和:输出被 clamp 且积分还在往饱和方向推 → 撤回本步积分累加
        if anti_windup and out != raw and (integral * err) > 0:
            integral -= err * dt
        actual += (plant_gain * out - actual) * (dt / tau)
        prev_err = err
        trace.append((i * dt, actual, out))
    return trace


def _overshoot(trace, setpoint):
    peak = max(a for _, a, _ in trace)
    return max(0.0, (peak - setpoint) / setpoint * 100.0)


def _settle_value(trace):
    return trace[-1][1]


# ---------------------------------------------------------------------------
# 3. 自适应:快环(STFT)→慢环(LTFT)迁移
# ---------------------------------------------------------------------------
def simulate_adaptation(bias_pct, steps=60, learn_rate=0.15, reset_at=None):
    """持续偏差 bias(%,基础偏稀需要的总修正)。慢环按 learn_rate 吸收快环平均值,
    快环 = 总需求 − 慢环已学。reset_at: 在该步把慢环清零(演示清值后快环重新顶起)。
    返回 [(step, stft, ltft, total)]。total 应≈bias 恒定。"""
    ltft, trace = 0.0, []
    for i in range(steps):
        if reset_at is not None and i == reset_at:
            ltft = 0.0                       # 清学习值
        stft = bias_pct - ltft               # 快环顶剩余
        ltft += learn_rate * stft            # 慢环逐步吸收
        trace.append((i, stft, bias_pct - stft, stft + (bias_pct - stft)))
    return trace


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("ok " if c else "FAIL ") + m)
        ok = ok and c

    # trim 归账:基础 3.0ms, LTFT+12 STFT+3 → ×1.15 = 3.45
    nb, tot = reconcile_fuel_trim(3.0, 3, 12)
    chk(abs(nb - 3.45) < 1e-9 and abs(tot - 15.0) < 1e-9, "trim 归账 3.0×1.15=3.45")
    # 归账后再算:新基础下 trim 应回 ~0(等效基础已含 +15%)
    nb2, tot2 = reconcile_fuel_trim(nb, 0, 0)
    chk(abs(nb2 - nb) < 1e-9, "归账后残差 trim=0 不再动基础")
    # 方向:负 trim 下调基础
    nb3, _ = reconcile_fuel_trim(3.0, -2, -8)
    chk(nb3 < 3.0, "负 trim(过浓)→ 基础下调")
    # 健康判据
    h, _ = trim_is_healthy(1, 1.5)
    chk(h, "总 2.5% 判健康")
    h2, _ = trim_is_healthy(4, 12)
    chk(not h2, "总 16% 判不健康")

    # PID:良好整定应收敛到 setpoint 附近
    sp = 0.7
    good = simulate_pid(sp, kp=2.0, ki=3.0, kd=0.1)
    chk(abs(_settle_value(good) - sp) / sp < 0.05, "良好整定收敛到 setpoint ±5%")
    chk(all(o <= 1.0 + 1e-9 for _, _, o in good), "输出不超权限 out_max")

    # windup:高积分 + 无抗饱和 + 会饱和 → 过冲大于带抗饱和
    windup = simulate_pid(sp, kp=2.0, ki=20.0, kd=0.0, anti_windup=False, tau=0.5)
    protg = simulate_pid(sp, kp=2.0, ki=20.0, kd=0.0, anti_windup=True, tau=0.5)
    chk(_overshoot(windup, sp) > _overshoot(protg, sp),
        "无抗饱和过冲 > 有抗饱和 (windup=%.1f%% vs %.1f%%)"
        % (_overshoot(windup, sp), _overshoot(protg, sp)))

    # 权限饱和:setpoint 高于 plant 能达到的(plant_gain<sp)→ 达不到
    sat = simulate_pid(0.9, kp=2, ki=3, kd=0.1, plant_gain=0.6, out_max=1.0)
    chk(_settle_value(sat) < 0.9, "权限/plant 不足时达不到 setpoint")

    # 自适应:总修正守恒≈bias,慢环吸收快环
    tr = simulate_adaptation(8.0)
    chk(abs(tr[-1][3] - 8.0) < 1e-6, "总修正守恒≈bias 8%")
    chk(tr[0][1] > tr[-1][1] and tr[-1][2] > tr[0][2], "快环回落、慢环升(迁移)")
    chk(tr[-1][1] < 1.0, "末态快环≈0(慢环已吸收)")
    # 清值后快环重新顶起
    trr = simulate_adaptation(8.0, reset_at=30)
    chk(trr[30][1] > trr[29][1], "清学习值后快环立即变大(重新顶起)")

    print("ALL PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Closed-loop control first-principles demos")
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--selftest", action="store_true")

    p1 = sub.add_parser("reconcile", help="燃油 trim 归账:把闭环修正搬回基础表")
    p1.add_argument("--base", type=float, required=True, help="基础值(如脉宽 ms 或 VE)")
    p1.add_argument("--ltft", type=float, default=0.0, help="长期燃油修正 %%")
    p1.add_argument("--stft", type=float, default=0.0, help="短期燃油修正 %%")

    p2 = sub.add_parser("pid", help="PID 阶跃行为演示")
    p2.add_argument("--setpoint", type=float, default=0.7)
    p2.add_argument("--kp", type=float, default=2.0)
    p2.add_argument("--ki", type=float, default=3.0)
    p2.add_argument("--kd", type=float, default=0.1)
    p2.add_argument("--no-anti-windup", action="store_true")

    p3 = sub.add_parser("adapt", help="快环→慢环迁移")
    p3.add_argument("--bias", type=float, default=8.0, help="持续偏差需要的总修正 %%")
    p3.add_argument("--reset-at", type=int, default=None)

    a = ap.parse_args()
    if a.selftest or a.cmd is None:
        sys.exit(selftest())

    if a.cmd == "reconcile":
        healthy, total = trim_is_healthy(a.stft, a.ltft)
        nb, tot = reconcile_fuel_trim(a.base, a.stft, a.ltft)
        print("总修正 = STFT %+.1f%% + LTFT %+.1f%% = %+.1f%%  [%s]"
              % (a.stft, a.ltft, total, "健康<3%%" if healthy else "不健康,基础表偏了"))
        print("方向:trim %s → 基础%s" % ("为正=ECU 加油=基础偏稀" if total > 0 else "为负=ECU 减油=基础偏浓",
                                        "上调" if total > 0 else "下调"))
        print("新基础 = %.4f × (1%+.4f) = %.4f" % (a.base, tot / 100.0, nb))
        print("搬移后预期:LTFT/STFT 应回落到 ~0(闭环无事可做)。")
        print("★ 前提:已确认 trim 非传感器/MAF/进气泄漏误差(空气优先),否则会把测量错误焊进基础表。")
    elif a.cmd == "pid":
        tr = simulate_pid(a.setpoint, a.kp, a.ki, a.kd, anti_windup=not a.no_anti_windup)
        os_pct = _overshoot(tr, a.setpoint)
        print("PID setpoint=%.2f Kp=%.1f Ki=%.1f Kd=%.2f 抗饱和=%s"
              % (a.setpoint, a.kp, a.ki, a.kd, "否" if a.no_anti_windup else "是"))
        print("过冲 = %.1f%%  末态 = %.3f (目标 %.3f)" % (os_pct, _settle_value(tr), a.setpoint))
        # 简易 ASCII 轨迹(每 40 步取一点)
        pts = tr[::40]
        for t, act, out in pts:
            bar = "#" * int(act / a.setpoint * 30)
            print("t=%4.2f 实际=%.3f 输出=%.3f |%s" % (t, act, out, bar))
        print("说明:过冲大/震荡 → 查 Ki 与抗饱和;末态达不到 → 前馈基础/权限不足(别让闭环独扛)。")
    elif a.cmd == "adapt":
        tr = simulate_adaptation(a.bias, reset_at=a.reset_at)
        print("持续偏差 bias=%.1f%%  快环(STFT)→慢环(LTFT)迁移%s"
              % (a.bias, "(step %d 清学习值)" % a.reset_at if a.reset_at is not None else ""))
        print("step  STFT   LTFT   总修正")
        for i, stft, ltft, total in tr[::5]:
            print("%4d  %+5.1f  %+5.1f  %+5.1f" % (i, stft, ltft, total))
        print("说明:总修正守恒,只是从短期迁到长期。清学习值后快环需重新顶起并再收敛。")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
airfuel.py — air & fuel model math for the air-fuel-model-builder skill.

Pure Python 3, no dependencies. The doctrine: air modeling is the #1 priority; every fuel/spark
number rests on it. This module does the physics so the calibration is built on real numbers, not
hand-waving. Express the model on the LOAD (air-mass) axis, not boost.

Run `python airfuel.py --selftest` to verify the equations against hand-checked cases.
"""

import math

R_AIR = 287.05          # J/(kg*K) specific gas constant for dry air
AFR_STOICH_GASOLINE = 14.68


# --------------------------------------------------------------------------- #
# Air model (speed-density)
# --------------------------------------------------------------------------- #

def ideal_gas_density(map_kpa, iat_c):
    """Manifold charge density [kg/m^3] from MAP [kPa absolute] and IAT [deg C]."""
    map_pa = map_kpa * 1000.0
    iat_k = iat_c + 273.15
    return map_pa / (R_AIR * iat_k)


def sd_airmass_cycle(map_kpa, iat_c, disp_l, n_cyl, ve):
    """Trapped air mass per cylinder per intake event [kg]. VE as a fraction (0.95 = 95%)."""
    disp_m3 = disp_l / 1000.0
    rho = ideal_gas_density(map_kpa, iat_c)
    return ve * (disp_m3 / n_cyl) * rho


def sd_airmass_dot(rpm, map_kpa, iat_c, disp_l, n_cyl, ve):
    """Total engine air-mass flow [kg/s] for a 4-stroke (one intake per cyl per 2 revolutions)."""
    m_cycle = sd_airmass_cycle(map_kpa, iat_c, disp_l, n_cyl, ve)
    intakes_per_sec = n_cyl * (rpm / 2.0) / 60.0
    return m_cycle * intakes_per_sec


# --------------------------------------------------------------------------- #
# Fuel model
# --------------------------------------------------------------------------- #

def afr_target_from_lambda(lambda_target, afr_stoich=AFR_STOICH_GASOLINE):
    """Convert a lambda target to an AFR target."""
    return afr_stoich * lambda_target


def required_fuel_mass(air_mass_kg, lambda_target, afr_stoich=AFR_STOICH_GASOLINE):
    """Fuel mass [kg] to hit lambda_target for a given trapped air mass [kg]."""
    return air_mass_kg / afr_target_from_lambda(lambda_target, afr_stoich)


def injector_flow_at_pressure(flow_ref_kg_s, p_actual, p_ref):
    """Rescale injector static flow for a different differential fuel pressure (sqrt law)."""
    return flow_ref_kg_s * math.sqrt(p_actual / p_ref)


def injector_pw(fuel_mass_kg, injector_flow_kg_s, deadtime_ms=0.0):
    """Injector pulse width [ms] = open time to deliver fuel_mass + deadtime offset."""
    open_ms = (fuel_mass_kg / injector_flow_kg_s) * 1000.0
    return open_ms + deadtime_ms


def injector_duty(pw_ms, rpm):
    """Injector duty cycle [%] for a 4-stroke (one injection event per 2 revolutions).

    Available window per cycle = 120000 / rpm milliseconds. Keep below ~85% for headroom.
    """
    window_ms = 120000.0 / rpm
    return 100.0 * pw_ms / window_ms


GASOLINE_LB_PER_GAL = 6.0
LB_PER_HR_TO_CC_MIN = 10.5   # gasoline approx (Banish: 39.06 lb/hr ~= 410.9 cc/min)


def fuel_system_sizing(power_hp, n_cyl, bsfc=0.5, duty_headroom=0.80):
    """BSFC-based fuel-system sizing (Banish Ch.2): from a power target, size injectors + pump.

    power x BSFC = total fuel lb/hr; / cyl = per-injector minimum; / headroom (0.80 = 20% cushion,
    the ideal margin) = safe injector size; pump = total / headroom. Gasoline ~6 lb/gal.
    BSFC ~0.5 lb/hp-hr is a typical NA gasoline WOT figure; FI/E85 run higher. Returns a dict.
    """
    total_lb_hr = power_hp * bsfc
    inj_min = total_lb_hr / n_cyl
    inj_safe = inj_min / duty_headroom
    pump_lb_hr = inj_safe * n_cyl
    return {
        "total_fuel_lb_hr": total_lb_hr,
        "injector_min_lb_hr": inj_min,
        "injector_safe_lb_hr": inj_safe,
        "injector_safe_cc_min": inj_safe * LB_PER_HR_TO_CC_MIN,
        "pump_lb_hr": pump_lb_hr,
        "pump_gal_hr": pump_lb_hr / GASOLINE_LB_PER_GAL,
        "pump_L_hr": pump_lb_hr / GASOLINE_LB_PER_GAL * 3.785,
    }


# --------------------------------------------------------------------------- #
# Close the loop: correct the model from real data
# --------------------------------------------------------------------------- #

def ve_correction(ve_old, lambda_meas, lambda_target):
    """Corrected VE from a wideband reading. If measured leaner (higher lambda) than target,
    the model under-estimated air -> raise VE. Apply cell-by-cell; converge, don't overshoot."""
    return ve_old * (lambda_meas / lambda_target)


def ve_correction_from_trims(ve_old, stft_pct, ltft_pct):
    """Fold short+long fuel trims back into VE. Positive total trim = ECU adding fuel = model under-
    estimating air -> raise VE. After folding, trims should collapse toward < 3% (doctrine)."""
    total = (stft_pct + ltft_pct) / 100.0
    return ve_old * (1.0 + total)


def maf_rescale(values, factor):
    """Rescale a MAF transfer curve (or injector flow table) by a single factor. REMINDER: scale
    MAF + injector flow + displacement together; do NOT scale deadtime or voltage-comp with this."""
    return [v * factor for v in values]


def air_model_error_pct(modeled, measured):
    """Percent error of the air model vs a measured reference. Target: <5% ok, <3% ideal, <1% OEM."""
    return 100.0 * (modeled - measured) / measured


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _selftest():
    # Density at standard-ish conditions: 100 kPa, 25 C -> ~1.169 kg/m^3
    rho = ideal_gas_density(100.0, 25.0)
    assert abs(rho - 1.1684) < 0.01, rho

    # 2.0 L, 4 cyl, VE 1.0, NA (100 kPa), 25 C, 6000 rpm.
    # per-cyl swept volume 0.5 L -> 0.0005 m^3; charge mass/cyl = 1.1684 * 0.0005 = 5.842e-4 kg
    m_cycle = sd_airmass_cycle(100.0, 25.0, 2.0, 4, 1.0)
    assert abs(m_cycle - 5.842e-4) < 1e-5, m_cycle
    # flow = m_cycle * 4 * (6000/2)/60 = m_cycle * 200 events/s
    mdot = sd_airmass_dot(6000, 100.0, 25.0, 2.0, 4, 1.0)
    assert abs(mdot - m_cycle * 200) < 1e-6, mdot

    # Boost doubles density (200 kPa) -> roughly doubles air mass
    mdot_boost = sd_airmass_dot(6000, 200.0, 25.0, 2.0, 4, 1.0)
    assert abs(mdot_boost / mdot - 2.0) < 0.001, mdot_boost / mdot

    # Fuel: stoich (lambda 1) for given air mass
    fuel = required_fuel_mass(m_cycle, 1.0)
    assert abs(fuel - m_cycle / 14.68) < 1e-9, fuel

    # Injector pressure rescale: 4 bar -> 5 bar raises flow by sqrt(5/4)
    f5 = injector_flow_at_pressure(0.0030, 5.0, 4.0)
    assert abs(f5 - 0.0030 * math.sqrt(1.25)) < 1e-9, f5

    # PW + deadtime
    pw = injector_pw(1.0e-4, 0.0030, deadtime_ms=0.9)
    assert abs(pw - ((1.0e-4 / 0.0030) * 1000 + 0.9)) < 1e-9, pw

    # Duty at 6000 rpm: window = 20 ms; 10 ms PW -> 50%
    assert abs(injector_duty(10.0, 6000) - 50.0) < 1e-9

    # BSFC sizing (Banish worked example): 500 hp, 8 cyl, bsfc 0.5, 20% cushion
    fs = fuel_system_sizing(500, 8, bsfc=0.5, duty_headroom=0.80)
    assert abs(fs["total_fuel_lb_hr"] - 250.0) < 1e-9, fs
    assert abs(fs["injector_min_lb_hr"] - 31.25) < 1e-9, fs
    assert abs(fs["injector_safe_lb_hr"] - 39.0625) < 1e-9, fs
    assert abs(fs["pump_gal_hr"] - 52.083) < 0.01, fs
    assert abs(fs["injector_safe_cc_min"] - 410.2) < 1.0, fs

    # VE correction: measured leaner than target -> VE up
    assert ve_correction(0.90, 0.92, 0.88) > 0.90
    # trims: +5% STFT +3% LTFT -> VE up by 8%
    assert abs(ve_correction_from_trims(1.0, 5.0, 3.0) - 1.08) < 1e-9

    # MAF rescale
    assert maf_rescale([10, 20], 1.1) == [11.0, 22.0]

    # error metric
    assert abs(air_model_error_pct(103, 100) - 3.0) < 1e-9
    print("airfuel.py self-test: ALL PASS")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="air/fuel model math")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--rpm", type=float)
    ap.add_argument("--map", type=float, help="MAP kPa abs")
    ap.add_argument("--iat", type=float, default=25.0)
    ap.add_argument("--disp", type=float, help="displacement L")
    ap.add_argument("--cyl", type=int, default=4)
    ap.add_argument("--ve", type=float, default=1.0)
    args = ap.parse_args()

    if args.selftest:
        _selftest()
    elif args.rpm and args.map and args.disp:
        mdot = sd_airmass_dot(args.rpm, args.map, args.iat, args.disp, args.cyl, args.ve)
        print(f"air mass flow = {mdot*1000:.2f} g/s  ({mdot:.5f} kg/s)")
        print(f"per-cyl charge = {sd_airmass_cycle(args.map, args.iat, args.disp, args.cyl, args.ve)*1e6:.1f} mg")
    else:
        ap.print_help()

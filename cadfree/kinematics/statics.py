"""Planar statics + 1-DOF mass-spring. Not Adams, not contact, not inertia of a 3-D assembly.

Four-bar / slider-crank pin forces at one pose, given applied loads or a spring.
Holding torque from ΣM. 1-DOF ωn from equivalent k and m. CadQuery does not do this.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from cadfree.physics.book import G

DISCLAIMER = (
    "Planar quasi-static pin forces (no inertia, no friction unless μ is given) "
    "plus closed-form 1-DOF mẍ + cẋ + kx. Not rigid-body dynamics, not SolidWorks Motion, "
    "not a mesh of the coil."
)

G_MUSIC = 79.3e9


def cross2(r: np.ndarray, f: np.ndarray) -> float:
    r = np.asarray(r, dtype=float).reshape(2)
    f = np.asarray(f, dtype=float).reshape(2)
    return float(r[0] * f[1] - r[1] * f[0])


def hooke_n(k_n_per_m: float, x_m: float, preload_n: float = 0.0) -> float:
    return float(k_n_per_m) * float(x_m) + float(preload_n)


def coil_rate_n_per_m(d_m: float, D_m: float, n_active: float, G_pa: float = G_MUSIC) -> float:
    """k = G d^4 / (8 D^3 n). SI."""
    d_m, D_m, n_active = float(d_m), float(D_m), max(float(n_active), 1e-9)
    return float(G_pa) * d_m**4 / (8.0 * D_m**3 * n_active)


def wahl_factor(D_m: float, d_m: float) -> float:
    c = float(D_m) / max(float(d_m), 1e-12)
    return (4.0 * c - 1.0) / (4.0 * c - 4.0) + 0.615 / c


def wahl_shear_pa(F_n: float, D_m: float, d_m: float) -> float:
    k_w = wahl_factor(D_m, d_m)
    return k_w * 8.0 * float(F_n) * float(D_m) / (math.pi * float(d_m) ** 3)


def spring_energy_j(k_n_per_m: float, x_m: float) -> float:
    return 0.5 * float(k_n_per_m) * float(x_m) ** 2


def ondof(
    m_kg: float,
    k_n_per_m: float,
    c_n_s_per_m: float = 0.0,
    *,
    include_gravity: bool = True,
) -> dict[str, Any]:
    """Closed-form 1-DOF. Not a time integration of the assembly."""
    m_kg = max(float(m_kg), 1e-12)
    k_n_per_m = float(k_n_per_m)
    c = max(float(c_n_s_per_m), 0.0)
    if k_n_per_m <= 0:
        return {
            "ok": False,
            "error": "k must be > 0 for a 1-DOF oscillator",
            "disclaimer": DISCLAIMER,
        }
    wn = math.sqrt(k_n_per_m / m_kg)
    cc = 2.0 * math.sqrt(k_n_per_m * m_kg)
    zeta = c / cc if cc else 0.0
    sag = (m_kg * G / k_n_per_m) if include_gravity else 0.0
    return {
        "ok": True,
        "m_kg": m_kg,
        "k_n_per_m": k_n_per_m,
        "c_n_s_per_m": c,
        "wn_rad_s": wn,
        "fn_hz": wn / (2.0 * math.pi),
        "zeta": zeta,
        "static_sag_m": sag,
        "settling_s_approx": (4.0 / (zeta * wn)) if zeta > 1e-6 else None,
        "regime": "overdamped" if zeta > 1 else ("critically damped" if zeta == 1 else "underdamped"),
        "disclaimer": DISCLAIMER,
        "for_model": (
            f"1-DOF ωn = {wn:.2f} rad/s ({wn / (2 * math.pi):.2f} Hz), ζ = {zeta:.3f}, "
            f"static sag {sag * 1000:.1f} mm. Not a quarter-car 2-DOF, not Adams."
        ),
    }


def slider_crank_pin_forces(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
    slide_u: np.ndarray,
    f_slider_n: float,
) -> dict[str, Any]:
    """Rod is a two-force member. F_slider is the external load on the slider along +u (N)."""
    a = np.asarray(a, dtype=float).reshape(2)
    b = np.asarray(b, dtype=float).reshape(2)
    c = np.asarray(c, dtype=float).reshape(2)
    u = np.asarray(slide_u, dtype=float).reshape(2)
    nu = float(np.linalg.norm(u))
    rod = c - b
    lr = float(np.linalg.norm(rod))
    if nu < 1e-12 or lr < 1e-12:
        return {"ok": False, "error": "slider-crank degenerates at this pose"}
    u = u / nu
    unit_cb = rod / lr
    den = float(np.dot(u, unit_cb))
    if abs(den) < 1e-6:
        return {
            "ok": False,
            "locked_force": True,
            "error": "rod is perpendicular to the slide — infinite mechanical advantage / lock",
            "disclaimer": DISCLAIMER,
        }
    lam = -float(f_slider_n) / den  # force of rod on slider, along C-B
    f_rod_on_slider = lam * unit_cb
    f_rod_on_crank = -lam * unit_cb
    r_ab = b - a
    t2 = cross2(r_ab, f_rod_on_crank)
    f_ground_on_crank = -f_rod_on_crank
    mag = abs(lam)
    return {
        "ok": True,
        "kind": "slider-crank",
        "T_hold_nm": t2,
        "F_rod_n": mag,
        "pins": {
            "A": {"F_n": float(np.linalg.norm(f_ground_on_crank)), "Fx": float(f_ground_on_crank[0]), "Fy": float(f_ground_on_crank[1])},
            "B": {"F_n": mag, "Fx": float(f_rod_on_crank[0]), "Fy": float(f_rod_on_crank[1])},
            "C": {"F_n": mag, "Fx": float(f_rod_on_slider[0]), "Fy": float(f_rod_on_slider[1])},
        },
        "max_pin_n": mag,
        "disclaimer": DISCLAIMER,
    }


def fourbar_pin_forces(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
    d: np.ndarray,
    *,
    f_at_c: tuple[float, float] | None = None,
    m_crank_extra_nm: float = 0.0,
    m_rocker_nm: float = 0.0,
) -> dict[str, Any]:
    """Unknown holding torque on the crank about A. Optional force (N) at pin C on the rocker."""
    a = np.asarray(a, dtype=float).reshape(2)
    b = np.asarray(b, dtype=float).reshape(2)
    c = np.asarray(c, dtype=float).reshape(2)
    d = np.asarray(d, dtype=float).reshape(2)
    fc = np.array(f_at_c if f_at_c is not None else (0.0, 0.0), dtype=float).reshape(2)

    # x = [F12x, F12y, F32x, F32y, F43x, F43y, F14x, F14y, T2]
    A = np.zeros((9, 9))
    rhs = np.zeros(9)
    # body 2 crank: F12 + F32 = 0 ; M_A: (B-A)×F32 + T2 + M_extra = 0
    A[0, 0] = 1.0
    A[0, 2] = 1.0
    A[1, 1] = 1.0
    A[1, 3] = 1.0
    r_ba = b - a
    A[2, 2] = -r_ba[1]
    A[2, 3] = r_ba[0]
    A[2, 8] = 1.0
    rhs[2] = -float(m_crank_extra_nm)
    # body 3 coupler: -F32 + F43 = 0 ; M_B: (C-B)×F43 = 0
    A[3, 2] = -1.0
    A[3, 4] = 1.0
    A[4, 3] = -1.0
    A[4, 5] = 1.0
    r_cb = c - b
    A[5, 4] = -r_cb[1]
    A[5, 5] = r_cb[0]
    # body 4 rocker: F14 - F43 + Fc = 0 ; M_D: (C-D)×(-F43) + (C-D)×Fc + T4 = 0
    A[6, 6] = 1.0
    A[6, 4] = -1.0
    rhs[6] = -fc[0]
    A[7, 7] = 1.0
    A[7, 5] = -1.0
    rhs[7] = -fc[1]
    r_cd = c - d
    A[8, 4] = r_cd[1]
    A[8, 5] = -r_cd[0]
    rhs[8] = -cross2(r_cd, fc) - float(m_rocker_nm)

    try:
        x = np.linalg.solve(A, rhs)
    except np.linalg.LinAlgError:
        return {"ok": False, "error": "four-bar statics is singular at this pose (toggle / lock)", "disclaimer": DISCLAIMER}

    def pin(fx: float, fy: float) -> dict[str, float]:
        return {"Fx": float(fx), "Fy": float(fy), "F_n": float(math.hypot(fx, fy))}

    pins = {
        "A": pin(x[0], x[1]),
        "B": pin(x[2], x[3]),
        "C": pin(x[4], x[5]),
        "D": pin(x[6], x[7]),
    }
    max_pin = max(p["F_n"] for p in pins.values())
    return {
        "ok": True,
        "kind": "fourbar",
        "T_hold_nm": float(x[8]),
        "pins": pins,
        "max_pin_n": max_pin,
        "F_at_c": {"Fx": float(fc[0]), "Fy": float(fc[1])},
        "disclaimer": DISCLAIMER,
    }


def pin_shear_pa(f_n: float, d_m: float) -> float:
    area = math.pi * max(float(d_m), 1e-9) ** 2 / 4.0
    return float(f_n) / area

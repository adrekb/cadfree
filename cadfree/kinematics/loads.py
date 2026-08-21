"""Project-level spring forces, pin loads, 1-DOF reduction. CadQuery does not run this."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from cadfree.cad.assembly import get_part, list_instances
from cadfree.kinematics.geometry import plane_basis, to_2d, vec3
from cadfree.kinematics.mechanism import _rest_state, list_joints, pose_at
from cadfree.kinematics.statics import (
    DISCLAIMER,
    coil_rate_n_per_m,
    fourbar_pin_forces,
    hooke_n,
    ondof,
    pin_shear_pa,
    slider_crank_pin_forces,
    spring_energy_j,
    wahl_shear_pa,
)
from cadfree.manufacturing.strength import parse_load_n
from cadfree.manufacturing.types import Check, Recommendation
from cadfree.store.db import db

ALLOW_SHEAR_PA = 800e6
PIN_ALLOW_PA = 150e6  # mild-steel pin shear order of magnitude, not a coupon


def _constraints(project_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT constraints FROM projects WHERE id = ?", (project_id,)).fetchone()
    blob = json.loads((row["constraints"] if row else None) or "{}")
    survey = dict(blob.get("survey") or {})
    return {**survey, **blob}


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _inv4(m: np.ndarray) -> np.ndarray:
    return np.linalg.inv(np.asarray(m, dtype=float))


def _xform(m_pose: np.ndarray, m_rest: np.ndarray, p: np.ndarray) -> np.ndarray:
    ph = np.array([float(p[0]), float(p[1]), float(p[2]), 1.0])
    return (np.asarray(m_pose, dtype=float) @ _inv4(m_rest) @ ph)[:3]


def _end_dict(raw: Any) -> dict[str, float]:
    if isinstance(raw, dict):
        return {
            "x": float(raw.get("x") or 0),
            "y": float(raw.get("y") or 0),
            "z": float(raw.get("z") or 0),
        }
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def spring_joints(joints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [j for j in joints if (j.get("kind") or "") in {"spring", "torsion"}]


def _spring_ends_mm(
    joint: dict[str, Any],
    rest: dict[str, Any],
    matrices: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    a_id = (joint.get("instance_a") or "").strip()
    b_id = (joint.get("instance_b") or "").strip()
    origin = joint.get("origin") or {}
    params = joint.get("params") or {}
    pa = np.array(
        [float(origin.get("x") or 0), float(origin.get("y") or 0), float(origin.get("z") or 0)],
        dtype=float,
    )
    end = _end_dict(params.get("end") or params.get("origin_b"))
    pb = np.array([end["x"], end["y"], end["z"]], dtype=float)
    if abs(pb[0]) + abs(pb[1]) + abs(pb[2]) < 1e-9:
        inst = rest["by_id"].get(b_id) or {}
        m = np.array(inst.get("matrix") or np.eye(4), dtype=float)
        pb = m[:3, 3].copy()
    ma0 = np.array((rest["by_id"].get(a_id) or {}).get("matrix") or np.eye(4), dtype=float)
    mb0 = np.array((rest["by_id"].get(b_id) or {}).get("matrix") or np.eye(4), dtype=float)
    ma1 = matrices.get(a_id, ma0) if a_id else np.eye(4)
    mb1 = matrices.get(b_id, mb0)
    if a_id:
        pa = _xform(ma1, ma0, pa)
    pb = _xform(mb1, mb0, pb)
    return pa, pb


def _relative_angle_rad(
    joint: dict[str, Any],
    rest: dict[str, Any],
    matrices: dict[str, np.ndarray],
) -> float:
    b_id = joint.get("instance_b") or ""
    inst = rest["by_id"].get(b_id)
    if not inst:
        return 0.0
    m0 = np.array(inst["matrix"], dtype=float)
    m1 = matrices.get(b_id, m0)
    axis = vec3(joint.get("axis"))
    r = m1[:3, :3] @ m0[:3, :3].T
    tmp = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    v = np.cross(axis, tmp)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return 0.0
    v = v / n
    v2 = r @ v
    return float(math.atan2(np.dot(axis, np.cross(v, v2)), np.dot(v, v2)))


def eval_force_elements(
    joints: list[dict[str, Any]],
    rest: dict[str, Any],
    posed: dict[str, Any],
) -> dict[str, Any]:
    matrices = posed.get("matrices") or {}
    elements: list[dict[str, Any]] = []
    energy = 0.0
    solids: list[dict[str, Any]] = []
    f_world: list[dict[str, Any]] = []
    m_extra_crank = 0.0
    for joint in spring_joints(joints):
        params = joint.get("params") or {}
        kind = joint.get("kind")
        if kind == "torsion":
            k = _num(params.get("k_nm_per_rad")) or 0.0
            rest_deg = _num(params.get("rest_deg")) or 0.0
            theta = _relative_angle_rad(joint, rest, matrices)
            dtheta = theta - math.radians(rest_deg)
            torque = -k * dtheta
            energy += 0.5 * k * dtheta * dtheta
            m_extra_crank += torque
            elements.append(
                {
                    "id": joint.get("id"),
                    "name": joint.get("name"),
                    "kind": "torsion",
                    "theta_deg": math.degrees(theta),
                    "T_nm": torque,
                    "k_nm_per_rad": k,
                }
            )
            continue
        k_n_per_mm = _num(params.get("k_n_per_mm"))
        d_mm = _num(params.get("d_mm"))
        D_mm = _num(params.get("D_mm"))
        n_act = _num(params.get("n_active"))
        G_pa = _num(params.get("G_pa")) or 79.3e9
        if k_n_per_mm is None and d_mm and D_mm and n_act:
            k_n_per_mm = coil_rate_n_per_m(d_mm / 1000.0, D_mm / 1000.0, n_act, G_pa) / 1000.0
        k_n_per_mm = k_n_per_mm or 0.0
        free_mm = _num(params.get("free_mm"))
        solid_mm = _num(params.get("solid_mm")) or 0.0
        preload = _num(params.get("preload_n")) or 0.0
        pa, pb = _spring_ends_mm(joint, rest, matrices)
        L = float(np.linalg.norm(pb - pa))
        if free_mm is not None:
            L0 = float(free_mm)
        else:
            rest_mats = {
                iid: np.array(info["matrix"], dtype=float) for iid, info in rest["by_id"].items()
            }
            pa0, pb0 = _spring_ends_mm(joint, rest, rest_mats)
            L0 = float(np.linalg.norm(pb0 - pa0))
        f = hooke_n(k_n_per_mm * 1000.0, (L0 - L) / 1000.0, preload)
        if L + 1e-6 < solid_mm:
            solids.append({"name": joint.get("name"), "L_mm": L, "solid_mm": solid_mm})
        energy += spring_energy_j(k_n_per_mm * 1000.0, (L0 - L) / 1000.0)
        tau = None
        if d_mm and D_mm:
            tau = wahl_shear_pa(abs(f), D_mm / 1000.0, d_mm / 1000.0)
        vec = pb - pa
        nrm = float(np.linalg.norm(vec)) or 1.0
        unit = vec / nrm
        # Force on B along A→B when in tension (L > L0, f negative with our compression sign)...
        # f > 0 compression: spring pushes ends apart. Force on B = +unit * f if unit is B-A...
        # unit = (B-A)/L. Compression: push B along +unit, push A along -unit.
        f_on_b = unit * f
        f_world.append({"instance": joint.get("instance_b"), "F": f_on_b.tolist(), "at_mm": pb.tolist()})
        elements.append(
            {
                "id": joint.get("id"),
                "name": joint.get("name"),
                "kind": "spring",
                "L_mm": L,
                "free_mm": L0,
                "solid_mm": solid_mm,
                "F_n": f,
                "k_n_per_mm": k_n_per_mm,
                "wahl_mpa": None if tau is None else tau / 1e6,
                "solid": L + 1e-6 < solid_mm,
            }
        )
    return {
        "elements": elements,
        "energy_j": energy,
        "solids": solids,
        "m_extra_nm": m_extra_crank,
        "f_world": f_world,
    }


def _pivots_2d(posed: dict[str, Any], axis: Any = "z") -> dict[str, np.ndarray] | None:
    raw = posed.get("pivots") or {}
    if not raw:
        return None
    e1, e2, k = plane_basis(vec3(axis))
    origin = np.array(raw.get("A") or [0, 0, 0], dtype=float)
    origin = origin - float(np.dot(origin, k)) * k
    out = {}
    for name, p in raw.items():
        out[name] = to_2d(np.array(p, dtype=float), origin, e1, e2)
    return out


def loads_at_pose(
    project_id: str,
    drive_deg: float = 0.0,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    constraints = constraints or _constraints(project_id)
    joints = list_joints(project_id)
    rest = _rest_state(project_id)
    posed = pose_at(project_id, drive_deg)
    springs = eval_force_elements(joints, rest, posed)
    if not posed.get("ok"):
        return {
            "ok": False,
            "drive_deg": drive_deg,
            "error": posed.get("error") or posed.get("reason") or "pose failed",
            "springs": springs,
            "disclaimer": DISCLAIMER,
        }
    f_n = parse_load_n(constraints)
    torque = _num(constraints.get("input_torque_nm"))
    pin_d_mm = _num(constraints.get("pin_d_mm"))
    kind = posed.get("kind")
    loop = posed.get("loop")
    pins_out: dict[str, Any] | None = None
    if kind == "fourbar" and posed.get("pivots"):
        p2 = _pivots_2d(posed, (loop or {}).get("j_ab", {}).get("axis") if loop else "z")
        f_at_c = None
        if f_n is not None:
            f_at_c = (0.0, -float(f_n))
        # Spring on coupler/rocker: dump net 2D force at C if we have one world force near C
        extra_m = springs.get("m_extra_nm") or 0.0
        if torque is not None:
            extra_m += float(torque)
        if p2 and {"A", "B", "C", "D"} <= set(p2):
            pins_out = fourbar_pin_forces(
                p2["A"], p2["B"], p2["C"], p2["D"], f_at_c=f_at_c, m_crank_extra_nm=extra_m
            )
            if pins_out.get("ok") and springs.get("f_world") and p2:
                # Re-solve with the first spring force applied at C as a first-order dump into the rocker.
                fw = springs["f_world"][0]["F"]
                e1, e2, k = plane_basis(vec3("z"))
                f2 = (float(np.dot(fw, e1)), float(np.dot(fw, e2)))
                if f_at_c:
                    f2 = (f2[0] + f_at_c[0], f2[1] + f_at_c[1])
                pins_out = fourbar_pin_forces(
                    p2["A"], p2["B"], p2["C"], p2["D"], f_at_c=f2, m_crank_extra_nm=extra_m
                )
    elif kind == "slider-crank" and posed.get("pivots") and f_n is not None:
        p2 = _pivots_2d(posed)
        loop = posed.get("loop") or {}
        u = vec3((loop.get("j_s") or {}).get("axis"), default=(1.0, 0.0, 0.0))
        e1, e2, _k = plane_basis(vec3("z"))
        u2 = np.array([float(np.dot(u, e1)), float(np.dot(u, e2))])
        if p2 and {"A", "B", "C"} <= set(p2):
            pins_out = slider_crank_pin_forces(p2["A"], p2["B"], p2["C"], u2, float(f_n))

    max_pin = (pins_out or {}).get("max_pin_n")
    pin_check = None
    if max_pin is not None and pin_d_mm:
        tau = pin_shear_pa(float(max_pin), pin_d_mm / 1000.0)
        pin_check = {
            "tau_mpa": tau / 1e6,
            "allow_mpa": PIN_ALLOW_PA / 1e6,
            "ok": tau <= PIN_ALLOW_PA,
            "d_mm": pin_d_mm,
        }

    wahl_fail = [
        e for e in springs.get("elements") or [] if e.get("wahl_mpa") is not None and e["wahl_mpa"] > ALLOW_SHEAR_PA / 1e6
    ]
    possible = True
    reasons: list[str] = []
    if springs.get("solids"):
        possible = False
        reasons.append(
            f"{springs['solids'][0]['name']} is on the solid height at {drive_deg:g}° "
            f"({springs['solids'][0]['L_mm']:.1f} mm < {springs['solids'][0]['solid_mm']:.1f} mm)"
        )
    if wahl_fail:
        possible = False
        reasons.append(
            f"{wahl_fail[0]['name']} Wahl shear {wahl_fail[0]['wahl_mpa']:.0f} MPa is above ~{ALLOW_SHEAR_PA/1e6:.0f} MPa music-wire order"
        )
    if pin_check and not pin_check["ok"]:
        possible = False
        reasons.append(
            f"pin shear {pin_check['tau_mpa']:.0f} MPa on Ø{pin_d_mm:g} mm exceeds ~{PIN_ALLOW_PA/1e6:.0f} MPa"
        )
    if pins_out and not pins_out.get("ok"):
        possible = False
        reasons.append(pins_out.get("error") or "statics failed at this pose")

    t_hold = (pins_out or {}).get("T_hold_nm")
    summary = (
        reasons[0]
        if reasons
        else (
            f"At {drive_deg:g}°"
            + (f" max pin {max_pin:.1f} N" if max_pin is not None else "")
            + (f", hold {t_hold:.3f} N·m" if t_hold is not None else "")
            + (f", spring energy {springs['energy_j']:.3f} J" if springs.get("elements") else "")
            + "."
        )
    )
    return {
        "ok": possible,
        "possible": possible,
        "drive_deg": drive_deg,
        "kind": kind,
        "springs": springs,
        "pins": pins_out,
        "pin_check": pin_check,
        "max_pin_n": max_pin,
        "T_hold_nm": t_hold,
        "summary": summary,
        "reasons": reasons,
        "disclaimer": DISCLAIMER,
    }


def reduce_1dof(project_id: str, constraints: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Equivalent k from ΔU/Δx or ΔT/Δθ over a short drive; m from part mass if present."""
    constraints = constraints or _constraints(project_id)
    joints = list_joints(project_id)
    if not spring_joints(joints) and _num(constraints.get("k_n_per_mm")) is None:
        return None
    a = loads_at_pose(project_id, 0.0, constraints)
    b = loads_at_pose(project_id, 5.0, constraints)
    k_mm = _num(constraints.get("k_n_per_mm"))
    m_g = 0.0
    for inst in list_instances(project_id):
        part = get_part(project_id, inst.get("part_id"))
        metrics = part.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except json.JSONDecodeError:
                metrics = {}
        # mass not always stored; skip
        _ = metrics
    m_kg = (_num(constraints.get("mass_kg")) or _num(constraints.get("max_mass_g")) or 0) 
    if m_kg and m_kg > 20:
        m_kg = m_kg / 1000.0  # likely grams in max_mass_g
    if not m_kg:
        m_kg = 0.2
    if k_mm:
        return ondof(m_kg, k_mm * 1000.0, _num(constraints.get("c_n_s_per_m")) or 0.0)
    ua = float((a.get("springs") or {}).get("energy_j") or 0)
    ub = float((b.get("springs") or {}).get("energy_j") or 0)
    dtheta = math.radians(5.0)
    k_theta = abs(2.0 * (ub - ua) / max(dtheta * dtheta, 1e-12))
    # crude I_eq from k_theta / wn^2 left unknown — report k_theta and a translational k if slider
    k_lin = k_theta  # N·m/rad
    return {
        "ok": True,
        "k_nm_per_rad": k_theta,
        "energy_j_0": ua,
        "energy_j_5": ub,
        "note": "k_eq from ½kθ² ≈ ΔU over 5°. Inertia is not the 3-D assembly; pass mass_kg for ωn.",
        "ondof": ondof(m_kg, max(k_lin, 1e-6), 0.0) if k_lin else None,
        "disclaimer": DISCLAIMER,
    }


def analyze_mechanism_loads(
    project_id: str,
    constraints: dict[str, Any] | None = None,
    degrees: list[float] | None = None,
) -> dict[str, Any]:
    constraints = constraints or _constraints(project_id)
    joints = list_joints(project_id)
    if not joints:
        return {"ok": True, "possible": True, "skipped": True, "disclaimer": DISCLAIMER}
    has_load = parse_load_n(constraints) is not None or _num(constraints.get("input_torque_nm")) is not None
    has_spring = bool(spring_joints(joints)) or spec_like_spring(constraints)
    if not has_load and not has_spring:
        return {
            "ok": True,
            "possible": True,
            "skipped": True,
            "note": "No load_n / input_torque_nm / spring joint — pin forces not scored. ask_survey.",
            "disclaimer": DISCLAIMER,
        }
    degrees = degrees or [0.0, 45.0, 90.0]
    samples = [loads_at_pose(project_id, d, constraints) for d in degrees]
    ok_samples = [s for s in samples if s.get("pins") and s["pins"].get("ok")]
    max_pin = max((s.get("max_pin_n") or 0) for s in ok_samples) if ok_samples else None
    t_hold = None
    if ok_samples:
        t_hold = max(s["T_hold_nm"] for s in ok_samples if s.get("T_hold_nm") is not None)
    fails = [s for s in samples if s.get("possible") is False]
    onedof = reduce_1dof(project_id, constraints) if has_spring else None
    possible = not fails
    reasons = []
    for s in fails:
        reasons.extend(s.get("reasons") or [s.get("summary") or ""])
    if max_pin is not None and not reasons:
        reasons.append(f"Quasi-static max pin {max_pin:.1f} N over {degrees}.")
    for_model = (
        (reasons[0] if fails else f"Pin loads at the sampled poses: max {max_pin:.1f} N." if max_pin is not None else "Spring / 1-DOF scored.")
        + " "
        + DISCLAIMER
    )
    return {
        "ok": possible,
        "possible": possible,
        "skipped": False,
        "samples": samples,
        "max_pin_n": max_pin,
        "T_hold_nm": t_hold,
        "ondof": onedof,
        "reasons": reasons,
        "for_model": for_model,
        "disclaimer": DISCLAIMER,
    }


def spec_like_spring(constraints: dict[str, Any] | None) -> bool:
    from cadfree.cots.springs import spec_fields_present, spec_from_constraints

    return spec_fields_present(spec_from_constraints(constraints))


def mechanism_load_overlay(project_id: str, constraints: dict[str, Any] | None = None) -> dict[str, Any] | None:
    report = analyze_mechanism_loads(project_id, constraints)
    if report.get("skipped"):
        return None
    checks = []
    recs = []
    if report.get("possible"):
        checks.append(
            Check(
                "pin_loads",
                "Pin loads",
                "pass",
                report["for_model"],
                details={"max_pin_n": report.get("max_pin_n"), "T_hold_nm": report.get("T_hold_nm")},
            )
        )
    else:
        checks.append(
            Check(
                "pin_loads",
                "Pin loads",
                "fail",
                report["for_model"],
                details={"reasons": report.get("reasons") or []},
            )
        )
        recs.append(Recommendation("spec", "Lower the load, thicken the pin, or pick a coil that does not solid."))
    return {
        "possible": report["possible"],
        "verdict": "feasible" if report["possible"] else "needs_spec_change",
        "summary": report["for_model"],
        "checks": [c.to_dict() for c in checks],
        "recommendations": [r.to_dict() for r in recs],
        "score": report,
        "assumptions": [DISCLAIMER],
    }

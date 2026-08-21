"""Rigid-body dynamics above planar kinematics. CadQuery does not run this.

Rung A (always): RK4 of the 1-DOF mẍ+cẋ+kx already scored by statics.ondof.
Rung B (optional): Exudyn rigid multibody if `import exudyn` works. Joints on
the assembly map to revolute/prismatic/spring. Missing Exudyn is named in one
sentence — we never invent a pin-force time history.
"""

from __future__ import annotations

import math
from typing import Any

DISCLAIMER = (
    "1-DOF RK4 is always on when k and m exist. Exudyn rigid-body DAE is the next "
    "rung when installed. Not Adams Flex, not contact penalty unless Exudyn says so, "
    "not SolidWorks Motion."
)


def probe_exudyn() -> dict[str, Any]:
    try:
        import exudyn  # noqa: F401

        ver = getattr(exudyn, "__version__", None) or getattr(exudyn, "exudynVersion", None)
        return {
            "available": True,
            "label": f"Exudyn rigid multibody ({ver or 'import ok'})",
            "version": ver,
        }
    except Exception as exc:
        return {
            "available": False,
            "label": "Exudyn not installed",
            "install_hint": "pip install exudyn  # optional: 3-D rigid-body dynamics above planar statics",
            "error": str(exc)[:300],
        }


def rk4_1dof(
    m_kg: float,
    k_n_per_m: float,
    c_n_s_per_m: float = 0.0,
    *,
    x0: float = 0.0,
    v0: float = 0.0,
    f_n: float = 0.0,
    t_end: float = 1.0,
    dt: float = 0.001,
    gravity: bool = True,
) -> dict[str, Any]:
    """Integrate mẍ + cẋ + kx = F - mg (optional). Not the assembly."""
    m = max(float(m_kg), 1e-12)
    k = float(k_n_per_m)
    c = max(float(c_n_s_per_m), 0.0)
    if k <= 0:
        return {"ok": False, "error": "k must be > 0", "disclaimer": DISCLAIMER}
    g = 9.80665 if gravity else 0.0
    force = float(f_n) - m * g

    def deriv(x: float, v: float) -> tuple[float, float]:
        return v, (force - c * v - k * x) / m

    t = 0.0
    x, v = float(x0), float(v0)
    xs = [x]
    vs = [v]
    ts = [t]
    steps = max(2, int(math.ceil(float(t_end) / max(float(dt), 1e-6))))
    h = float(t_end) / steps
    x_max = abs(x)
    v_max = abs(v)
    for _ in range(steps):
        k1x, k1v = deriv(x, v)
        k2x, k2v = deriv(x + 0.5 * h * k1x, v + 0.5 * h * k1v)
        k3x, k3v = deriv(x + 0.5 * h * k2x, v + 0.5 * h * k2v)
        k4x, k4v = deriv(x + h * k3x, v + h * k3v)
        x = x + (h / 6.0) * (k1x + 2 * k2x + 2 * k3x + k4x)
        v = v + (h / 6.0) * (k1v + 2 * k2v + 2 * k3v + k4v)
        t += h
        xs.append(x)
        vs.append(v)
        ts.append(t)
        x_max = max(x_max, abs(x))
        v_max = max(v_max, abs(v))
    # Keep a compact trace (≤ 200 samples) for the studio.
    stride = max(1, len(ts) // 200)
    trace = [{"t": ts[i], "x": xs[i], "v": vs[i]} for i in range(0, len(ts), stride)]
    wn = math.sqrt(k / m)
    return {
        "ok": True,
        "solver": "rk4_1dof",
        "m_kg": m,
        "k_n_per_m": k,
        "c_n_s_per_m": c,
        "t_end_s": float(t_end),
        "x_max_m": x_max,
        "v_max_ms": v_max,
        "x_end_m": xs[-1],
        "wn_rad_s": wn,
        "samples": trace,
        "disclaimer": DISCLAIMER,
        "for_model": (
            f"1-DOF RK4 |x|_max = {x_max * 1000:.2f} mm over {t_end:g} s "
            f"(ωn = {wn:.2f} rad/s). Not the 3-D assembly."
        ),
    }


def _instance_size_m(inst: dict[str, Any], parts_by_id: dict[str, Any]) -> list[float]:
    part = parts_by_id.get(inst.get("part_id") or "") or {}
    metrics = part.get("metrics") or {}
    if isinstance(metrics, str):
        import json

        try:
            metrics = json.loads(metrics)
        except json.JSONDecodeError:
            metrics = {}
    bbox = metrics.get("bbox_mm") or [40.0, 10.0, 10.0]
    return [max(float(bbox[i] if i < len(bbox) else 10.0) * 0.001, 0.002) for i in range(3)]


def _origin_m(joint: dict[str, Any]) -> list[float]:
    o = joint.get("origin") or {}
    return [float(o.get(k) or 0.0) * 0.001 for k in ("x", "y", "z")]


def _axis(joint: dict[str, Any]) -> list[float]:
    a = joint.get("axis") or {}
    vec = [float(a.get(k) or 0.0) for k in ("x", "y", "z")]
    n = math.sqrt(sum(c * c for c in vec)) or 1.0
    return [c / n for c in vec]


def run_exudyn(project_id: str, *, t_end: float = 0.5, step: float = 1e-3) -> dict[str, Any]:
    probe = probe_exudyn()
    if not probe["available"]:
        return {
            "ok": False,
            "solver": "exudyn",
            "probe": probe,
            "error": probe.get("install_hint"),
            "disclaimer": DISCLAIMER,
        }
    try:
        import exudyn as exu
        from exudyn.utilities import InertiaCuboid  # type: ignore

        from cadfree.cad.assembly import list_instances, list_parts
        from cadfree.kinematics.mechanism import list_joints
    except Exception as exc:
        return {
            "ok": False,
            "solver": "exudyn",
            "probe": probe,
            "error": f"Exudyn imported but utilities failed: {exc}",
            "disclaimer": DISCLAIMER,
        }

    joints = list_joints(project_id)
    instances = list_instances(project_id)
    if not joints or not instances:
        return {
            "ok": False,
            "solver": "exudyn",
            "probe": probe,
            "error": "Need joints and instances. define_joint first. CadQuery will not move a linkage.",
            "disclaimer": DISCLAIMER,
        }
    parts = {p["id"]: p for p in list_parts(project_id)}
    SC = exu.SystemContainer()
    mbs = SC.AddSystem()
    bodies: dict[str, Any] = {}
    notes: list[str] = []
    try:
        ground = mbs.CreateGround(referencePosition=[0, 0, 0])
        bodies[""] = ground
        bodies["ground"] = ground
        for inst in instances:
            iid = inst.get("id") or ""
            loc = inst.get("loc") or {}
            pos = [float(loc.get(k) or 0.0) * 0.001 for k in ("x", "y", "z")]
            side = _instance_size_m(inst, parts)
            inertia = InertiaCuboid(density=1000.0, sideLengths=side)
            body = mbs.CreateRigidBody(
                inertia=inertia,
                referencePosition=pos,
                gravity=[0, 0, -9.81],
            )
            bodies[iid] = body
        for joint in joints:
            kind = (joint.get("kind") or "revolute").lower()
            a = bodies.get((joint.get("instance_a") or "").strip() or "ground")
            b = bodies.get((joint.get("instance_b") or "").strip())
            if a is None or b is None:
                notes.append(f"skipped joint {joint.get('name')}: missing body")
                continue
            pos = _origin_m(joint)
            ax = _axis(joint)
            if kind in {"revolute", "gear"}:
                mbs.CreateRevoluteJoint(bodyNumbers=[a, b], position=pos, axis=ax)
            elif kind == "prismatic":
                mbs.CreatePrismaticJoint(bodyNumbers=[a, b], position=pos, axis=ax)
            elif kind == "fixed":
                mbs.CreateGenericJoint(bodyNumbers=[a, b], position=pos, constrainedAxes=[1, 1, 1, 1, 1, 1])
            elif kind in {"spring", "torsion"}:
                params = joint.get("params") or {}
                k = float(params.get("k_n_per_m") or params.get("k") or 1000.0)
                try:
                    mbs.CreateSpringDamper(bodyNumbers=[a, b], stiffness=k, damping=0.0)
                except Exception:
                    notes.append(f"spring {joint.get('name')} not mapped — Exudyn CreateSpringDamper rejected")
            else:
                notes.append(f"joint kind {kind} not mapped")
        mbs.Assemble()
        sims = exu.SimulationSettings()
        sims.timeIntegration.endTime = float(t_end)
        sims.timeIntegration.numberOfSteps = max(10, int(float(t_end) / max(float(step), 1e-5)))
        sims.timeIntegration.verboseMode = 0
        mbs.SolveDynamic(sims)
        n = mbs.systemData.NumberOfCoordinates() if hasattr(mbs, "systemData") else None
        return {
            "ok": True,
            "solver": "exudyn",
            "probe": probe,
            "t_end_s": float(t_end),
            "n_coordinates": n,
            "n_bodies": len(instances),
            "n_joints": len(joints),
            "notes": notes,
            "disclaimer": DISCLAIMER,
            "for_model": (
                f"Exudyn rigid-body dynamic of {len(instances)} bodies / {len(joints)} joints "
                f"to t={t_end:g} s. Inertia is bbox cuboids at 1000 kg/m³, not the mesh density. "
                "Not Adams Flex."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "solver": "exudyn",
            "probe": probe,
            "error": f"Exudyn model failed: {exc}",
            "notes": notes,
            "disclaimer": DISCLAIMER,
        }


def analyze_mechanism_dynamics(
    project_id: str,
    *,
    t_end: float = 0.5,
) -> dict[str, Any]:
    from cadfree.kinematics.loads import reduce_1dof, _constraints

    constraints = _constraints(project_id)
    onedof = reduce_1dof(project_id, constraints)
    rk = None
    if onedof and onedof.get("ok") and onedof.get("k_n_per_m"):
        rk = rk4_1dof(
            float(onedof.get("m_kg") or 0.2),
            float(onedof["k_n_per_m"]),
            float(onedof.get("c_n_s_per_m") or 0.0),
            t_end=t_end,
        )
    elif onedof and onedof.get("ondof") and onedof["ondof"].get("ok"):
        inner = onedof["ondof"]
        rk = rk4_1dof(
            float(inner.get("m_kg") or 0.2),
            float(inner.get("k_n_per_m") or 0.0),
            float(inner.get("c_n_s_per_m") or 0.0),
            t_end=t_end,
        )
    exudyn = run_exudyn(project_id, t_end=t_end)
    return {
        "ok": bool((rk and rk.get("ok")) or exudyn.get("ok")),
        "kind": "dynamics",
        "rk4_1dof": rk,
        "exudyn": exudyn,
        "disclaimer": DISCLAIMER,
        "for_model": (exudyn.get("for_model") if exudyn.get("ok") else None)
        or (rk.get("for_model") if rk else "No k/m for 1-DOF and Exudyn did not run."),
    }

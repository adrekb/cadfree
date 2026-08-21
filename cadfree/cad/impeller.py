"""Parametric centrifugal impeller. CadQuery authors the solid; solvers do physics.

Blade meanline follows β from the tangential: dθ = dr / (r tan β).
PARAMS changes rebuild the solid. This is not a pump curve, not CFD, not a
periodic sector, and not TurboGrid.
"""

from __future__ import annotations

from typing import Any

from cadfree.cad.params import apply_params

DEFAULT_PARAMS: dict[str, Any] = {
    "r1_mm": 18.0,
    "r2_mm": 50.0,
    "b1_mm": 12.0,
    "b2_mm": 8.0,
    "beta1_deg": 35.0,
    "beta2_deg": 25.0,
    "n_blades": 6,
    "hub_r_mm": 10.0,
    "hub_h_mm": 16.0,
    "plate_t_mm": 4.0,
    "blade_t_mm": 2.5,
    "bore_d_mm": 8.0,
}

STARTER_IMPELLER = '''\
"""Centrifugal impeller. PARAMS drive the solid; β is from the tangential.

Meanline: dθ = dr / (r tan β), β interpolated from beta1_deg to beta2_deg.
CadQuery only authors geometry. Euler head / Wiesner / hoop live in pack=turbo.
Not a measured pump curve, not CFD, not OpenRadioss burst.
"""
import math
import cadquery as cq

PARAMS = {
    "r1_mm": 18.0,
    "r2_mm": 50.0,
    "b1_mm": 12.0,
    "b2_mm": 8.0,
    "beta1_deg": 35.0,
    "beta2_deg": 25.0,
    "n_blades": 6,
    "hub_r_mm": 10.0,
    "hub_h_mm": 16.0,
    "plate_t_mm": 4.0,
    "blade_t_mm": 2.5,
    "bore_d_mm": 8.0,
}

p = PARAMS
n_blades = max(3, int(round(float(p["n_blades"]))))
r1 = float(p["r1_mm"])
r2 = float(p["r2_mm"])
beta1 = math.radians(float(p["beta1_deg"]))
beta2 = math.radians(float(p["beta2_deg"]))
nseg = 16
pts = []
theta = 0.0
prev_r = r1
for i in range(nseg + 1):
    t = i / nseg
    r = r1 + (r2 - r1) * t
    beta = beta1 + (beta2 - beta1) * t
    tb = math.tan(beta)
    if abs(tb) < 1e-6:
        tb = 1e-6
    if i > 0:
        theta += (r - prev_r) / (max(r, 1e-6) * tb)
    pts.append((r * math.cos(theta), r * math.sin(theta)))
    prev_r = r

half = float(p["blade_t_mm"]) / 2.0
left = []
right = []
for i, (x, y) in enumerate(pts):
    if i == 0:
        dx, dy = pts[1][0] - x, pts[1][1] - y
    elif i == len(pts) - 1:
        dx, dy = x - pts[-2][0], y - pts[-2][1]
    else:
        dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    left.append((x + nx * half, y + ny * half))
    right.append((x - nx * half, y - ny * half))
poly = left + list(reversed(right))
blade_h = max(float(p["b2_mm"]), 1.0)
one = cq.Workplane("XY").polyline(poly).close().extrude(blade_h)

plate = cq.Workplane("XY").circle(r2).extrude(float(p["plate_t_mm"]))
hub = cq.Workplane("XY").circle(float(p["hub_r_mm"])).extrude(float(p["hub_h_mm"]))
result = plate.union(hub)
for i in range(n_blades):
    result = result.union(one.rotate((0, 0, 0), (0, 0, 1), 360.0 * i / n_blades))
bore = float(p["bore_d_mm"])
if bore > 0:
    result = result.cut(cq.Workplane("XY").circle(bore / 2.0).extrude(float(p["hub_h_mm"]) + 1.0))
'''


def impeller_source(params: dict[str, Any] | None = None) -> str:
    src = STARTER_IMPELLER
    if params:
        src = apply_params(src, params)
    return src

"""Handbook formulas. The LLM does not invent μ, C_d, or viscosity.

Every entry is SI. CadQuery millimetres are converted in snapshot.py before
these see a number. Not CFD, not contact dynamics, not a coupon test.
"""

from __future__ import annotations

from typing import Any

FRICTION_PAIRS: dict[str, float] = {
    "dry_steel_steel": 0.6,
    "greased_steel": 0.12,
    "nylon_steel": 0.3,
    "ptfe_steel": 0.05,
    "acetal_steel": 0.2,
    "rubber_dry": 0.8,
    "wood_steel": 0.4,
}

FLUIDS: dict[str, dict[str, float]] = {
    "air": {"rho": 1.225, "mu_visc": 1.81e-5, "label": "air 15 °C, 1 atm"},
    "water": {"rho": 997.0, "mu_visc": 1.0e-3, "label": "water 20 °C"},
    "oil_iso32": {"rho": 870.0, "mu_visc": 0.028, "label": "ISO VG 32 hydraulic oil, ~40 °C typical"},
}

G = 9.80665
SIGMA_SB = 5.670374419e-8
H_STILL_AIR = 10.0  # W/(m²·K), named still-air film; not a CFD h.


def _f(
    *,
    id: str,
    domain: str,
    title: str,
    latex: str,
    expr: str,
    output: str,
    unit: str,
    variables: dict[str, dict[str, Any]],
    disclaimer: str,
    maintain: str = "",
    tags: tuple[str, ...] = (),
    source: str = "Shigley / Fox / first-order handbook",
) -> dict[str, Any]:
    return {
        "id": id,
        "domain": domain,
        "title": title,
        "latex": latex,
        "expr": expr,
        "output": output,
        "unit": unit,
        "variables": variables,
        "disclaimer": disclaimer,
        "maintain": maintain,
        "tags": list(tags),
        "source": source,
    }


def _var(latex: str, unit: str, label: str, **extra: Any) -> dict[str, Any]:
    out = {"latex": latex, "unit": unit, "label": label}
    out.update(extra)
    return out


FORMULAS: list[dict[str, Any]] = [
    _f(
        id="coulomb_friction",
        domain="friction",
        title="Coulomb sliding friction",
        latex=r"F_f = \mu F_N",
        expr="mu * F_N",
        output="F_f",
        unit="N",
        variables={
            "mu": _var(r"\mu", "1", "friction coefficient"),
            "F_N": _var(r"F_N", "N", "normal load"),
        },
        disclaimer="Amontons–Coulomb. Not Stribeck, not EHL, not stick-slip.",
        maintain="If dry μ is high and heat follows, grease or a bushing. Do not guess μ — use pair= from the book or a datasheet.",
        tags=("friction", "maintain", "brake", "slide"),
    ),
    _f(
        id="friction_power",
        domain="friction",
        title="Friction heating / power",
        latex=r"P = F_f v",
        expr="F_f * v",
        output="P",
        unit="W",
        variables={
            "F_f": _var(r"F_f", "N", "friction force"),
            "v": _var(r"v", "m/s", "sliding speed"),
        },
        disclaimer="Instantaneous P = F v. Not a thermal-network transient.",
        maintain="If P is more than a few watts in a small plastic bushing, it will glaze. Relube, enlarge area, or switch to rolling.",
        tags=("friction", "heat", "maintain"),
    ),
    _f(
        id="pv_bushing",
        domain="friction",
        title="Plain-bearing PV",
        latex=r"p = F_N / A,\quad \mathrm{PV} = p\,v",
        expr="(F_N / A) * v",
        output="PV",
        unit="Pa·m/s",
        variables={
            "F_N": _var(r"F_N", "N", "radial load"),
            "A": _var(r"A", "m^2", "projected area (D × L for a journal)"),
            "v": _var(r"v", "m/s", "surface speed"),
        },
        disclaimer="First-order PV. Limit is a typical catalog number, not this bushing's coupon.",
        maintain="Stay under the material PV_limit. Grease at assembly; relube sooner as PV approaches the limit.",
        tags=("bushing", "bearing", "maintain", "pv"),
    ),
    _f(
        id="archard_wear",
        domain="friction",
        title="Archard wear volume",
        latex=r"V_w = k F_N s / H",
        expr="k_wear * F_N * s / H",
        output="V_w",
        unit="m^3",
        variables={
            "k_wear": _var(r"k", "1", "Archard wear coefficient", default=1e-5),
            "F_N": _var(r"F_N", "N", "normal load"),
            "s": _var(r"s", "m", "sliding distance"),
            "H": _var(r"H", "Pa", "hardness (pressure)", default=1e8),
        },
        disclaimer="Archard is an order-of-magnitude wear estimate. k varies by decades.",
        maintain="Convert V_w to a diametral loss on the journal and set an inspect-before-that interval.",
        tags=("wear", "maintain"),
    ),
    _f(
        id="capstan",
        domain="friction",
        title="Capstan / belt friction",
        latex=r"T_\mathrm{hold} / T_\mathrm{load} = e^{\mu \theta}",
        expr="exp(mu * theta)",
        output="ratio",
        unit="1",
        variables={
            "mu": _var(r"\mu", "1", "friction coefficient"),
            "theta": _var(r"\theta", "rad", "wrap angle"),
        },
        disclaimer="Ideal capstan. No belt stiffness, no pulley inertia.",
        tags=("belt", "friction"),
    ),
    _f(
        id="screw_self_lock",
        domain="friction",
        title="Power-screw self-locking",
        latex=r"\lambda = \arctan(l / (\pi d_m)),\quad \text{locks if }\lambda < \arctan\mu",
        expr="(atan(lead / (pi * d_m)) < atan(mu)) * 1.0",
        output="self_lock",
        unit="1",
        variables={
            "lead": _var(r"l", "m", "lead per turn"),
            "d_m": _var(r"d_m", "m", "mean thread diameter"),
            "mu": _var(r"\mu", "1", "thread friction"),
        },
        disclaimer="Square-thread first-order. Acme/buttress need the thread-angle term.",
        maintain="If it does not self-lock, the lift needs a brake or a non-backdriving ratio.",
        tags=("screw", "jack", "maintain"),
    ),
    _f(
        id="reynolds",
        domain="fluids",
        title="Reynolds number",
        latex=r"Re = \rho v D / \mu",
        expr="rho * v * D / mu_visc",
        output="Re",
        unit="1",
        variables={
            "rho": _var(r"\rho", "kg/m^3", "density"),
            "v": _var(r"v", "m/s", "speed"),
            "D": _var(r"D", "m", "characteristic length / diameter"),
            "mu_visc": _var(r"\mu", "Pa·s", "dynamic viscosity"),
        },
        disclaimer="Pipe/plate first-order. Transition is not a single number for every geometry.",
        maintain="Re < 2300 laminar in a pipe; Re > 4000 turbulent — filters and fittings see more loss.",
        tags=("fluids", "aero", "pipe"),
    ),
    _f(
        id="hagen_poiseuille",
        domain="fluids",
        title="Hagen–Poiseuille pipe ΔP (laminar)",
        latex=r"\Delta p = 8 \mu L Q / (\pi r^4)",
        expr="8 * mu_visc * L * Q / (pi * r**4)",
        output="dp",
        unit="Pa",
        variables={
            "mu_visc": _var(r"\mu", "Pa·s", "viscosity"),
            "L": _var(r"L", "m", "length"),
            "Q": _var(r"Q", "m^3/s", "volume flow"),
            "r": _var(r"r", "m", "radius"),
        },
        disclaimer="Laminar, circular, fully developed. Invalid when Re is turbulent.",
        tags=("fluids", "pipe"),
    ),
    _f(
        id="darcy_weisbach",
        domain="fluids",
        title="Darcy–Weisbach head loss",
        latex=r"h_f = f \frac{L}{D}\frac{v^2}{2g}",
        expr="f * (L / D) * (v**2) / (2 * g)",
        output="h_f",
        unit="m",
        variables={
            "f": _var(r"f", "1", "friction factor"),
            "L": _var(r"L", "m", "length"),
            "D": _var(r"D", "m", "diameter"),
            "v": _var(r"v", "m/s", "mean speed"),
            "g": _var(r"g", "m/s^2", "gravity", default=G),
        },
        disclaimer="Needs a friction factor (64/Re laminar, Blasius turbulent-smooth). Not a 3D CFD field.",
        tags=("fluids", "pipe"),
    ),
    _f(
        id="orifice",
        domain="fluids",
        title="Orifice / discharge",
        latex=r"Q = C_d A \sqrt{2\Delta p / \rho}",
        expr="Cd * A * sqrt(2 * dp / rho)",
        output="Q",
        unit="m^3/s",
        variables={
            "Cd": _var(r"C_d", "1", "discharge coefficient", default=0.62),
            "A": _var(r"A", "m^2", "orifice area"),
            "dp": _var(r"\Delta p", "Pa", "pressure drop"),
            "rho": _var(r"\rho", "kg/m^3", "density"),
        },
        disclaimer="Incompressible orifice. C_d is geometry-specific.",
        tags=("fluids", "orifice"),
    ),
    _f(
        id="dynamic_pressure",
        domain="aero",
        title="Dynamic pressure",
        latex=r"q = \tfrac{1}{2} \rho v^2",
        expr="0.5 * rho * v**2",
        output="q",
        unit="Pa",
        variables={
            "rho": _var(r"\rho", "kg/m^3", "density"),
            "v": _var(r"v", "m/s", "speed"),
        },
        disclaimer="Incompressible q. Say so if Mach is not << 0.3.",
        tags=("aero", "fluids"),
    ),
    _f(
        id="drag_force",
        domain="aero",
        title="Drag force",
        latex=r"F_D = \tfrac{1}{2} \rho v^2 C_D A",
        expr="0.5 * rho * v**2 * Cd * A",
        output="F_D",
        unit="N",
        variables={
            "rho": _var(r"\rho", "kg/m^3", "density"),
            "v": _var(r"v", "m/s", "speed"),
            "Cd": _var(r"C_D", "1", "drag coefficient", default=1.0),
            "A": _var(r"A", "m^2", "projected area"),
        },
        disclaimer="Single C_D, one projected area. Not a RANS/LES field, not stall.",
        tags=("aero", "fluids", "drag"),
    ),
    _f(
        id="aero_power",
        domain="aero",
        title="Propulsive power against drag",
        latex=r"P = F_D v",
        expr="F_D * v",
        output="P",
        unit="W",
        variables={
            "F_D": _var(r"F_D", "N", "drag"),
            "v": _var(r"v", "m/s", "speed"),
        },
        disclaimer="Steady P = F v. Not a propeller map.",
        tags=("aero", "power"),
    ),
    _f(
        id="momentum_hover",
        domain="aero",
        title="Ideal momentum-theory hover power",
        latex=r"P = T^{3/2} / \sqrt{2 \rho A}",
        expr="T**1.5 / (2 * rho * A)**0.5",
        output="P",
        unit="W",
        variables={
            "T": _var(r"T", "N", "total thrust (= weight in hover)"),
            "rho": _var(r"\rho", "kg/m^3", "air density", default=1.225),
            "A": _var(r"A", "m^2", "total rotor disk area"),
        },
        disclaimer="Actuator-disk hover. Not a propeller map, not induced-power with figure of merit, not forward flight.",
        tags=("aero", "multirotor", "drone", "hover"),
        source="Leishman / first-order momentum theory",
    ),
    _f(
        id="disk_area",
        domain="aero",
        title="n-rotor disk area",
        latex=r"A = n \pi (d/2)^2",
        expr="n * pi * (d / 2)**2",
        output="A",
        unit="m^2",
        variables={
            "n": _var(r"n", "1", "rotor count", default=4),
            "d": _var(r"d", "m", "propeller diameter"),
        },
        disclaimer="Geometric disk. Tip loss and ducting are not this formula.",
        tags=("aero", "multirotor", "drone"),
        source="geometry",
    ),
    _f(
        id="thrust_weight",
        domain="aero",
        title="Thrust-to-weight",
        latex=r"T/W = T / (m g)",
        expr="T / (m * g)",
        output="tw",
        unit="1",
        variables={
            "T": _var(r"T", "N", "total maximum thrust"),
            "m": _var(r"m", "kg", "all-up mass"),
            "g": _var(r"g", "m/s^2", "gravity", default=G),
        },
        disclaimer="Peak static T/W. Not a control-margin or prop-inflow model.",
        tags=("aero", "multirotor", "drone"),
        source="first-order",
    ),
    _f(
        id="quad_vmax",
        domain="aero",
        title="Quad max speed from leftover thrust vs drag",
        latex=r"v = \sqrt{2 \sqrt{T^2 - (mg)^2} / (\rho C_D A)}",
        expr="(2 * ((T**2 - (m * g)**2)**0.5) / (rho * Cd * A))**0.5",
        output="v",
        unit="m/s",
        variables={
            "T": _var(r"T", "N", "total maximum thrust"),
            "m": _var(r"m", "kg", "all-up mass"),
            "g": _var(r"g", "m/s^2", "gravity", default=G),
            "rho": _var(r"\rho", "kg/m^3", "air density", default=1.225),
            "Cd": _var(r"C_D", "1", "drag coefficient", default=1.0),
            "A": _var(r"A", "m^2", "frontal area"),
        },
        disclaimer="F_fwd = sqrt(T² − W²), then v = sqrt(2 F / (ρ Cd A)). Needs T > W. Not a propeller map, not stall.",
        tags=("aero", "multirotor", "drone", "drag"),
        source="first-order excess thrust",
    ),
    _f(
        id="cantilever_stress",
        domain="solids",
        title="Cantilever bending stress",
        latex=r"\sigma = \frac{6 F_N L}{b t^2}",
        expr="6 * F_N * L / (b * t**2)",
        output="sigma",
        unit="Pa",
        variables={
            "F_N": _var(r"F_N", "N", "tip load"),
            "L": _var(r"L", "m", "span"),
            "b": _var(r"b", "m", "width"),
            "t": _var(r"t", "m", "thickness"),
        },
        disclaimer="Rectangle cantilever, M c / I. Not mesh FEA. Infill/knockdowns live in the first-order rung.",
        tags=("beam", "strength"),
    ),
    _f(
        id="cantilever_deflection",
        domain="solids",
        title="Cantilever tip deflection",
        latex=r"\delta = \frac{F_N L^3}{3 E I},\quad I = b t^3 / 12",
        expr="F_N * L**3 / (3 * E * (b * t**3 / 12))",
        output="delta",
        unit="m",
        variables={
            "F_N": _var(r"F_N", "N", "tip load"),
            "L": _var(r"L", "m", "span"),
            "E": _var(r"E", "Pa", "modulus"),
            "b": _var(r"b", "m", "width"),
            "t": _var(r"t", "m", "thickness"),
        },
        disclaimer="Euler–Bernoulli. Not shear deflection, not FDM anisotropy.",
        tags=("beam", "stiffness"),
    ),
    _f(
        id="shaft_torsion",
        domain="solids",
        title="Round-shaft torsion",
        latex=r"\tau = T r / J,\quad J = \pi d^4 / 32",
        expr="T * (D / 2) / (pi * D**4 / 32)",
        output="tau",
        unit="Pa",
        variables={
            "T": _var(r"T", "N·m", "torque"),
            "D": _var(r"D", "m", "diameter"),
        },
        disclaimer="Solid circular shaft. Keyways and hollow sections are not this formula.",
        tags=("shaft", "torsion"),
    ),
    _f(
        id="bearing_L10",
        domain="maintenance",
        title="Rolling-bearing L10 life",
        latex=r"L_{10} = (C / P)^p \times 10^6 \text{ rev}",
        expr="(C / P)**p * 1e6",
        output="L10_rev",
        unit="rev",
        variables={
            "C": _var(r"C", "N", "basic dynamic load rating"),
            "P": _var(r"P", "N", "equivalent load"),
            "p": _var(r"p", "1", "3 ball / 10/3 roller", default=3),
        },
        disclaimer="ISO 281 sketch. No contamination, lubrication, or temperature factors.",
        maintain="Hours = L10_rev / (60 n_rpm). Replace on that order, not 'when it sounds bad'.",
        tags=("bearing", "maintain"),
    ),
    _f(
        id="hooke_spring",
        domain="spring",
        title="Hooke spring force",
        latex=r"F = k x",
        expr="k * x",
        output="F",
        unit="N",
        variables={
            "k": _var(r"k", "N/m", "rate"),
            "x": _var(r"x", "m", "deflection from free length"),
        },
        disclaimer="Linear coil. Not a progressive rate, not a printed TPU gyroid.",
        tags=("spring", "latch", "suspension"),
    ),
    _f(
        id="coil_rate",
        domain="spring",
        title="Round-wire compression/extension rate",
        latex=r"k = G d^4 / (8 D^3 n)",
        expr="G * d**4 / (8 * D**3 * n)",
        output="k",
        unit="N/m",
        variables={
            "G": _var(r"G", "Pa", "shear modulus", default=79.3e9),
            "d": _var(r"d", "m", "wire diameter"),
            "D": _var(r"D", "m", "mean coil diameter"),
            "n": _var(r"n", "1", "active coils"),
        },
        disclaimer="Wahl-free rate. Ends, pitch, and a rectangular wire are not this formula. G defaults to music wire 79.3 GPa.",
        tags=("spring", "coil"),
        source="Shigley coil spring",
    ),
    _f(
        id="wahl_stress",
        domain="spring",
        title="Wahl shear in a coil",
        latex=r"\tau = K \frac{8 F D}{\pi d^3},\quad K=\frac{4C-1}{4C-4}+\frac{0.615}{C},\quad C=D/d",
        expr="((4*(D/d)-1)/(4*(D/d)-4) + 0.615/(D/d)) * 8 * F * D / (pi * d**3)",
        output="tau",
        unit="Pa",
        variables={
            "F": _var(r"F", "N", "axial load"),
            "D": _var(r"D", "m", "mean coil diameter"),
            "d": _var(r"d", "m", "wire diameter"),
        },
        disclaimer="Static Wahl correction. Not fatigue, not shot-peened life, not a 3-D coil mesh.",
        tags=("spring", "stress"),
        source="Shigley / Wahl",
    ),
    _f(
        id="spring_energy",
        domain="spring",
        title="Spring energy",
        latex=r"U = \tfrac{1}{2} k x^2",
        expr="0.5 * k * x**2",
        output="U",
        unit="J",
        variables={
            "k": _var(r"k", "N/m", "rate"),
            "x": _var(r"x", "m", "deflection"),
        },
        disclaimer="Linear spring. Latch work is ΔU, not a Motion energy plot.",
        tags=("spring", "latch", "energy"),
    ),
    _f(
        id="mass_spring_wn",
        domain="spring",
        title="1-DOF natural frequency",
        latex=r"\omega_n = \sqrt{k/m}",
        expr="sqrt(k / m)",
        output="wn",
        unit="rad/s",
        variables={
            "k": _var(r"k", "N/m", "equivalent rate"),
            "m": _var(r"m", "kg", "equivalent mass"),
        },
        disclaimer="One DOF. Not a quarter-car (2 DOF), not the full linkage inertia matrix.",
        tags=("spring", "suspension", "dynamics"),
    ),
    _f(
        id="pin_shear",
        domain="solids",
        title="Pin shear stress",
        latex=r"\tau = F / (\pi d^2 / 4)",
        expr="F / (pi * d**2 / 4)",
        output="tau",
        unit="Pa",
        variables={
            "F": _var(r"F", "N", "pin force"),
            "d": _var(r"d", "m", "pin diameter"),
        },
        disclaimer="Single shear, uniform. Double shear halves this. Not contact FEA of the hole.",
        tags=("pin", "linkage", "stress"),
    ),
    _f(
        id="fit_clearance",
        domain="solids",
        title="Pin/hole clearance after process shrink",
        latex=r"c = D_{\mathrm{hole}} - d_{\mathrm{pin}} - s",
        expr="D_hole - d_pin - shrink",
        output="c",
        unit="m",
        variables={
            "D_hole": _var(r"D_{\mathrm{hole}}", "m", "nominal hole diameter"),
            "d_pin": _var(r"d_{\mathrm{pin}}", "m", "nominal pin diameter"),
            "shrink": _var(r"s", "m", "hole undersize (negative = kerf oversize)", default=0.0),
        },
        disclaimer=(
            "Nominal diameters minus first-order process shrink. ISO 286 envelopes live in "
            "the fits catalog, not this one line. Not a CMM."
        ),
        tags=("fit", "iso", "pin", "hole"),
        source="ISO 286 family / shop shrink rule of thumb",
    ),
    _f(
        id="stackup_wc",
        domain="solids",
        title="Worst-case tolerance stack",
        latex=r"T_{\mathrm{wc}} = t_1 + t_2 + t_3",
        expr="t1 + t2 + t3",
        output="T_wc",
        unit="m",
        variables={
            "t1": _var(r"t_1", "m", "full band on dim 1 (plus+minus)"),
            "t2": _var(r"t_2", "m", "full band on dim 2", default=0.0),
            "t3": _var(r"t_3", "m", "full band on dim 3", default=0.0),
        },
        disclaimer="Arithmetic sum of bilateral bands. Not RSS, not GD&T, not a Monte Carlo.",
        tags=("fit", "stackup", "tolerance"),
        source="Shop stackup, first-order",
    ),
    _f(
        id="stackup_rss",
        domain="solids",
        title="RSS tolerance stack",
        latex=r"T_{\mathrm{rss}} = \sqrt{t_1^2 + t_2^2 + t_3^2}",
        expr="sqrt(t1**2 + t2**2 + t3**2)",
        output="T_rss",
        unit="m",
        variables={
            "t1": _var(r"t_1", "m", "full band on dim 1 (plus+minus)"),
            "t2": _var(r"t_2", "m", "full band on dim 2", default=0.0),
            "t3": _var(r"t_3", "m", "full band on dim 3", default=0.0),
        },
        disclaimer="sqrt(Σ t_i²) on full plus+minus bands. Not a statistical Cpk, not GD&T.",
        tags=("fit", "stackup", "tolerance"),
        source="Shop stackup, first-order",
    ),
    _f(
        id="conduction",
        domain="heat",
        title="1-D conduction",
        latex=r"\dot{Q} = k A \Delta T / L",
        expr="k * A * dT / L",
        output="Qdot",
        unit="W",
        variables={
            "k": _var(r"k", "W/(m·K)", "conductivity"),
            "A": _var(r"A", "m^2", "area"),
            "dT": _var(r"\Delta T", "K", "temperature drop"),
            "L": _var(r"L", "m", "thickness"),
        },
        disclaimer="Plane wall, steady. Not a 3D thermal FEA.",
        tags=("heat",),
    ),
    _f(
        id="newton_cooling",
        domain="heat",
        title="Newton film convection",
        latex=r"\dot{Q} = h A (T - T_{\infty})",
        expr="h * A * (T - T_inf)",
        output="Qdot",
        unit="W",
        variables={
            "h": _var(r"h", "W/(m^2·K)", "film coefficient", default=H_STILL_AIR),
            "A": _var(r"A", "m^2", "surface area"),
            "T": _var(r"T", "K", "surface temperature"),
            "T_inf": _var(r"T_{\infty}", "K", "sink temperature"),
        },
        disclaimer="Newton's law of cooling. h defaults to 10 W/(m²·K) still air. Not a CFD film, not boiling.",
        tags=("heat", "convection"),
        source="Incropera, first-order",
    ),
    _f(
        id="radiation_net",
        domain="heat",
        title="Net gray-body radiation to a large enclosure",
        latex=r"\dot{Q} = \varepsilon \sigma A (T^4 - T_{\infty}^4)",
        expr="epsilon * sigma * A * (T**4 - T_inf**4)",
        output="Qdot",
        unit="W",
        variables={
            "epsilon": _var(r"\varepsilon", "1", "emissivity", default=0.9),
            "sigma": _var(r"\sigma", "W/(m^2·K^4)", "Stefan–Boltzmann", default=SIGMA_SB),
            "A": _var(r"A", "m^2", "surface area"),
            "T": _var(r"T", "K", "surface temperature"),
            "T_inf": _var(r"T_{\infty}", "K", "surroundings temperature"),
        },
        disclaimer="Gray body to a large enclosure. Not view factors, not a cavity, not solar load.",
        tags=("heat", "radiation"),
        source="Incropera, first-order",
    ),
    _f(
        id="lumped_tau",
        domain="heat",
        title="Lumped thermal time constant",
        latex=r"\tau = \rho V c_p / (h A)",
        expr="rho_solid * V * cp / (h * A)",
        output="tau",
        unit="s",
        variables={
            "rho_solid": _var(r"\rho", "kg/m^3", "solid density"),
            "V": _var(r"V", "m^3", "volume"),
            "cp": _var(r"c_p", "J/(kg·K)", "specific heat"),
            "h": _var(r"h", "W/(m^2·K)", "film coefficient", default=H_STILL_AIR),
            "A": _var(r"A", "m^2", "surface area"),
        },
        disclaimer="Valid when Bi = hL/k ≪ 1. Not a 3-D transient FEA.",
        tags=("heat", "transient"),
        source="Incropera lumped capacitance",
    ),
    _f(
        id="lumped_Tss",
        domain="heat",
        title="Lumped steady temperature under a heat load",
        latex=r"T_{\mathrm{ss}} = T_{\infty} + \dot{Q}/(h A)",
        expr="T_inf + Qdot / (h * A)",
        output="T_ss",
        unit="K",
        variables={
            "T_inf": _var(r"T_{\infty}", "K", "sink temperature"),
            "Qdot": _var(r"\dot{Q}", "W", "heat into the part"),
            "h": _var(r"h", "W/(m^2·K)", "film coefficient", default=H_STILL_AIR),
            "A": _var(r"A", "m^2", "surface area"),
        },
        disclaimer="Energy balance on a lumped body. Not spatial hot spots, not CalculiX NT.",
        tags=("heat",),
        source="Incropera lumped capacitance",
    ),
]

BY_ID = {f["id"]: f for f in FORMULAS}

PACKS: dict[str, dict[str, Any]] = {
    "strength": {
        "id": "strength",
        "title": "Will it hold (handbook cantilever from the SI solid)",
        "formulas": ["cantilever_stress", "cantilever_deflection"],
        "solvers": ["analytical", "fea"],
    },
    "bushing": {
        "id": "bushing",
        "title": "Plain bearing — friction, PV, wear",
        "formulas": ["coulomb_friction", "friction_power", "pv_bushing", "archard_wear"],
        "solvers": ["analytical"],
    },
    "aero": {
        "id": "aero",
        "title": "Drag / dynamic pressure from projected area",
        "formulas": ["reynolds", "dynamic_pressure", "drag_force", "aero_power"],
        "solvers": ["analytical", "fluids"],
    },
    "pipe": {
        "id": "pipe",
        "title": "Internal flow — Re then laminar ΔP or Darcy",
        "formulas": ["reynolds", "hagen_poiseuille", "darcy_weisbach"],
        "solvers": ["analytical", "fluids"],
    },
    "multirotor": {
        "id": "multirotor",
        "title": "Hover power / T/W / first-order quad speed (not a prop map)",
        "formulas": ["disk_area", "thrust_weight", "momentum_hover", "quad_vmax"],
        "solvers": ["analytical"],
    },
    "spring": {
        "id": "spring",
        "title": "Coil / latch / 1-DOF — not Adams",
        "formulas": ["hooke_spring", "coil_rate", "wahl_stress", "spring_energy", "mass_spring_wn", "pin_shear"],
        "solvers": ["analytical", "mechanism"],
    },
    "mechanism": {
        "id": "mechanism",
        "title": "Planar pin forces + springs at a pose",
        "formulas": ["pin_shear", "hooke_spring", "wahl_stress"],
        "solvers": ["mechanism", "analytical"],
    },
    "fit": {
        "id": "fit",
        "title": "Pin/hole clearance after shrink + stackup",
        "formulas": ["fit_clearance", "stackup_wc", "stackup_rss"],
        "solvers": ["analytical"],
    },
    "heat": {
        "id": "heat",
        "title": "Steady conduction / film / radiation + lumped Tss (not thermal FEA)",
        "formulas": ["conduction", "newton_cooling", "radiation_net", "lumped_tau", "lumped_Tss"],
        "solvers": ["analytical", "thermal"],
    },
}


def list_book(domain: str | None = None) -> list[dict[str, Any]]:
    rows = FORMULAS
    if domain:
        want = domain.lower().strip()
        rows = [f for f in rows if f["domain"] == want or want in f["tags"]]
    return [
        {
            "id": f["id"],
            "domain": f["domain"],
            "title": f["title"],
            "latex": f["latex"],
            "unit": f["unit"],
            "tags": f["tags"],
            "disclaimer": f["disclaimer"],
        }
        for f in rows
    ]


def lookup_formula(query: str, domain: str | None = None) -> dict[str, Any]:
    q = (query or "").lower().strip()
    tokens = [t for t in q.replace(",", " ").split() if t]
    scored: list[tuple[int, dict[str, Any]]] = []
    for f in FORMULAS:
        if domain and f["domain"] != domain.lower() and domain.lower() not in f["tags"]:
            continue
        hay = " ".join([f["id"], f["title"], f["domain"], *f["tags"], f.get("maintain") or ""]).lower()
        score = sum(3 if tok in f["id"] else 1 for tok in tokens if tok in hay)
        if f["id"] == q.replace(" ", "_"):
            score += 20
        if score:
            scored.append((score, f))
    scored.sort(key=lambda x: -x[0])
    hits = [h for _, h in scored[:8]]
    if not hits and not tokens:
        hits = FORMULAS[:8]
    return {
        "ok": True,
        "query": query,
        "formulas": hits,
        "packs": list(PACKS.values()),
        "friction_pairs": FRICTION_PAIRS,
        "fluids": {k: v["label"] for k, v in FLUIDS.items()},
        "note": "Use these equations. Do not invent coefficients. solve_formula / run_solvers do the arithmetic.",
    }

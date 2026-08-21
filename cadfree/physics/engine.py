"""Evaluate handbook formulas. Optional SymPy rearrange. Always emit LaTeX steps."""

from __future__ import annotations

import ast
import math
from typing import Any

from cadfree.physics.book import BY_ID, FRICTION_PAIRS, FLUIDS, G

_ALLOWED_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "pi": math.pi,
    "e": math.e,
    "pow": pow,
}


def sympy_status() -> dict[str, Any]:
    try:
        import sympy  # noqa: F401

        return {"available": True, "label": "SymPy rearrange"}
    except Exception:
        return {
            "available": False,
            "install_hint": "pip install sympy  # optional: solve for a different variable",
        }


def _num(value: float) -> str:
    x = float(value)
    if abs(x) >= 1e4 or (abs(x) < 1e-3 and x != 0):
        return f"{x:.4g}"
    return f"{x:.6g}"


class _Safe(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> None:
        if isinstance(
            node,
            (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.Constant,
                ast.Name,
                ast.Load,
                ast.Call,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Pow,
                ast.Mod,
                ast.USub,
                ast.UAdd,
                ast.FloorDiv,
                ast.Compare,
                ast.Gt,
                ast.Lt,
                ast.GtE,
                ast.LtE,
                ast.Eq,
                ast.NotEq,
                ast.BoolOp,
                ast.And,
                ast.Or,
                ast.IfExp,
                ast.keyword,
            ),
        ):
            for child in ast.iter_child_nodes(node):
                self.visit(child)
            return
        raise ValueError(f"unsafe expression: {type(node).__name__}")


def safe_eval(expr: str, values: dict[str, float]) -> float:
    tree = ast.parse(expr, mode="eval")
    _Safe().visit(tree)
    env = {**_ALLOWED_FUNCS, **{k: float(v) for k, v in values.items()}}
    return float(eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, env))


def _fill_specials(values: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, raw in values.items():
        if raw is None or isinstance(raw, bool):
            continue
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    pair = str(values.get("pair") or values.get("friction_pair") or "").strip()
    if pair and "mu" not in out:
        if pair not in FRICTION_PAIRS:
            raise ValueError(
                f"unknown friction pair {pair!r}. Book pairs: {sorted(FRICTION_PAIRS)}"
            )
        out["mu"] = FRICTION_PAIRS[pair]
    fluid = str(values.get("fluid") or "").strip().lower()
    if fluid in FLUIDS:
        spec = FLUIDS[fluid]
        out.setdefault("rho", spec["rho"])
        out.setdefault("mu_visc", spec["mu_visc"])
    out.setdefault("g", G)
    return out


def _needed(formula: dict[str, Any], values: dict[str, float]) -> list[str]:
    missing = []
    for name, spec in formula["variables"].items():
        if name in values:
            continue
        if spec.get("default") is not None:
            continue
        missing.append(name)
    return missing


def _with_defaults(formula: dict[str, Any], values: dict[str, float]) -> dict[str, float]:
    filled = dict(values)
    for name, spec in formula["variables"].items():
        if name not in filled and spec.get("default") is not None:
            filled[name] = float(spec["default"])
    return filled


def _plug_latex(formula: dict[str, Any], values: dict[str, float]) -> str:
    latex = formula["latex"]
    specs = sorted(
        formula["variables"].items(),
        key=lambda kv: -len(kv[1].get("latex") or kv[0]),
    )
    for name, spec in specs:
        if name not in values:
            continue
        token = spec.get("latex") or name
        latex = latex.replace(token, _num(values[name]))
    return latex


def _rearrange(formula: dict[str, Any], solve_for: str, values: dict[str, float]) -> dict[str, Any]:
    status = sympy_status()
    if not status["available"]:
        return {
            "ok": False,
            "error": status.get("install_hint") or "SymPy not installed",
            "sympy": status,
        }
    import sympy as sp

    names = list(formula["variables"]) + [formula["output"]]
    syms = {n: sp.symbols(n, real=True) for n in names}
    try:
        rhs = sp.sympify(formula["expr"], locals=syms)
        eq = sp.Eq(syms[formula["output"]], rhs)
        target = syms[solve_for]
        sols = sp.solve(eq, target)
    except Exception as exc:
        return {"ok": False, "error": f"SymPy could not rearrange: {exc}"}
    if not sols:
        return {"ok": False, "error": f"no symbolic solution for {solve_for}"}
    expr = sols[0]
    known = {syms[k]: v for k, v in values.items() if k in syms and k != solve_for}
    try:
        numeric = float(expr.subs(known))
    except Exception as exc:
        return {"ok": False, "error": f"substituted rearrange failed: {exc}", "latex": sp.latex(expr)}
    return {
        "ok": True,
        "expr_sympy": str(expr),
        "latex": sp.latex(sp.Eq(target, expr)),
        "value": numeric,
        "sympy": True,
    }


def solve_formula(
    formula_id: str,
    values: dict[str, Any] | None = None,
    *,
    solve_for: str | None = None,
    provenance: dict[str, str] | None = None,
) -> dict[str, Any]:
    formula = BY_ID.get(formula_id)
    if not formula:
        return {"ok": False, "error": f"unknown formula {formula_id}. lookup_formula first."}
    try:
        filled = _fill_specials(values or {})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    used_defaults: list[str] = []
    for name, spec in formula["variables"].items():
        if name not in filled and spec.get("default") is not None:
            filled[name] = float(spec["default"])
            used_defaults.append(name)

    target = (solve_for or formula["output"]).strip()
    steps: list[dict[str, str]] = [
        {"title": "From the book", "latex": formula["latex"]},
    ]
    if target != formula["output"]:
        rearranged = _rearrange(formula, target, filled)
        if not rearranged.get("ok"):
            return {
                "ok": False,
                "formula_id": formula_id,
                "title": formula["title"],
                "needed": _needed(formula, filled) + [formula["output"]],
                "error": rearranged.get("error"),
                "steps": steps,
                "disclaimer": formula["disclaimer"],
            }
        steps.append({"title": "Rearranged (SymPy)", "latex": rearranged["latex"]})
        value = float(rearranged["value"])
        unit = formula["variables"].get(target, {}).get("unit") or ""
        steps.append(
            {
                "title": "Result",
                "latex": rf"{formula['variables'].get(target, {}).get('latex', target)} = {_num(value)}\,\mathrm{{{unit}}}",
            }
        )
        return _result(formula, target, unit, value, filled, steps, used_defaults, provenance, sympy=True)

    missing = _needed(formula, filled)
    if missing:
        return {
            "ok": False,
            "formula_id": formula_id,
            "title": formula["title"],
            "latex": formula["latex"],
            "needed": missing,
            "have": filled,
            "steps": steps,
            "error": "missing SI inputs: " + ", ".join(missing) + ". Do not invent them — ask_survey or bind the CadQuery snapshot.",
            "disclaimer": formula["disclaimer"],
        }
    used = _with_defaults(formula, filled)
    try:
        value = safe_eval(formula["expr"], used)
    except Exception as exc:
        return {"ok": False, "error": f"eval failed: {exc}", "formula_id": formula_id}
    unit = formula["unit"]
    steps.append({"title": "With this part's SI numbers", "latex": _plug_latex(formula, used)})
    steps.append(
        {
            "title": "Result",
            "latex": rf"{formula['output']} = {_num(value)}\,\mathrm{{{unit}}}".replace("mathrm{}", "mathrm{ }"),
        }
    )
    return _result(formula, formula["output"], unit, value, used, steps, used_defaults, provenance, sympy=False)


def _result(
    formula: dict[str, Any],
    output: str,
    unit: str,
    value: float,
    used: dict[str, float],
    steps: list[dict[str, str]],
    used_defaults: list[str],
    provenance: dict[str, str] | None,
    *,
    sympy: bool,
) -> dict[str, Any]:
    extra: dict[str, float] = {}
    if formula["id"] == "pv_bushing":
        extra["PV_MPa_ms"] = value / 1e6
    if formula["id"] == "cantilever_stress":
        extra["sigma_MPa"] = value / 1e6
    if formula["id"] == "cantilever_deflection":
        extra["delta_mm"] = value * 1000.0
    if formula["id"] == "wahl_stress":
        extra["tau_MPa"] = value / 1e6
    if formula["id"] == "pin_shear":
        extra["tau_MPa"] = value / 1e6
    if formula["id"] == "coil_rate":
        extra["k_n_per_mm"] = value / 1000.0
    if formula["id"] == "mass_spring_wn":
        extra["fn_hz"] = value / (2.0 * math.pi)
    return {
        "ok": True,
        "kind": "analytical",
        "formula_id": formula["id"],
        "domain": formula["domain"],
        "title": formula["title"],
        "output": output,
        "value": value,
        "unit": unit,
        "extra": extra,
        "inputs": used,
        "provenance": provenance or {},
        "used_defaults": used_defaults,
        "steps": steps,
        "maintain": formula.get("maintain") or "",
        "disclaimer": formula["disclaimer"],
        "source": formula.get("source"),
        "sympy": sympy,
        "octave": _octave_script(formula, used, output, value, unit),
    }


def _octave_script(
    formula: dict[str, Any], values: dict[str, float], output: str, value: float, unit: str
) -> str:
    lines = [
        f"% {formula['title']} — SI. From the Cadfree formula book, not CadQuery.",
        f"% {formula['latex']}",
    ]
    for key, val in values.items():
        lines.append(f"{key} = {val:.8g};")
    lines.append(f"out = {formula['expr']};")
    lines.append(f"fprintf('{output} %g {unit}\\n', out);")
    lines.append(f"% python/safe_eval = {value:.8g}")
    return "\n".join(lines) + "\n"

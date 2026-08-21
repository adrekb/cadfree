"""Feature tree from CadQuery source.

CadQuery is a Python script, not a history kernel. We do not fork it and we
do not invent OCCT face IDs. The tree is the Workplane operations in the
script, in order. Clicking a fillet patches *that* call. Shared PARAMS keys
are isolated so the other fillet does not move.
"""

from __future__ import annotations

import ast
from typing import Any

from cadfree.cad.params import apply_params, extract_params

FEATURE_TREE_NOTE = (
    "CadQuery is a Python script, not a history kernel like SolidWorks. "
    "This tree is the operations in the source. Click a fillet to change that "
    "call. The STL has no face IDs — picking a fillet in the 3D view is not wired."
)

FEATURE_METHODS = {
    "Workplane",
    "box",
    "sphere",
    "cylinder",
    "wedge",
    "cone",
    "rect",
    "circle",
    "ellipse",
    "polygon",
    "slot2D",
    "polyline",
    "spline",
    "extrude",
    "twistExtrude",
    "revolve",
    "loft",
    "sweep",
    "hole",
    "cboreHole",
    "cskHole",
    "cutBlind",
    "cutThruAll",
    "union",
    "cut",
    "intersect",
    "cutEach",
    "combine",
    "fillet",
    "chamfer",
    "shell",
    "thicken",
    "offset2D",
    "mirror",
    "translate",
    "rotate",
    "rotateAboutCenter",
    "split",
    "section",
    "add",
    "text",
}

CONTEXT_METHODS = {
    "faces",
    "edges",
    "vertices",
    "wires",
    "solids",
    "shells",
    "workplane",
    "end",
    "tag",
    "pushPoints",
    "rarray",
    "polarArray",
    "eachpoint",
    "transformed",
    "copyWorkplane",
    "center",
    "first",
    "last",
    "item",
    "vals",
}

KIND_LABELS = {
    "Workplane": "Workplane",
    "box": "Box",
    "sphere": "Sphere",
    "cylinder": "Cylinder",
    "fillet": "Fillet",
    "chamfer": "Chamfer",
    "hole": "Hole",
    "cboreHole": "C'bore hole",
    "cskHole": "C'sink hole",
    "extrude": "Extrude",
    "cutBlind": "Cut blind",
    "cutThruAll": "Cut through",
    "union": "Union",
    "cut": "Cut",
    "intersect": "Intersect",
    "shell": "Shell",
    "thicken": "Thicken",
    "translate": "Translate",
    "rotate": "Rotate",
    "mirror": "Mirror",
    "loft": "Loft",
    "sweep": "Sweep",
    "revolve": "Revolve",
    "circle": "Circle",
    "rect": "Rectangle",
}

ARG_NAMES = {
    "fillet": ["radius"],
    "chamfer": ["length"],
    "hole": ["diameter"],
    "cboreHole": ["diameter", "cbore_d", "cbore_depth"],
    "cskHole": ["diameter", "csk_d", "csk_angle"],
    "extrude": ["until"],
    "cutBlind": ["until"],
    "shell": ["thickness"],
    "thicken": ["thickness"],
    "circle": ["radius"],
    "sphere": ["radius"],
    "box": ["length", "width", "height"],
    "cylinder": ["radius", "height"],
    "Workplane": ["plane"],
}

SKIP_NAMES = {"PARAMS", "p"}


def extract_features(source: str) -> dict[str, Any]:
    """Parse CadQuery source into a feature list. Never invent ops."""
    params = extract_params(source)
    if not (source or "").strip():
        return {
            "features": [],
            "params": params,
            "parse_error": None,
            "note": "No CadQuery source on this part.",
            "honest": FEATURE_TREE_NOTE,
        }
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "features": [],
            "params": params,
            "parse_error": f"{exc.msg} (line {exc.lineno})",
            "note": "Fix the script — the tree is the Python, not a separate history.",
            "honest": FEATURE_TREE_NOTE,
        }

    features: list[dict[str, Any]] = []
    for stmt in tree.body:
        body_name, value = _stmt_target(stmt)
        if body_name in SKIP_NAMES:
            continue
        chain = _flatten_calls(value)
        if not chain:
            continue
        pending: list[str] = []
        for call in chain:
            kind = _method_name(call)
            if not kind:
                continue
            if kind in CONTEXT_METHODS:
                pending.append(_call_text(call))
                continue
            if kind not in FEATURE_METHODS:
                pending.append(_call_text(call))
                continue
            features.append(
                _feature_from_call(call, kind, body_name, pending, params, len(features))
            )
            pending = []
    return {
        "features": features,
        "params": params,
        "parse_error": None,
        "note": FEATURE_TREE_NOTE,
        "honest": FEATURE_TREE_NOTE,
    }


def patch_feature(
    source: str,
    feature_id: str,
    value: float,
    arg_index: int = 0,
) -> dict[str, Any]:
    """Change one numeric argument on one operation. Isolates shared PARAMS."""
    parsed = extract_features(source)
    feat = _find_feature(parsed.get("features") or [], feature_id)
    if not feat:
        return {
            "ok": False,
            "error": f"No feature {feature_id} in this script.",
            "features": parsed.get("features") or [],
            "honest": FEATURE_TREE_NOTE,
        }
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"ok": False, "error": f"{exc.msg} (line {exc.lineno})"}

    call = _locate_call(tree, feat)
    if call is None:
        return {
            "ok": False,
            "error": "Could not locate that call. Edit the script directly.",
            "honest": FEATURE_TREE_NOTE,
        }
    args = feat.get("args") or []
    if not args:
        return {
            "ok": False,
            "error": f"{feat.get('kind')} has no numeric argument to patch. Edit the script.",
            "honest": FEATURE_TREE_NOTE,
        }
    if arg_index < 0 or arg_index >= len(args):
        arg_index = int(feat.get("primary_index") or 0)
        if arg_index >= len(args):
            arg_index = 0
    if arg_index >= len(call.args):
        return {"ok": False, "error": "That argument is not a positional value in the call."}

    node = call.args[arg_index]
    arg = args[arg_index] if arg_index < len(args) else {}
    kind = feat.get("kind") or "op"
    params = dict(parsed.get("params") or {})
    isolated = False
    note = ""
    new_source = source

    if arg.get("kind") == "params" and arg.get("key"):
        key = str(arg["key"])
        uses = _count_param_uses(parsed["features"], key)
        if uses <= 1:
            new_source = apply_params(source, {key: value})
            note = f"Updated PARAMS[{key!r}] — only this {kind} uses it."
        else:
            new_key = _unique_param_key(params, kind, feat.get("index") or 0)
            new_source = apply_params(source, {new_key: value})
            try:
                tree2 = ast.parse(new_source)
            except SyntaxError as extra:
                return {"ok": False, "error": f"{extra.msg} (line {extra.lineno})"}
            shifted = extract_features(new_source)
            feat2 = _closest_feature(shifted["features"], kind, feat.get("index") or 0)
            call2 = _locate_call(tree2, feat2 or feat)
            if call2 is None or arg_index >= len(call2.args):
                return {
                    "ok": False,
                    "error": "Isolating that fillet lost the call site. Edit the script.",
                }
            expr = _params_expr(new_source, new_key)
            new_source = _replace_span(new_source, call2.args[arg_index], expr)
            isolated = True
            note = (
                f"That {kind} shared PARAMS[{key!r}] with {uses - 1} other op(s). "
                f"Isolated it to PARAMS[{new_key!r}] so the others stay put."
            )
    else:
        new_source = _replace_span(source, node, repr(value))
        isolated = True
        note = f"Changed this {kind} only."

    out = extract_features(new_source)
    selected = _closest_feature(out["features"], kind, feat.get("index") or 0)
    return {
        "ok": True,
        "source": new_source,
        "isolated": isolated,
        "note": note,
        "feature_id": (selected or {}).get("id"),
        "index": (selected or {}).get("index"),
        **out,
    }


def _stmt_target(stmt: ast.stmt) -> tuple[str | None, ast.AST | None]:
    if isinstance(stmt, ast.Assign) and stmt.targets:
        target = stmt.targets[0]
        name = target.id if isinstance(target, ast.Name) else None
        return name, stmt.value
    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        name = stmt.target.id if isinstance(stmt.target, ast.Name) else None
        return name, stmt.value
    if isinstance(stmt, ast.Expr):
        return None, stmt.value
    return None, None


def _flatten_calls(node: ast.AST | None) -> list[ast.Call]:
    if node is None:
        return []
    chain: list[ast.Call] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Call):
        chain.append(cur)
        func = cur.func
        if isinstance(func, ast.Attribute):
            cur = func.value
        else:
            break
    chain.reverse()
    return chain


def _method_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _call_text(call: ast.Call) -> str:
    try:
        return ast.unparse(call)
    except Exception:
        return _method_name(call)


def _feature_from_call(
    call: ast.Call,
    kind: str,
    body: str | None,
    selectors: list[str],
    params: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    args = [_describe_arg(a, i, kind, params) for i, a in enumerate(call.args)]
    primary_index = 0 if args else None
    names = ARG_NAMES.get(kind) or []
    for i, arg in enumerate(args):
        if i < len(names):
            arg["name"] = names[i]
    editable_args = [a for a in args if a.get("editable")]
    primary = None
    if editable_args:
        primary = editable_args[0]
        primary_index = int(primary["index"])
    label = _label(kind, args, selectors)
    return {
        "id": f"{kind}:{call.lineno}:{call.col_offset}",
        "index": index,
        "kind": kind,
        "label": label,
        "body": body or "script",
        "line": call.lineno,
        "col": call.col_offset,
        "end_line": getattr(call, "end_lineno", None) or call.lineno,
        "end_col": getattr(call, "end_col_offset", None) or 0,
        "selectors": selectors,
        "args": args,
        "editable": bool(editable_args),
        "primary_index": primary_index,
        "primary": primary,
    }


def _describe_arg(node: ast.AST, index: int, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = _safe_unparse(node)
    info: dict[str, Any] = {
        "index": index,
        "kind": "expr",
        "raw": raw,
        "editable": True,
        "value": None,
        "key": None,
    }
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
        node.value, bool
    ):
        info.update(kind="literal", value=float(node.value), editable=True)
        return info
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float)):
            info.update(kind="literal", value=float(-node.operand.value), editable=True)
            return info
    key = _params_key(node)
    if key is not None:
        val = params.get(key)
        info.update(
            kind="params",
            key=key,
            value=float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None,
            editable=isinstance(val, (int, float)) and not isinstance(val, bool),
        )
        return info
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        info.update(kind="string", value=None, editable=False, raw=repr(node.value))
        return info
    # Tuples / selectors / expressions: show them, don't offer a single slider.
    info["editable"] = False
    return info


def _params_key(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    base = node.value
    if not isinstance(base, ast.Name) or base.id not in {"p", "PARAMS"}:
        return None
    sl = node.slice
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def _label(kind: str, args: list[dict[str, Any]], selectors: list[str]) -> str:
    title = KIND_LABELS.get(kind, kind)
    nums = [a for a in args if isinstance(a.get("value"), (int, float))]
    if kind == "Workplane" and args:
        plane = args[0].get("raw") or ""
        return f"Workplane {plane.strip(chr(39) + chr(34))}".strip()
    if kind == "fillet" and nums:
        return f"Fillet { _fmt(nums[0]['value']) } mm"
    if kind == "chamfer" and nums:
        return f"Chamfer { _fmt(nums[0]['value']) } mm"
    if kind in {"hole", "cboreHole", "cskHole"} and nums:
        return f"{title} Ø{_fmt(nums[0]['value'])} mm"
    if kind == "box" and len(nums) >= 3:
        return f"Box {_fmt(nums[0]['value'])}×{_fmt(nums[1]['value'])}×{_fmt(nums[2]['value'])}"
    if kind == "circle" and nums:
        return f"Circle r={_fmt(nums[0]['value'])}"
    if kind == "extrude" and nums:
        return f"Extrude {_fmt(nums[0]['value'])} mm"
    if nums and kind not in {"union", "cut", "intersect", "translate", "rotate"}:
        return f"{title} {_fmt(nums[0]['value'])}"
    if selectors:
        short = selectors[-1]
        if len(short) > 42:
            short = short[:40] + "…"
        return f"{title} · {short}"
    return title


def _fmt(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _find_feature(features: list[dict[str, Any]], feature_id: str) -> dict[str, Any] | None:
    for feat in features:
        if feat.get("id") == feature_id:
            return feat
    if feature_id.startswith("index:"):
        try:
            idx = int(feature_id.split(":", 1)[1])
        except ValueError:
            return None
        for feat in features:
            if feat.get("index") == idx:
                return feat
    return None


def _locate_call(tree: ast.AST, feat: dict[str, Any], line_shift: int = 0) -> ast.Call | None:
    want_line = int(feat["line"]) + line_shift
    want_col = int(feat["col"])
    want_kind = feat["kind"]
    exact: ast.Call | None = None
    same_kind: ast.Call | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _method_name(node) != want_kind:
            continue
        if node.lineno == want_line and node.col_offset == want_col:
            exact = node
            break
        if node.lineno == want_line:
            same_kind = same_kind or node
    return exact or same_kind


def _line_shift(before: str, after: str) -> int:
    return after.count("\n") - before.count("\n")


def _count_param_uses(features: list[dict[str, Any]], key: str) -> int:
    n = 0
    for feat in features:
        for arg in feat.get("args") or []:
            if arg.get("kind") == "params" and arg.get("key") == key:
                n += 1
    return n


def _unique_param_key(params: dict[str, Any], kind: str, index: int) -> str:
    base = f"{kind}_{index}_mm"
    if base not in params:
        return base
    n = 2
    while f"{base}_{n}" in params:
        n += 1
    return f"{base}_{n}"


def _params_expr(source: str, key: str) -> str:
    if "\np = PARAMS" in source or source.startswith("p = PARAMS") or "\np=PARAMS" in source:
        return f"p[{key!r}]"
    return f"PARAMS[{key!r}]"


def _replace_span(source: str, node: ast.AST, new_text: str) -> str:
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if None in (lineno, end_lineno, col, end_col):
        raise ValueError("AST node has no source span")
    lines = source.splitlines(keepends=True)
    if not lines:
        return new_text
    start = sum(len(line) for line in lines[: lineno - 1]) + col
    end = sum(len(line) for line in lines[: end_lineno - 1]) + end_col
    return source[:start] + new_text + source[end:]


def _closest_feature(
    features: list[dict[str, Any]], kind: str, index: int
) -> dict[str, Any] | None:
    for feat in features:
        if feat.get("index") == index and feat.get("kind") == kind:
            return feat
    matches = [f for f in features if f.get("kind") == kind]
    if not matches:
        return None
    if 0 <= index < len(features) and features[index].get("kind") == kind:
        return features[index]
    return matches[min(index, len(matches) - 1)]


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""

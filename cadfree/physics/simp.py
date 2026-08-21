"""SIMP topology optimization — the research method behind Fusion Generative Design.

Lineage (do not pretend this is Autodesk):
  Sigmund 2001, A 99 line topology optimization code written in MATLAB
  Liu & Tovar 2014, An efficient 3D topology optimization MATLAB code
  Manufacturing filters: mill 2.5D (extrude) and a simple AM overhang squeeze

This is voxel FEM + optimality-criteria SIMP, not CalculiX and not a
level-set T-spline. Missing scipy is named in one sentence. We never
invent a compliance number that did not come from the solve.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

try:
    import scipy.sparse as sp
    import scipy.sparse.linalg as sla

    _SCIPY = True
    _SCIPY_ERR = ""
except Exception as exc:  # pragma: no cover
    sp = None  # type: ignore
    sla = None  # type: ignore
    _SCIPY = False
    _SCIPY_ERR = str(exc)


def probe_simp() -> dict[str, Any]:
    return {
        "available": bool(_SCIPY),
        "solver": "simp-voxel-fem",
        "label": "SIMP topology optimization (Sigmund / Liu–Tovar), not Fusion Generative Design",
        "install_hint": (
            "pip install scipy  (needed for the sparse voxel FEM inside SIMP)"
            if not _SCIPY
            else ""
        ),
        "error": _SCIPY_ERR or None,
        "not": (
            "Not Autodesk Generative Design, not nTopology, not a T-spline. "
            "Voxel SIMP; organic mesh out; CadQuery is not rewritten."
        ),
    }


def lk_quad(E: float = 1.0, nu: float = 0.3) -> np.ndarray:
    """4-node quad plane-stress KE. Sigmund 99-line."""
    k = np.array(
        [
            1 / 2 - nu / 6,
            1 / 8 + nu / 8,
            -1 / 4 - nu / 12,
            -1 / 8 + 3 * nu / 8,
            -1 / 4 + nu / 12,
            -1 / 8 - nu / 8,
            nu / 6,
            1 / 8 - 3 * nu / 8,
        ]
    )
    KE = (E / (1 - nu**2)) * np.array(
        [
            [k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7]],
            [k[1], k[0], k[7], k[6], k[5], k[4], k[3], k[2]],
            [k[2], k[7], k[0], k[5], k[6], k[3], k[4], k[1]],
            [k[3], k[6], k[5], k[0], k[7], k[2], k[1], k[4]],
            [k[4], k[5], k[6], k[7], k[0], k[1], k[2], k[3]],
            [k[5], k[4], k[3], k[2], k[1], k[0], k[7], k[6]],
            [k[6], k[3], k[4], k[1], k[2], k[7], k[0], k[5]],
            [k[7], k[2], k[1], k[4], k[3], k[6], k[5], k[0]],
        ]
    )
    return KE


def optimize_2d(
    nelx: int,
    nely: int,
    volfrac: float = 0.5,
    penal: float = 3.0,
    rmin: float = 1.5,
    *,
    nloop: int = 40,
    E0: float = 1.0,
    Emin: float = 1e-9,
    nu: float = 0.3,
    load: str = "mbb",
    mill_25d: bool = False,
    additive: bool = False,
    passive_void: np.ndarray | None = None,
    passive_solid: np.ndarray | None = None,
    x0: np.ndarray | None = None,
) -> dict[str, Any]:
    """2D SIMP. Default load is the MBB beam (Sigmund)."""
    if not _SCIPY:
        return {"ok": False, "error": probe_simp()["install_hint"], "reason": probe_simp()["install_hint"]}
    nelx, nely = int(nelx), int(nely)
    if x0 is not None:
        x = np.clip(np.asarray(x0, dtype=float), 0.001, 1.0)
        if x.shape != (nely, nelx):
            raise ValueError(f"x0 shape {x.shape} != ({nely}, {nelx})")
    else:
        x = np.full((nely, nelx), float(volfrac))
    _ = (mill_25d, additive)  # 2-D is already a single layer; mill/AM filters are 3-D.
    KE = lk_quad(1.0, nu)
    dof = 2 * (nelx + 1) * (nely + 1)
    edof = _edof_2d(nelx, nely)
    H, Hs = _filter_2d(nelx, nely, rmin)
    freedofs, F = _bc_2d(nelx, nely, dof, load)
    history: list[float] = []
    dc = np.zeros_like(x)
    for _ in range(int(nloop)):
        x, dc = _apply_passive(x, dc, passive_void, passive_solid)
        U, c = _fe_2d(x, KE, edof, freedofs, F, penal, E0, Emin, dof)
        history.append(float(c))
        ce = np.zeros_like(x)
        for ely in range(nely):
            for elx in range(nelx):
                ue = U[edof[ely, elx]]
                ce[ely, elx] = float(ue @ KE @ ue)
        dc = -penal * (x ** (penal - 1.0)) * E0 * ce
        dc = (H @ dc.ravel(order="F") / Hs).reshape((nely, nelx), order="F")
        x = _oc(x, volfrac, dc, passive_void, passive_solid)
        if len(history) > 4 and abs(history[-1] - history[-2]) / max(history[-1], 1e-12) < 1e-3:
            break
    x, dc = _apply_passive(x, dc, passive_void, passive_solid)
    return {
        "ok": True,
        "x": x,
        "compliance": history[-1] if history else None,
        "history": history,
        "volfrac_actual": float(x.mean()),
        "nelx": nelx,
        "nely": nely,
        "method": "simp-2d",
    }


def optimize_3d(
    nelx: int,
    nely: int,
    nelz: int,
    volfrac: float = 0.4,
    penal: float = 3.0,
    rmin: float = 1.4,
    *,
    nloop: int = 18,
    E0: float = 1.0,
    Emin: float = 1e-9,
    nu: float = 0.3,
    mill_25d: bool = False,
    additive: bool = False,
    passive_void: np.ndarray | None = None,
    passive_solid: np.ndarray | None = None,
    x0: np.ndarray | None = None,
) -> dict[str, Any]:
    """3D hex SIMP. mill_25d forces a uniform extrusion; additive squeezes unsupported layers."""
    if not _SCIPY:
        return {"ok": False, "error": probe_simp()["install_hint"], "reason": probe_simp()["install_hint"]}
    nelx, nely, nelz = int(nelx), int(nely), int(nelz)
    if nelz <= 1:
        out = optimize_2d(
            nelx,
            nely,
            volfrac,
            penal,
            rmin,
            nloop=nloop,
            E0=E0,
            Emin=Emin,
            nu=nu,
            load="cantilever",
            mill_25d=mill_25d,
            additive=additive,
            passive_void=None if passive_void is None else passive_void[:, :, 0].T
            if passive_void.ndim == 3
            else passive_void,
            passive_solid=None if passive_solid is None else passive_solid[:, :, 0].T
            if passive_solid.ndim == 3
            else passive_solid,
            x0=None if x0 is None else (x0[:, :, 0].T if x0.ndim == 3 else x0),
        )
        if out.get("ok") and out.get("x") is not None:
            x2 = out["x"]
            x3 = np.repeat(x2.T[:, :, None], max(nelz, 1), axis=2)
            out["x"] = x3
            out["method"] = "simp-2d-extruded"
        return out
    if x0 is not None:
        x = np.clip(np.asarray(x0, dtype=float), 0.001, 1.0)
        if x.shape != (nelx, nely, nelz):
            raise ValueError(f"x0 shape {x.shape} != ({nelx}, {nely}, {nelz})")
    else:
        x = np.full((nelx, nely, nelz), float(volfrac))
    KE = _hex8_ke(1.0, nu)
    ndof = 3 * (nelx + 1) * (nely + 1) * (nelz + 1)
    edof = _edof_3d(nelx, nely, nelz)
    H, Hs = _filter_3d(nelx, nely, nelz, rmin)
    freedofs, F = _bc_3d(nelx, nely, nelz, ndof)
    history: list[float] = []
    dc = np.zeros_like(x)
    for _ in range(int(nloop)):
        x, dc = _apply_passive(x, dc, passive_void, passive_solid)
        if mill_25d:
            x = _project_mill(x)
        if additive:
            x = _overhang_filter(x)
        U, c = _fe_3d(x, KE, edof, freedofs, F, penal, E0, Emin, ndof)
        history.append(float(c))
        ce = np.zeros_like(x)
        for ez in range(nelz):
            for ey in range(nely):
                for ex in range(nelx):
                    ue = U[edof[ex, ey, ez]]
                    ce[ex, ey, ez] = float(ue @ KE @ ue)
        dc = -penal * (x ** (penal - 1.0)) * E0 * ce
        dc = (H @ dc.ravel(order="F") / Hs).reshape((nelx, nely, nelz), order="F")
        x = _oc(x, volfrac, dc, passive_void, passive_solid)
        if mill_25d:
            x = _project_mill(x)
        if additive:
            x = _overhang_filter(x)
        if len(history) > 3 and abs(history[-1] - history[-2]) / max(history[-1], 1e-12) < 2e-3:
            break
    x, dc = _apply_passive(x, dc, passive_void, passive_solid)
    return {
        "ok": True,
        "x": x,
        "compliance": history[-1] if history else None,
        "history": history,
        "volfrac_actual": float(x.mean()),
        "nelx": nelx,
        "nely": nely,
        "nelz": nelz,
        "method": "simp-3d" + ("-mill" if mill_25d else "") + ("-am" if additive else ""),
    }


def _edof_2d(nelx: int, nely: int) -> np.ndarray:
    edof = np.zeros((nely, nelx, 8), dtype=int)
    for ely in range(nely):
        for elx in range(nelx):
            n1 = (nely + 1) * elx + ely
            n2 = (nely + 1) * (elx + 1) + ely
            edof[ely, elx] = [
                2 * n1,
                2 * n1 + 1,
                2 * n2,
                2 * n2 + 1,
                2 * n2 + 2,
                2 * n2 + 3,
                2 * n1 + 2,
                2 * n1 + 3,
            ]
    return edof


def _bc_2d(nelx: int, nely: int, dof: int, load: str) -> tuple[np.ndarray, np.ndarray]:
    F = np.zeros(dof)
    if load == "mbb":
        # Half-MBB: left edge roller+fixed y at bottom-left, load down at top-left,
        # right-bottom uy fixed. Matches Sigmund's 99-line intent closely enough
        # that compliance falls and volume holds.
        F[1] = -1.0
        fixed = list(range(0, 2 * (nely + 1), 2))
        fixed.append(2 * (nelx + 1) * (nely + 1) - 1)
    else:
        # Cantilever: left wall fixed, load down at right-middle
        left = []
        for j in range(nely + 1):
            n = j
            left.extend([2 * n, 2 * n + 1])
        fixed = left
        mid = nely // 2
        n_load = (nely + 1) * nelx + mid
        F[2 * n_load + 1] = -1.0
    fixed = np.unique(np.array(fixed, dtype=int))
    free = np.setdiff1d(np.arange(dof), fixed)
    return free, F


def _fe_2d(
    x: np.ndarray,
    KE: np.ndarray,
    edof: np.ndarray,
    freedofs: np.ndarray,
    F: np.ndarray,
    penal: float,
    E0: float,
    Emin: float,
    dof: int,
) -> tuple[np.ndarray, float]:
    nely, nelx = x.shape
    ntriplets = nelx * nely * 64
    iK = np.zeros(ntriplets, dtype=int)
    jK = np.zeros(ntriplets, dtype=int)
    sK = np.zeros(ntriplets)
    idx = 0
    for ely in range(nely):
        for elx in range(nelx):
            ed = edof[ely, elx]
            E = Emin + (x[ely, elx] ** penal) * (E0 - Emin)
            ke = E * KE
            ii, jj = np.meshgrid(ed, ed, indexing="ij")
            iK[idx : idx + 64] = ii.ravel()
            jK[idx : idx + 64] = jj.ravel()
            sK[idx : idx + 64] = ke.ravel()
            idx += 64
    K = sp.coo_matrix((sK, (iK, jK)), shape=(dof, dof)).tocsc()
    U = np.zeros(dof)
    Kff = K[freedofs[:, None], freedofs]
    U[freedofs] = sla.spsolve(Kff, F[freedofs])
    c = float(U @ (K @ U))
    return U, c


def _filter_2d(nelx: int, nely: int, rmin: float) -> tuple[Any, np.ndarray]:
    n = nelx * nely
    rmin = max(float(rmin), 1.0)
    rows, cols, vals = [], [], []
    for i in range(nelx):
        for j in range(nely):
            e1 = i * nely + j
            for k in range(max(i - int(rmin), 0), min(i + int(rmin) + 1, nelx)):
                for l in range(max(j - int(rmin), 0), min(j + int(rmin) + 1, nely)):
                    e2 = k * nely + l
                    fac = rmin - math.hypot(i - k, j - l)
                    if fac > 0:
                        rows.append(e1)
                        cols.append(e2)
                        vals.append(fac)
    H = sp.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsc()
    Hs = np.array(H.sum(axis=1)).ravel()
    Hs[Hs == 0] = 1.0
    return H, Hs


def _oc(
    x: np.ndarray,
    volfrac: float,
    dc: np.ndarray,
    passive_void: np.ndarray | None,
    passive_solid: np.ndarray | None,
) -> np.ndarray:
    l1, l2 = 0.0, 1e9
    move = 0.2
    xnew = x.copy()
    while (l2 - l1) / (l1 + l2 + 1e-12) > 1e-3:
        lmid = 0.5 * (l1 + l2)
        xnew = np.maximum(
            0.001,
            np.maximum(
                x - move,
                np.minimum(1.0, np.minimum(x + move, x * np.sqrt(np.maximum(-dc, 1e-12) / lmid))),
            ),
        )
        xnew, _ = _apply_passive(xnew, dc, passive_void, passive_solid)
        if xnew.mean() > volfrac:
            l1 = lmid
        else:
            l2 = lmid
    return xnew


def _apply_passive(
    x: np.ndarray,
    dc: np.ndarray,
    passive_void: np.ndarray | None,
    passive_solid: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    if passive_void is not None:
        x = np.where(passive_void, 0.001, x)
        dc = np.where(passive_void, 0.0, dc)
    if passive_solid is not None:
        x = np.where(passive_solid, 1.0, x)
        dc = np.where(passive_solid, 0.0, dc)
    return x, dc


def _hex8_ke(E: float, nu: float) -> np.ndarray:
    """8-node hex stiffness via 2×2×2 Gauss quadrature. Regular unit cube."""
    g = 1.0 / math.sqrt(3.0)
    pts = (-g, g)
    lam = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    D = np.array(
        [
            [lam + 2 * mu, lam, lam, 0, 0, 0],
            [lam, lam + 2 * mu, lam, 0, 0, 0],
            [lam, lam, lam + 2 * mu, 0, 0, 0],
            [0, 0, 0, mu, 0, 0],
            [0, 0, 0, 0, mu, 0],
            [0, 0, 0, 0, 0, mu],
        ]
    )
    nodes = np.array(
        [
            [-1, -1, -1],
            [1, -1, -1],
            [1, 1, -1],
            [-1, 1, -1],
            [-1, -1, 1],
            [1, -1, 1],
            [1, 1, 1],
            [-1, 1, 1],
        ],
        dtype=float,
    )
    KE = np.zeros((24, 24))
    for xi in pts:
        for eta in pts:
            for zeta in pts:
                dN_dxi = np.zeros((3, 8))
                for a, (x, y, z) in enumerate(nodes):
                    dN_dxi[0, a] = 0.125 * x * (1 + y * eta) * (1 + z * zeta)
                    dN_dxi[1, a] = 0.125 * y * (1 + x * xi) * (1 + z * zeta)
                    dN_dxi[2, a] = 0.125 * z * (1 + x * xi) * (1 + y * eta)
                # unit cube mapping: J = 0.5 I, detJ = 0.125
                detJ = 0.125
                dN_dx = dN_dxi * 2.0
                B = np.zeros((6, 24))
                for a in range(8):
                    i = 3 * a
                    B[0, i] = dN_dx[0, a]
                    B[1, i + 1] = dN_dx[1, a]
                    B[2, i + 2] = dN_dx[2, a]
                    B[3, i] = dN_dx[1, a]
                    B[3, i + 1] = dN_dx[0, a]
                    B[4, i + 1] = dN_dx[2, a]
                    B[4, i + 2] = dN_dx[1, a]
                    B[5, i] = dN_dx[2, a]
                    B[5, i + 2] = dN_dx[0, a]
                KE += B.T @ D @ B * detJ
    return KE


def _nidx(i: int, j: int, k: int, nelx: int, nely: int) -> int:
    return i + j * (nelx + 1) + k * (nelx + 1) * (nely + 1)


def _edof_3d(nelx: int, nely: int, nelz: int) -> np.ndarray:
    edof = np.zeros((nelx, nely, nelz, 24), dtype=int)
    corners = [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    ]
    for k in range(nelz):
        for j in range(nely):
            for i in range(nelx):
                dofs = []
                for di, dj, dk in corners:
                    n = _nidx(i + di, j + dj, k + dk, nelx, nely)
                    dofs.extend([3 * n, 3 * n + 1, 3 * n + 2])
                edof[i, j, k] = dofs
    return edof


def _bc_3d(nelx: int, nely: int, nelz: int, ndof: int) -> tuple[np.ndarray, np.ndarray]:
    F = np.zeros(ndof)
    fixed = []
    for j in range(nely + 1):
        for k in range(nelz + 1):
            n = _nidx(0, j, k, nelx, nely)
            fixed.extend([3 * n, 3 * n + 1, 3 * n + 2])
    # Load at +X face, downward (-Z) at mid height
    jmid = nely // 2
    kmid = nelz // 2
    n = _nidx(nelx, jmid, kmid, nelx, nely)
    F[3 * n + 2] = -1.0
    fixed = np.unique(np.array(fixed, dtype=int))
    free = np.setdiff1d(np.arange(ndof), fixed)
    return free, F


def _fe_3d(
    x: np.ndarray,
    KE: np.ndarray,
    edof: np.ndarray,
    freedofs: np.ndarray,
    F: np.ndarray,
    penal: float,
    E0: float,
    Emin: float,
    ndof: int,
) -> tuple[np.ndarray, float]:
    nelx, nely, nelz = x.shape
    ntriplets = nelx * nely * nelz * 576
    iK = np.zeros(ntriplets, dtype=int)
    jK = np.zeros(ntriplets, dtype=int)
    sK = np.zeros(ntriplets)
    idx = 0
    for k in range(nelz):
        for j in range(nely):
            for i in range(nelx):
                ed = edof[i, j, k]
                E = Emin + (x[i, j, k] ** penal) * (E0 - Emin)
                ke = E * KE
                ii, jj = np.meshgrid(ed, ed, indexing="ij")
                iK[idx : idx + 576] = ii.ravel()
                jK[idx : idx + 576] = jj.ravel()
                sK[idx : idx + 576] = ke.ravel()
                idx += 576
    K = sp.coo_matrix((sK, (iK, jK)), shape=(ndof, ndof)).tocsc()
    U = np.zeros(ndof)
    Kff = K[freedofs[:, None], freedofs]
    try:
        U[freedofs] = sla.spsolve(Kff, F[freedofs])
    except Exception:
        U[freedofs], _ = sla.cg(Kff, F[freedofs], maxiter=400, tol=1e-4)
    c = float(U @ (K @ U))
    return U, c


def _filter_3d(nelx: int, nely: int, nelz: int, rmin: float) -> tuple[Any, np.ndarray]:
    n = nelx * nely * nelz
    rmin = max(float(rmin), 1.0)
    ir = int(math.ceil(rmin))
    rows, cols, vals = [], [], []
    for k in range(nelz):
        for j in range(nely):
            for i in range(nelx):
                e1 = i + j * nelx + k * nelx * nely
                for kk in range(max(k - ir, 0), min(k + ir + 1, nelz)):
                    for jj in range(max(j - ir, 0), min(j + ir + 1, nely)):
                        for ii in range(max(i - ir, 0), min(i + ir + 1, nelx)):
                            fac = rmin - math.sqrt((i - ii) ** 2 + (j - jj) ** 2 + (k - kk) ** 2)
                            if fac > 0:
                                e2 = ii + jj * nelx + kk * nelx * nely
                                rows.append(e1)
                                cols.append(e2)
                                vals.append(fac)
    H = sp.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsc()
    Hs = np.array(H.sum(axis=1)).ravel()
    Hs[Hs == 0] = 1.0
    return H, Hs


def _project_mill(x: np.ndarray) -> np.ndarray:
    """2.5-axis mill: density is uniform along Z (tool axis)."""
    col = x.max(axis=2, keepdims=True)
    return np.broadcast_to(col, x.shape).copy()


def _overhang_filter(x: np.ndarray) -> np.ndarray:
    """Crude AM self-support: a voxel cannot exceed the max of the layer below."""
    out = x.copy()
    for k in range(1, x.shape[2]):
        below = out[:, :, k - 1]
        padded = np.pad(below, 1, mode="edge")
        support = np.zeros_like(below)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                support = np.maximum(support, padded[1 + di : 1 + di + below.shape[0], 1 + dj : 1 + dj + below.shape[1]])
        out[:, :, k] = np.minimum(out[:, :, k], np.maximum(support, 0.001))
    return out

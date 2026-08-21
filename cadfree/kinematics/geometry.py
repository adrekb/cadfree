"""Small linear algebra for planar / spatial joints. CadQuery does not do this."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def vec3(raw: Any, default: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> np.ndarray:
    if isinstance(raw, str):
        axis = raw.lower().strip()
        return {
            "x": np.array([1.0, 0.0, 0.0]),
            "y": np.array([0.0, 1.0, 0.0]),
            "z": np.array([0.0, 0.0, 1.0]),
            "+x": np.array([1.0, 0.0, 0.0]),
            "+y": np.array([0.0, 1.0, 0.0]),
            "+z": np.array([0.0, 0.0, 1.0]),
            "-x": np.array([-1.0, 0.0, 0.0]),
            "-y": np.array([0.0, -1.0, 0.0]),
            "-z": np.array([0.0, 0.0, -1.0]),
        }.get(axis, np.array(default, dtype=float))
    if isinstance(raw, dict):
        return np.array(
            [float(raw.get("x") or 0), float(raw.get("y") or 0), float(raw.get("z") or default[2])],
            dtype=float,
        )
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return np.array([float(raw[0]), float(raw[1]), float(raw[2])], dtype=float)
    return np.array(default, dtype=float)


def normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return np.array([0.0, 0.0, 1.0])
    return v / n


def axis_angle_matrix(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    axis = normalize(axis)
    x, y, z = axis
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    t = 1.0 - c
    return np.array(
        [
            [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
        ]
    )


def rotate_around(matrix: np.ndarray, pivot: np.ndarray, axis: np.ndarray, angle_rad: float) -> np.ndarray:
    t = np.eye(4)
    t[:3, 3] = pivot
    r = np.eye(4)
    r[:3, :3] = axis_angle_matrix(axis, angle_rad)
    tinv = np.eye(4)
    tinv[:3, 3] = -np.asarray(pivot, dtype=float)
    return t @ r @ tinv @ matrix


def plane_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    k = normalize(axis)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(k[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = normalize(np.cross(k, tmp))
    e2 = np.cross(k, e1)
    return e1, e2, k


def to_2d(point: np.ndarray, origin: np.ndarray, e1: np.ndarray, e2: np.ndarray) -> np.ndarray:
    v = np.asarray(point, dtype=float) - origin
    return np.array([float(np.dot(v, e1)), float(np.dot(v, e2))])


def from_2d(
    p2: np.ndarray, origin: np.ndarray, e1: np.ndarray, e2: np.ndarray, height: float, k: np.ndarray
) -> np.ndarray:
    return origin + float(p2[0]) * e1 + float(p2[1]) * e2 + height * k


def circle_circle(
    p0: np.ndarray, r0: float, p1: np.ndarray, r1: float, eps: float = 1e-6
) -> list[np.ndarray]:
    p0 = np.asarray(p0, dtype=float)
    p1 = np.asarray(p1, dtype=float)
    d = float(np.linalg.norm(p1 - p0))
    if d < eps:
        return []
    if d > r0 + r1 + eps or d < abs(r0 - r1) - eps:
        return []
    a = (r0 * r0 - r1 * r1 + d * d) / (2.0 * d)
    h_sq = max(r0 * r0 - a * a, 0.0)
    h = math.sqrt(h_sq)
    mid = p0 + (a / d) * (p1 - p0)
    perp = np.array([-(p1 - p0)[1], (p1 - p0)[0]]) / d
    if h < eps:
        return [mid]
    return [mid + h * perp, mid - h * perp]


def pick_closer(candidates: list[np.ndarray], prev: np.ndarray | None) -> np.ndarray:
    if not candidates:
        raise ValueError("no candidates")
    if prev is None:
        return candidates[0]
    prev = np.asarray(prev, dtype=float)
    return min(candidates, key=lambda c: float(np.linalg.norm(c - prev)))

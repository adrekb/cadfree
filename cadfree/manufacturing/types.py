from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal["pass", "fail", "warn", "info"]
Verdict = Literal[
    "feasible",
    "infeasible",
    "needs_material_change",
    "needs_process_change",
    "needs_spec_change",
    "unknown",
]


@dataclass
class Check:
    id: str
    title: str
    status: Status
    message: str
    process: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Recommendation:
    type: str
    reason: str
    from_value: str | None = None
    to_value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "reason": self.reason,
            "from": self.from_value,
            "to": self.to_value,
        }


@dataclass
class MeshMetrics:
    volume_mm3: float
    surface_area_mm2: float
    bbox_mm: tuple[float, float, float]
    watertight: bool
    triangle_count: int
    solidity: float
    overhang_ratio: float = 0.0
    min_thickness_mm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_mm3": self.volume_mm3,
            "volume_cm3": self.volume_mm3 / 1000.0,
            "surface_area_mm2": self.surface_area_mm2,
            "bbox_mm": list(self.bbox_mm),
            "watertight": self.watertight,
            "triangle_count": self.triangle_count,
            "solidity": self.solidity,
            "overhang_ratio": self.overhang_ratio,
            "min_thickness_mm": self.min_thickness_mm,
        }


@dataclass
class FeasibilityReport:
    possible: bool
    verdict: Verdict
    summary: str
    checks: list[Check]
    recommendations: list[Recommendation]
    mass: dict[str, Any]
    strength: dict[str, Any]
    assumptions: list[str]
    best_capability_id: str | None = None
    best_material_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "possible": self.possible,
            "verdict": self.verdict,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "mass": self.mass,
            "strength": self.strength,
            "assumptions": self.assumptions,
            "best_capability_id": self.best_capability_id,
            "best_material_id": self.best_material_id,
        }

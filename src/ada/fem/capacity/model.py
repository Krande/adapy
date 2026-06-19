"""Neutral, serializable capacity-model data structures.

These mirror the semantics of Genie's ``model.json`` (BucklingModels → Plates,
Stiffeners) and ``*__CriteriaResults.json`` (resolved design Variables /
VariableVectors), so that:

* a Genie-compatible ``model.json`` mirror is a trivial serialization, and
* a downstream code-check adapter can consume the neutral JSON 1:1.

All quantities are SI (m, Pa, N). Frozen dataclasses keep models hashable and
safe to share across result cases.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field

# Genie SectionType enum value for Holland-profile / bulb-flat sections.
SECTION_TYPE_BULB = 7


@dataclass(frozen=True)
class CapMaterial:
    """Plate/stiffener material. ``E``, ``fy``, ``G`` in Pa."""

    E: float
    fy: float
    poisson: float = 0.3
    gamma_m: float = 1.15
    G: float | None = None
    name: str = "steel"


@dataclass(frozen=True)
class CapSection:
    """Stiffener cross-section, described the way Genie's ``model.json`` does.

    ``section_type`` is Genie's ``SectionType`` code (``7`` = bulb/Holland). The
    web/flange dimensions are the *raw* profile dimensions; the bulb→angle
    idealization for the actual check is the consumer's responsibility (it lives
    in the code-check package, not here).
    """

    name: str
    section_type: int
    height: float
    web_thickness: float
    flange_width: float = 0.0
    flange_thickness: float = 0.0

    @property
    def is_bulb(self) -> bool:
        return self.section_type == SECTION_TYPE_BULB


@dataclass(frozen=True)
class CapPlate:
    """One plate field of a capacity model."""

    name: str
    thickness: float
    length: float  # span direction (along stiffener) [m]
    width: float  # stiffener spacing direction [m]
    material: CapMaterial
    element_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class CapStiffener:
    """One stiffener of a capacity model."""

    name: str
    section: CapSection
    material: CapMaterial
    span: float  # stiffener length l [m]
    element_ids: tuple[int, ...] = ()
    eccentricity: float = 0.0  # working-point offset from plate mid-plane [m]
    continuous: bool = True


@dataclass(frozen=True)
class CapacityModel:
    """A capacity model = panel group of plates + stiffeners (one Genie ``BucklingModel``)."""

    name: str
    plates: tuple[CapPlate, ...] = ()
    stiffeners: tuple[CapStiffener, ...] = ()

    def stiffener(self, name: str) -> CapStiffener:
        for s in self.stiffeners:
            if s.name == name:
                return s
        raise KeyError(name)


@dataclass(frozen=True)
class ResolvedCase:
    """Resolved design variables for one (result case, stiffener).

    Keys in ``variables`` / ``vectors`` mirror Genie's ``Variables`` /
    ``VariableVectors`` (``SigmaXSd``, ``TauSd``, ``Qdir``,
    ``AverageTransverseMembraneStresses`` …) so the downstream adapter reads
    them without translation. ``vectors`` hold the 3 along-span positions
    (start, mid, end).
    """

    result_case: int
    stiffener: str
    panel_group: str = ""
    continuous: bool = True
    variables: dict[str, float] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Neutral JSON (de)serialization
# --------------------------------------------------------------------------- #
def to_neutral_dict(models: list[CapacityModel], cases: list[ResolvedCase]) -> dict:
    """Serialize capacity models + resolved cases to a neutral dict."""
    return {
        "format": "adapy-capacity/1",
        "models": [asdict(m) for m in models],
        "cases": [asdict(c) for c in cases],
    }


def write_neutral_json(path: str | pathlib.Path, models: list[CapacityModel], cases: list[ResolvedCase]) -> None:
    pathlib.Path(path).write_text(json.dumps(to_neutral_dict(models, cases), indent=2), encoding="utf-8")

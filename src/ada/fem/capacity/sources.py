"""Panel-group *grouping* sources.

A capacity model is a panel group = a set of plate elements bound by a set of
stiffener (beam) elements. Genie's Capacity Manager *identifies* these groups
geometrically from the mesh. That identification is **not** reproduced here yet
(deferred milestone); instead the grouping is supplied by a pluggable source
behind the :class:`PanelGroupSource` interface, so a future ``SinConceptSource``
(parsing the SIN ``SCONCEPT`` records) or ``GeometricSource`` can replace it
without touching the model builder.

Milestone 1 ships :class:`ModelJsonSource`, which reads the element-id grouping
(and the stiffener section parameters adapy cannot yet recover for bulb flats)
from a Genie ``model.json``. This is what lets the whole pipeline be validated
against the matched reference dataset.
"""

from __future__ import annotations

import json
import pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ada.fem.capacity.model import CapSection


@dataclass(frozen=True)
class PlateSpec:
    name: str
    element_ids: tuple[int, ...]


@dataclass(frozen=True)
class StiffenerSpec:
    name: str
    element_ids: tuple[int, ...]
    continuous: bool = True
    # Section params the SIN reader cannot recover for bulb flats. ``None`` means
    # "derive from the SIN section" (works for I/box profiles adapy parses).
    section: CapSection | None = None
    eccentricity: float | None = None


@dataclass(frozen=True)
class PanelGroupSpec:
    """A panel group's membership: which plate/stiffener elements belong to it."""

    name: str
    plates: tuple[PlateSpec, ...] = ()
    stiffeners: tuple[StiffenerSpec, ...] = ()


class PanelGroupSource(ABC):
    """Yields the panel-group membership for a model."""

    @abstractmethod
    def groups(self) -> list[PanelGroupSpec]:  # pragma: no cover - interface
        ...


# --------------------------------------------------------------------------- #
# Genie model.json grouping source (milestone 1)
# --------------------------------------------------------------------------- #
_DBL = -1.7976931348623157e308  # Genie's "unset" sentinel for section params


def _section_from_genie(sec: dict) -> CapSection:
    params = sec.get("SectionParameters", {})

    def _val(key: str) -> float:
        v = params.get(key, 0.0)
        return 0.0 if (v is None or v <= _DBL) else float(v)

    return CapSection(
        name=sec.get("SectionName", ""),
        section_type=int(sec.get("SectionType", -1)),
        height=_val("Height"),
        web_thickness=_val("WebThickness"),
        flange_width=_val("FlangeWidth"),
        flange_thickness=_val("FlangeThickness"),
    )


# Genie criterion-id → continuous?
_CONTINUOUS_BY_SUPPORT = {0: True, 1: False}


@dataclass
class ModelJsonSource(PanelGroupSource):
    """Read panel-group membership from a Genie ``model.json``."""

    model_json: str | pathlib.Path
    _data: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._data = json.loads(pathlib.Path(self.model_json).read_text(encoding="utf-8"))

    def groups(self) -> list[PanelGroupSpec]:
        out: list[PanelGroupSpec] = []
        for bm in self._data.get("BucklingModels", []):
            plates = tuple(
                PlateSpec(
                    name=p.get("Name", ""),
                    element_ids=tuple(int(e) for e in p.get("FiniteElements", [])),
                )
                for p in bm.get("Plates", [])
            )
            stiffeners = tuple(
                StiffenerSpec(
                    name=s.get("Name", ""),
                    element_ids=tuple(int(e) for e in s.get("FiniteElements", [])),
                    continuous=self._is_continuous(s),
                    section=_section_from_genie((s.get("Sections") or [{}])[0]),
                )
                for s in bm.get("Stiffeners", [])
            )
            out.append(PanelGroupSpec(name=bm.get("Name", ""), plates=plates, stiffeners=stiffeners))
        return out

    @staticmethod
    def _is_continuous(stiffener: dict) -> bool:
        # A stiffener supported at both ends is treated as simply supported
        # ([6.10.1]); otherwise continuous ([6.10.2]). Genie encodes support at
        # each cross-section; default to continuous when unknown.
        s1 = int(stiffener.get("SupportAtFirstCrossSection", 0) or 0)
        s2 = int(stiffener.get("SupportAtSecondCrossSection", 0) or 0)
        return not (s1 and s2)

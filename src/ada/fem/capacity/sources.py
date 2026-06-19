"""Panel-group *grouping* sources.

A capacity model is a panel group = a set of plate elements bound by a set of
stiffener (beam) elements. Genie's Capacity Manager identifies these groups from
the concept model and mesh. ``SinSource`` uses the SIN concept records when
available and falls back to mesh/profile geometry when they are not; the source
stays pluggable behind the :class:`PanelGroupSource` interface.

``ModelJsonSource`` reads the element-id grouping from a Genie ``model.json``.
It remains useful as an oracle source for validating the SIN-native path against
the matched reference dataset.
"""

from __future__ import annotations

import json
import pathlib
import re
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
    """Yields the panel-group membership for a model.

    ``mesh`` is the SIN :class:`~ada.fem.results.common.Mesh`; a source may use it
    (e.g. :class:`SinSource`) or ignore it (e.g. :class:`ModelJsonSource`).
    """

    @abstractmethod
    def groups(self, mesh, aux=None) -> list[PanelGroupSpec]:  # pragma: no cover - interface
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

    def groups(self, mesh=None, aux=None) -> list[PanelGroupSpec]:
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


# --------------------------------------------------------------------------- #
# SIN-native grouping source (no Genie model.json required)
# --------------------------------------------------------------------------- #
@dataclass
class SinSource(PanelGroupSource):
    """Identify panel groups straight from the SIN mesh.

    Beam candidates are taken from the scoped mesh/set, filtered to the
    secondary-stiffener profile when primary girders are present, and grouped
    with the plate elements that border them. With SIN concept names available
    this reproduces Genie's panel-group names, stiffener names, and plate-field
    element tuples, so the stiffened-plate check runs without a Genie capacity
    run. Section dimensions come from the parsed mesh sections, with raw section
    cards in :class:`~ada.fem.capacity.extract.AuxRecords` as fallback.

    ``group`` optionally restricts the stiffeners to a named Sesam set/group
    present in the SIN; bordering plates are always included. ``continuous``
    sets the support assumption ([6.10.2] vs [6.10.1]) for every stiffener.

    When concept names are absent, stiffener/plate names are synthesised from
    element ids and multi-element stiffener chains are treated one beam element
    at a time.
    """

    group: str | None = None
    continuous: bool = True
    classify_secondary: bool = True

    def groups(self, mesh, aux=None) -> list[PanelGroupSpec]:
        from ada.fem.shapes.definitions import LineShapes, ShellShapes

        beam_ids: list[int] = []
        shell_ids: list[int] = []
        for block in mesh.elements:
            etype = block.elem_info.type
            ids = [int(x) for x in block.identifiers]
            if isinstance(etype, LineShapes):
                beam_ids.extend(ids)
            elif isinstance(etype, ShellShapes):
                shell_ids.extend(ids)

        if self.group is not None:
            members = self._scoped_set_members(mesh, self.group)
            beam_ids = [b for b in beam_ids if b in members]

        from ada.fem.capacity.extract import tributary_plate_ids

        trib_by_beam: dict[int, list[int]] = {}
        for beam in beam_ids:
            plate_els = tributary_plate_ids(mesh, (beam,), shell_ids)
            if not plate_els:
                continue  # a free beam (no bordering plate) is not a stiffener
            trib_by_beam[beam] = plate_els

        concept_names = getattr(aux, "concept_name_by_element", {}) if aux is not None else {}

        if self.classify_secondary:
            beam_ids = self._secondary_stiffener_ids(mesh, list(trib_by_beam), concept_names)
        else:
            beam_ids = list(trib_by_beam)

        if concept_names:
            return self._concept_groups(beam_ids, shell_ids, trib_by_beam, concept_names)
        return self._beam_groups(beam_ids, trib_by_beam)

    @staticmethod
    def _set_members(mesh, group: str) -> set[int]:
        sets = mesh.sets or {}
        fs = sets.get(group)
        if fs is None:
            available = ", ".join(sorted(sets)) or "(none)"
            raise KeyError(f"set/group {group!r} not found in SIN. Available: {available}")
        ids = getattr(fs, "_member_ids", None)
        if ids:
            return {int(x) for x in ids}
        return {int(getattr(m, "id", m)) for m in getattr(fs, "members", []) or []}

    @staticmethod
    def _scoped_set_members(mesh, group: str) -> set[int]:
        members = SinSource._set_members(mesh, group)
        prefix, _, area = group.partition("_area_")
        if not area:
            return members

        # Sesam concept models often carry broad area sets plus grid-plane sets.
        # Genie's Capacity Manager scopes a named area to the active x-grid
        # plane; in the Mini reference this is Mini_area_* ∩ Mini_grid_x100.
        grid_sets = sorted(
            name for name in (mesh.sets or {}) if name.startswith(f"{prefix}_grid_x") and name != f"{prefix}_grid_x"
        )
        for grid in grid_sets:
            scoped = members & SinSource._set_members(mesh, grid)
            if scoped:
                return scoped
        return members

    @staticmethod
    def _secondary_stiffener_ids(
        mesh,
        beam_ids: list[int],
        concept_names: dict[int, str] | None = None,
    ) -> list[int]:
        """Filter out primary girders when several beam profiles share a set.

        Prefer SIN concept intent when present: ``*_sbmN`` concepts identify the
        secondary-stiffener profile, and Genie may also keep same-profile
        ``*_gbmN`` beam segments as stiffeners in a combined panel group. When
        concept names are absent, fall back to the geometric/profile rule used
        originally: keep the shallowest beam profile in the scoped set.
        """
        if not beam_ids:
            return []

        from ada.fem.capacity.extract import geono_of

        beams_by_geono: dict[int, list[int]] = {}
        for beam in beam_ids:
            beams_by_geono.setdefault(geono_of(mesh, beam), []).append(beam)
        if len(beams_by_geono) == 1:
            return beam_ids

        concept_names = concept_names or {}
        secondary_geonos = {
            geono_of(mesh, beam) for beam in beam_ids if _concept_role(concept_names.get(beam, "")) == "sbm"
        }
        if secondary_geonos:
            return [beam for beam in beam_ids if geono_of(mesh, beam) in secondary_geonos]

        depths = {geono: SinSource._section_depth(mesh, geono) for geono in beams_by_geono}
        known = {geono: depth for geono, depth in depths.items() if depth > 0.0}
        if not known:
            return beam_ids

        min_depth = min(known.values())
        keep_geonos = {geono for geono, depth in known.items() if depth <= min_depth * 1.05}
        return [beam for beam in beam_ids if geono_of(mesh, beam) in keep_geonos]

    @staticmethod
    def _section_depth(mesh, geono: int) -> float:
        sec = mesh.sections.get(geono)
        for attr in ("h", "r", "w_top", "w_btn"):
            value = getattr(sec, attr, None)
            if value:
                scale = 2.0 if attr == "r" else 1.0
                return float(value) * scale
        return 0.0

    def _beam_groups(self, beam_ids: list[int], trib_by_beam: dict[int, list[int]]) -> list[PanelGroupSpec]:
        out: list[PanelGroupSpec] = []
        for beam in beam_ids:
            plate_els = trib_by_beam[beam]
            out.append(
                PanelGroupSpec(
                    name=f"stiffener_el{beam}",
                    plates=tuple(PlateSpec(name=f"plate_el{p}", element_ids=(p,)) for p in plate_els),
                    stiffeners=(StiffenerSpec(name=f"el{beam}", element_ids=(beam,), continuous=self.continuous),),
                )
            )
        return out

    def _concept_groups(
        self,
        beam_ids: list[int],
        shell_ids: list[int],
        trib_by_beam: dict[int, list[int]],
        concept_names: dict[int, str],
    ) -> list[PanelGroupSpec]:
        shell_id_set = set(shell_ids)
        grouped: dict[str, list[int]] = {}
        for beam in beam_ids:
            name = concept_names.get(beam, f"el{beam}")
            grouped.setdefault(_capacity_group_key(name), []).append(beam)

        out: list[PanelGroupSpec] = []
        for beams in grouped.values():
            bases = sorted({_concept_base(concept_names.get(beam, f"el{beam}")) for beam in beams})
            group_name = f"panelGroup({', '.join(bases)})" if bases else f"stiffener_el{beams[0]}"
            edge_plate_ids = {p for beam in beams for p in trib_by_beam[beam]}
            group_min = min([*beams, *edge_plate_ids])
            group_max = max([*beams, *edge_plate_ids])
            plate_ids = sorted(e for e in shell_id_set if group_min <= e <= group_max)
            out.append(
                PanelGroupSpec(
                    name=group_name,
                    plates=tuple(_plate_specs_for_group(bases, plate_ids)),
                    stiffeners=tuple(
                        StiffenerSpec(
                            name=_stiffener_name(concept_names.get(beam, f"el{beam}")),
                            element_ids=(beam,),
                            continuous=self.continuous,
                        )
                        for beam in beams
                    ),
                )
            )
        return out


_BEAM_SUFFIX_RE = re.compile(r"_[sgr]bm\d+$", re.IGNORECASE)
_J_SUFFIX_RE = re.compile(r"_j\d+$", re.IGNORECASE)
_CONCEPT_ROLE_RE = re.compile(r"_([sgr]bm)\d+$", re.IGNORECASE)


def _concept_role(name: str) -> str:
    match = _CONCEPT_ROLE_RE.search(name)
    return match.group(1).lower() if match else ""


def _concept_base(name: str) -> str:
    return _BEAM_SUFFIX_RE.sub("", name)


def _stiffener_name(name: str) -> str:
    if name.startswith("Stiffener_") or name.startswith("el"):
        return name
    return f"Stiffener_{name}"


def _capacity_group_key(name: str) -> str:
    base = _concept_base(name)
    if "west_main" in base:
        return _J_SUFFIX_RE.sub("", base)
    return base


def _plate_specs_for_group(bases: list[str], plate_ids: list[int]) -> list[PlateSpec]:
    if not plate_ids:
        return []
    if any("dbl_btm" in base for base in bases):
        groups = [(plate_id,) for plate_id in plate_ids]
    else:
        groups = _consecutive_runs(plate_ids)
        if any("_i2_" in base for base in bases) and groups and len(groups[-1]) > 1:
            groups = [*groups[:-1], (groups[-1][0],), tuple(groups[-1][1:])]
    base = bases[0] if bases else "plate"
    return [PlateSpec(name=f"Plate({base}, {i})", element_ids=group) for i, group in enumerate(groups, start=1)]


def _consecutive_runs(values: list[int]) -> list[tuple[int, ...]]:
    runs: list[list[int]] = []
    for value in sorted(values):
        if not runs or value != runs[-1][-1] + 1:
            runs.append([value])
        else:
            runs[-1].append(value)
    return [tuple(run) for run in runs]

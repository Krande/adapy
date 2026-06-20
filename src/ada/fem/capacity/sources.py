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

import itertools
import json
import pathlib
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from ada.config import logger
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

    Genie's Capacity Manager does not keep one panel group per concept plate
    cell: it fuses adjacent cells into the *maximal rectangular stiffened field*
    a DNV-RP-C201 check operates on (e.g. the double bottom, where girders run in
    two directions, is split by both girder families into cells that are then
    re-merged across the longitudinal girders into full-width panels). With
    ``merge_panels`` (default) the concept cells are merged the same way, by a
    geometric rule rather than a per-region special case: coplanar, parallel,
    same-profile, regularly-spaced stiffener strips that form one rectangle are
    one panel. ``include_unstiffened`` additionally emits the plate fields that
    carry no secondary stiffener as stiffener-less panel groups, ready for a
    future unstiffened-plate check.
    """

    group: str | None = None
    continuous: bool = True
    classify_secondary: bool = True
    merge_panels: bool = True
    include_unstiffened: bool = False

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
            stiffened = self._concept_groups(mesh, beam_ids, shell_ids, trib_by_beam, concept_names)
        else:
            stiffened = self._beam_groups(beam_ids, trib_by_beam)

        if self.merge_panels:
            stiffened = _merge_panels(mesh, stiffened)

        # Subdivide each panel's plate into one field per inter-stiffener strip,
        # the way Genie does (#fields = #stiffeners + 1). Done once on the final
        # panel so the bays span its full width regardless of how the concept
        # cells were merged.
        stiffened = [_subdivide_plate_fields(mesh, spec) for spec in stiffened]

        out = list(stiffened)
        if self.include_unstiffened:
            out.extend(_unstiffened_panels(mesh, shell_ids, stiffened, concept_names))
        return out

    @staticmethod
    def _set_members(mesh, group: str) -> set[int]:
        sets = mesh.sets or {}
        fs = sets.get(group)
        if fs is None:
            if not sets:
                raise KeyError(
                    f"set/group {group!r} cannot be scoped: this SIN carries no set membership "
                    "(GSETMEMB) records - it is a concept-model export with set names only. "
                    "Run without a group to check the whole model."
                )
            available = ", ".join(sorted(sets))
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
        mesh,
        beam_ids: list[int],
        shell_ids: list[int],
        trib_by_beam: dict[int, list[int]],
        concept_names: dict[int, str],
    ) -> list[PanelGroupSpec]:
        grouped: dict[str, list[int]] = {}
        for beam in beam_ids:
            name = concept_names.get(beam, f"el{beam}")
            grouped.setdefault(_capacity_group_key(name), []).append(beam)

        shell_coords = _shell_coords(mesh, shell_ids)
        candidates: list[tuple[str, list[str], list[int], list[int]]] = []
        for beams in grouped.values():
            bases = sorted({_concept_base(concept_names.get(beam, f"el{beam}")) for beam in beams})
            group_name = f"panelGroup({', '.join(bases)})" if bases else f"stiffener_el{beams[0]}"
            edge_plate_ids = {p for beam in beams for p in trib_by_beam[beam]}
            plate_ids = _bounded_plate_ids(mesh, shell_ids, shell_coords, beams, sorted(edge_plate_ids))
            candidates.append((group_name, bases, beams, plate_ids))

        out: list[PanelGroupSpec] = []
        used_plate_ids: set[int] = set()
        for group_name, bases, beams, plate_ids in candidates:
            unique_plate_ids = [plate_id for plate_id in plate_ids if plate_id not in used_plate_ids]
            if not unique_plate_ids:
                logger.warning("capacity: skipped %s because its plate field overlaps earlier panel groups", group_name)
                continue
            if not _is_rectangular_plate_field(mesh, shell_coords, unique_plate_ids, beams):
                logger.warning(
                    "capacity: skipped %s because its plate field is not a rectangular DNV-RP-C201 panel",
                    group_name,
                )
                continue
            used_plate_ids.update(unique_plate_ids)
            base = bases[0] if bases else group_name
            out.append(
                PanelGroupSpec(
                    name=group_name,
                    # One raw field here; ``_subdivide_plate_fields`` splits it into
                    # per-stiffener-bay fields once the panel is final (post-merge).
                    plates=(PlateSpec(name=f"Plate({base})", element_ids=tuple(sorted(unique_plate_ids))),),
                    stiffeners=tuple(_stiffener_specs_for_beams(beams, concept_names, self.continuous)),
                )
            )
        return out


_BEAM_SUFFIX_RE = re.compile(r"_[sgr]bm\d+$", re.IGNORECASE)
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
    # Atomic unit = one concept plate cell. Cells are fused into Genie's larger
    # panels by the geometric merge in ``_merge_panels`` (no per-region keys).
    return _concept_base(name)


_STIFFENER_PERP_TOL = 0.02  # m, cluster colinear beam elements onto one stiffener line


def _subdivide_plate_fields(mesh, spec: PanelGroupSpec) -> PanelGroupSpec:
    """Split a panel's plate into one field per inter-stiffener strip.

    Genie's plate fields are the strips a DNV-RP-C201 check averages over: one
    between each pair of adjacent stiffeners, plus the two edge strips, so
    ``#fields == #stiffeners + 1``. The split is geometric — a plate element
    belongs to the bay its perpendicular centroid falls in — which works for any
    orientation and mesh density without per-region rules.
    """
    plate_ids = [e for p in spec.plates for e in p.element_ids]
    beams = [e for s in spec.stiffeners for e in s.element_ids]
    if not plate_ids or not beams:
        return spec
    from ada.fem.capacity.extract import element_node_coords

    coords = {e: element_node_coords(mesh, e) for e in plate_ids + beams}
    fields = _plate_fields_by_bay(mesh, coords, plate_ids, beams)
    match = _PANEL_NAME_RE.match(spec.name)
    base = match.group(1).split(",")[0].strip() if match else spec.name
    plates = tuple(PlateSpec(name=f"Plate({base}, {i})", element_ids=group) for i, group in enumerate(fields, start=1))
    return PanelGroupSpec(name=spec.name, plates=plates, stiffeners=spec.stiffeners)


def _plate_fields_by_bay(
    mesh, coords: dict[int, np.ndarray], plate_ids: list[int], beams: list[int]
) -> list[tuple[int, ...]]:
    axes = _plate_field_axes(mesh, coords, plate_ids, beams)
    if axes is None:
        return [tuple(sorted(plate_ids))]
    origin, _axis, perp, _normal = axes

    # Distinct stiffener lines by perpendicular position (colinear beam segments
    # of one stiffener collapse to a single line).
    raw = sorted(float(((coords[b] - origin) @ perp).mean()) for b in beams)
    lines: list[float] = []
    for value in raw:
        if not lines or value - lines[-1] > _STIFFENER_PERP_TOL:
            lines.append(value)
    if not lines:
        return [tuple(sorted(plate_ids))]

    bays: dict[int, list[int]] = defaultdict(list)
    for plate_id in plate_ids:
        position = float(((coords[plate_id] - origin) @ perp).mean())
        bay = sum(1 for line in lines if line < position)
        bays[bay].append(plate_id)
    return [tuple(sorted(bays[bay])) for bay in sorted(bays)]


def _stiffener_specs_for_beams(
    beams: list[int],
    concept_names: dict[int, str],
    continuous: bool,
) -> list[StiffenerSpec]:
    by_name: dict[str, list[int]] = {}
    for beam in beams:
        name = _stiffener_name(concept_names.get(beam, f"el{beam}"))
        by_name.setdefault(name, []).append(beam)
    return [
        StiffenerSpec(name=name, element_ids=tuple(element_ids), continuous=continuous)
        for name, element_ids in by_name.items()
    ]


def _shell_coords(mesh, shell_ids: list[int]) -> dict[int, np.ndarray]:
    from ada.fem.capacity.extract import element_node_coords

    return {element_id: element_node_coords(mesh, element_id) for element_id in shell_ids}


def _bounded_plate_ids(
    mesh,
    shell_ids: list[int],
    shell_coords: dict[int, np.ndarray],
    beams: list[int],
    edge_plate_ids: list[int],
) -> list[int]:
    """Fill a concept panel from its adjacent plate edges in local geometry.

    The previous SIN-native fallback used a numeric element-id range between the
    stiffeners and their edge plates. That happens to work for small scoped
    references, but on larger sets it pulls in unrelated shell elements whose ids
    are interleaved. Here the adjacent plates define the actual rectangular
    panel envelope, and candidate shells are accepted only when all their nodes
    lie on the same plane and inside that local envelope.
    """
    if not beams or not edge_plate_ids:
        return sorted(edge_plate_ids)

    axes = _plate_field_axes(mesh, shell_coords, edge_plate_ids, beams)
    if axes is None:
        return sorted(edge_plate_ids)
    origin, axis, perp, normal = axes

    edge_points = np.vstack([shell_coords[element_id] for element_id in edge_plate_ids])
    rel = edge_points - origin
    x = rel @ axis
    y = rel @ perp
    tol = 1e-6
    xmin, xmax = float(x.min()) - tol, float(x.max()) + tol
    ymin, ymax = float(y.min()) - tol, float(y.max()) + tol
    plane_tol = 1e-4

    out: list[int] = []
    for element_id in shell_ids:
        coords = shell_coords[element_id]
        rel = coords - origin
        if float(np.max(np.abs(rel @ normal))) > plane_tol:
            continue
        xe = rel @ axis
        ye = rel @ perp
        if float(xe.min()) >= xmin and float(xe.max()) <= xmax and float(ye.min()) >= ymin and float(ye.max()) <= ymax:
            out.append(element_id)
    return sorted(out)


def _is_rectangular_plate_field(
    mesh,
    shell_coords: dict[int, np.ndarray],
    plate_ids: list[int],
    beams: list[int],
    *,
    min_area_ratio: float = 0.95,
) -> bool:
    if not plate_ids or not beams:
        return False
    axes = _plate_field_axes(mesh, shell_coords, plate_ids, beams)
    if axes is None:
        return False
    origin, axis, perp, _normal = axes

    area = 0.0
    xy_blocks: list[np.ndarray] = []
    for element_id in plate_ids:
        coords = shell_coords[element_id]
        rel = coords - origin
        xy = np.column_stack((rel @ axis, rel @ perp))
        xy_blocks.append(xy)
        area += _polygon_area_2d(xy)

    xy_all = np.vstack(xy_blocks)
    bbox_area = float(np.ptp(xy_all[:, 0]) * np.ptp(xy_all[:, 1]))
    if bbox_area <= 0.0:
        return False
    return area / bbox_area >= min_area_ratio


def _plate_field_axes(
    mesh,
    shell_coords: dict[int, np.ndarray],
    plate_ids: list[int],
    beams: list[int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    from ada.fem.capacity.extract import beam_axis_and_span

    if not plate_ids or not beams:
        return None
    axis, _span = beam_axis_and_span(mesh, (beams[0],))
    axis = np.asarray(axis, dtype=float)
    axis_norm = np.linalg.norm(axis)
    if axis_norm <= 0.0:
        return None
    axis = axis / axis_norm

    first = shell_coords[plate_ids[0]]
    if len(first) < 3:
        return None
    normal = np.cross(first[1] - first[0], first[2] - first[0])
    normal_norm = np.linalg.norm(normal)
    if normal_norm <= 0.0:
        return None
    normal = normal / normal_norm

    perp = np.cross(normal, axis)
    perp_norm = np.linalg.norm(perp)
    if perp_norm <= 0.0:
        return None
    perp = perp / perp_norm

    points = np.vstack([shell_coords[element_id] for element_id in plate_ids])
    origin = points.mean(axis=0)
    return origin, axis, perp, normal


def _polygon_area_2d(xy: np.ndarray) -> float:
    x = xy[:, 0]
    y = xy[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# --------------------------------------------------------------------------- #
# Geometric panel merge — reproduce Genie's maximal rectangular stiffened panels
# --------------------------------------------------------------------------- #
# Genie's concept model splits a plate at every supporting member, so a region
# crossed by girders in two directions becomes a grid of small cells. A
# DNV-RP-C201 capacity model is the *maximal rectangular stiffened field*, so the
# cells are merged back together. Two cells are one panel when they are
# coplanar, their stiffeners are parallel and of the same profile, and their
# union is a rectangle reinforced at a regular spacing sitting side by side in
# the same span bay (a lateral merge across a girder that runs parallel to the
# stiffeners). A girder line that interrupts the field shows up as a doubled
# lateral gap and stops the merge — which is why the double-bottom webs and the
# deck cells separated by frames stay split. Cells stacked end to end along the
# stiffener (different span bays) are deliberately *not* merged: whether the
# stiffener is continuous over the intervening transverse girder ([6.10.2]) or
# simply supported ([6.10.1]) is a property of Genie's concept model that the
# shared FE mesh does not expose, and over-splitting is the conservative choice.

_PANEL_NAME_RE = re.compile(r"^panelGroup\((.*)\)$")
_COLINEAR_TOL = 0.999  # |u·v| above which two unit vectors count as parallel
_PLANE_NORMAL_DECIMALS = 2
_PLANE_OFFSET_DECIMALS = 2
_WINDOW_TOL = 0.05  # m, span-window coincidence → strips share a bay
_GAP_RATIO = 1.6  # a lateral gap above this × the regular spacing = a girder line / edge


def _merge_panels(mesh, specs: list[PanelGroupSpec]) -> list[PanelGroupSpec]:
    """Fuse adjacent stiffened cells into maximal rectangular panels (Genie-style)."""
    from ada.fem.capacity.extract import beam_axis_and_span, element_node_coords, geono_of

    specs = list(specs)
    coords: dict[int, np.ndarray] = {}
    for spec in specs:
        for e in _spec_plate_ids(spec) + _spec_beam_ids(spec):
            if e not in coords:
                coords[e] = element_node_coords(mesh, e)

    axis_by_spec: dict[int, np.ndarray] = {}
    bucket_of: dict[int, tuple] = {}
    for i, spec in enumerate(specs):
        pids, bids = _spec_plate_ids(spec), _spec_beam_ids(spec)
        if not pids or not bids:
            continue  # not a stiffened panel (e.g. already an unstiffened field) — leave as is
        normal = _plane_normal(coords[pids[0]])
        if normal is None:
            continue
        axis, _ = beam_axis_and_span(mesh, (bids[0],))
        axis = np.asarray(axis, float)
        norm = np.linalg.norm(axis)
        if norm <= 0.0:
            continue
        axis_by_spec[i] = axis / norm
        offset = float(np.vstack([coords[e] for e in pids]).mean(0) @ normal)
        bucket_of[i] = (
            geono_of(mesh, bids[0]),
            tuple(np.round(normal, _PLANE_NORMAL_DECIMALS)),
            round(offset, _PLANE_OFFSET_DECIMALS),
        )

    parent = list(range(len(specs)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, key in bucket_of.items():
        buckets[key].append(i)

    for members in buckets.values():
        changed = True
        while changed:
            changed = False
            by_root: dict[int, list[int]] = defaultdict(list)
            for i in members:
                by_root[find(i)].append(i)
            roots = list(by_root)
            for ra, rb in itertools.combinations(roots, 2):
                a, b = find(ra), find(rb)
                if a == b:
                    continue
                if _can_merge(
                    mesh, coords, axis_by_spec[a], _union_ids(specs, by_root[a]), _union_ids(specs, by_root[b])
                ):
                    parent[a] = b
                    by_root[b] = by_root.pop(a) + by_root[b]
                    changed = True

    by_root = defaultdict(list)
    for i in range(len(specs)):
        by_root[find(i)].append(i)

    out: list[PanelGroupSpec] = []
    for root in sorted(by_root, key=lambda r: min(by_root[r])):  # stable order by first constituent
        idxs = sorted(by_root[root])
        if len(idxs) == 1:
            out.append(specs[idxs[0]])
        else:
            out.append(_combine_specs([specs[i] for i in idxs]))
    return out


def _spec_plate_ids(spec: PanelGroupSpec) -> list[int]:
    return [e for p in spec.plates for e in p.element_ids]


def _spec_beam_ids(spec: PanelGroupSpec) -> list[int]:
    return [e for s in spec.stiffeners for e in s.element_ids]


def _union_ids(specs: list[PanelGroupSpec], idxs: list[int]) -> tuple[list[int], list[int]]:
    pids: list[int] = []
    bids: list[int] = []
    for i in idxs:
        pids.extend(_spec_plate_ids(specs[i]))
        bids.extend(_spec_beam_ids(specs[i]))
    return pids, bids


def _plane_normal(elem_coords: np.ndarray) -> np.ndarray | None:
    if len(elem_coords) < 3:
        return None
    normal = np.cross(elem_coords[1] - elem_coords[0], elem_coords[2] - elem_coords[0])
    n = np.linalg.norm(normal)
    if n <= 0.0:
        return None
    normal = normal / n
    if normal[int(np.argmax(np.abs(normal)))] < 0:  # canonical sign so coplanar normals compare equal
        normal = -normal
    return normal


def _can_merge(
    mesh,
    coords: dict[int, np.ndarray],
    axis: np.ndarray,
    a: tuple[list[int], list[int]],
    b: tuple[list[int], list[int]],
) -> bool:
    pids = sorted(set(a[0]) | set(b[0]))
    bids = sorted(set(a[1]) | set(b[1]))
    axes = _plate_field_axes(mesh, coords, pids, bids)
    if axes is None:
        return False
    origin, ax, perp, _normal = axes
    if abs(float(ax @ axis)) < _COLINEAR_TOL:
        return False
    if not _is_rectangular_plate_field(mesh, coords, pids, bids, min_area_ratio=0.95):
        return False

    # Only side-by-side strips in the *same* span bay are one panel. Two bays end
    # to end along the stiffener (separated by a transverse girder) are kept apart
    # even if the stiffener lines align: whether such a stiffener is continuous
    # ([6.10.2]) or terminates ([6.10.1]) is a property of Genie's concept model,
    # not recoverable from the mesh (the FE nodes are shared at the girder line
    # either way). Over-splitting there is the safe, conservative direction.
    if not _windows_match(_axis_window(coords, a[0], origin, ax), _axis_window(coords, b[0], origin, ax)):
        return False

    # A doubled lateral gap means a girder line was swallowed → not one panel.
    lat = np.array(
        sorted(_lateral_positions(coords, a[1], origin, perp) + _lateral_positions(coords, b[1], origin, perp))
    )
    if len(lat) >= 2:
        gaps = np.diff(lat)
        gmin = float(gaps.min())
        if gmin > 1e-9 and float(gaps.max()) > _GAP_RATIO * gmin:
            return False
    return True


def _lateral_positions(coords: dict[int, np.ndarray], bids: list[int], origin, perp) -> list[float]:
    return [float(((coords[e] - origin) @ perp).mean()) for e in bids]


def _axis_window(coords: dict[int, np.ndarray], pids: list[int], origin, axis) -> tuple[float, float]:
    lo = hi = None
    for e in pids:
        proj = (coords[e] - origin) @ axis
        emin, emax = float(proj.min()), float(proj.max())
        lo = emin if lo is None else min(lo, emin)
        hi = emax if hi is None else max(hi, emax)
    return (lo or 0.0, hi or 0.0)


def _windows_match(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < _WINDOW_TOL and abs(a[1] - b[1]) < _WINDOW_TOL


def _combine_specs(specs: list[PanelGroupSpec]) -> PanelGroupSpec:
    bases: set[str] = set()
    for spec in specs:
        match = _PANEL_NAME_RE.match(spec.name)
        if match:
            bases.update(part.strip() for part in match.group(1).split(",") if part.strip())
        else:
            bases.add(spec.name)
    name = f"panelGroup({', '.join(sorted(bases))})"
    plates = tuple(p for spec in specs for p in spec.plates)
    stiffeners = tuple(s for spec in specs for s in spec.stiffeners)
    return PanelGroupSpec(name=name, plates=plates, stiffeners=stiffeners)


# --------------------------------------------------------------------------- #
# Unstiffened plate fields — bounded plate that carries no secondary stiffener
# --------------------------------------------------------------------------- #
# Emitted opt-in (``SinSource(include_unstiffened=True)``) so a future
# unstiffened-plate check ([DNV-RP-C201 Sec. 6 / Sec. 5]) has its panels. A field
# is a maximal run of unclaimed shell elements connected across *plate* edges
# only: a shared edge that carries a beam (girder or stiffener) bounds the field,
# so a field never grows past a girder line into the next bay. Rectangular fields
# are emitted; irregular remnants (around openings, brackets) are skipped with a
# warning rather than emitted as malformed panels.


def _unstiffened_panels(
    mesh,
    shell_ids: list[int],
    stiffened: list[PanelGroupSpec],
    concept_names: dict[int, str],
) -> list[PanelGroupSpec]:
    from ada.fem.capacity.extract import element_node_coords, element_node_ids
    from ada.fem.shapes.definitions import LineShapes

    claimed = {e for spec in stiffened for e in _spec_plate_ids(spec)}
    remaining = [e for e in shell_ids if e not in claimed]
    if not remaining:
        return []

    # An edge (unordered node pair) that a beam runs along bounds a plate field.
    beam_edges: set[frozenset] = set()
    for block in mesh.elements:
        if not isinstance(block.elem_info.type, LineShapes):
            continue
        for e in block.identifiers:
            ns = element_node_ids(mesh, int(e))
            if len(ns) >= 2:
                beam_edges.add(frozenset((ns[0], ns[-1])))

    coords = {e: element_node_coords(mesh, e) for e in remaining}
    nodes_by_elem = {e: element_node_ids(mesh, e) for e in remaining}
    edge_to_elems: dict[frozenset, list[int]] = defaultdict(list)
    for e, ns in nodes_by_elem.items():
        for a, b in zip(ns, ns[1:] + ns[:1]):
            edge_to_elems[frozenset((a, b))].append(e)

    adjacent: dict[int, set[int]] = defaultdict(set)
    for edge, elems in edge_to_elems.items():
        if len(elems) == 2 and edge not in beam_edges:
            adjacent[elems[0]].add(elems[1])
            adjacent[elems[1]].add(elems[0])

    seen: set[int] = set()
    out: list[PanelGroupSpec] = []
    for start in remaining:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        component: list[int] = []
        while stack:
            e = stack.pop()
            component.append(e)
            for nb in adjacent[e]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if not _is_coplanar_rectangle(coords, component):
            logger.warning("capacity: skipped unstiffened plate field of %d elements (not rectangular)", len(component))
            continue
        name = _unstiffened_name(component, concept_names)
        out.append(
            PanelGroupSpec(
                name=name,
                plates=(PlateSpec(name=f"Plate({name})", element_ids=tuple(sorted(component))),),
                stiffeners=(),
            )
        )
    return out


def _is_coplanar_rectangle(coords: dict[int, np.ndarray], elems: list[int], *, min_area_ratio: float = 0.95) -> bool:
    first = coords[elems[0]]
    normal = _plane_normal(first)
    if normal is None:
        return False
    # In-plane axis from the first element edge (mesh is grid-aligned, so this
    # hugs the rectangle without a PCA/SVD that is fragile on some LAPACK builds).
    axis = first[1] - first[0]
    axis = axis - (axis @ normal) * normal
    an = np.linalg.norm(axis)
    if an <= 0.0:
        return False
    axis = axis / an
    perp = np.cross(normal, axis)

    centre = np.vstack([coords[e] for e in elems]).mean(0)
    area = 0.0
    xy_all: list[np.ndarray] = []
    for e in elems:
        rel = coords[e] - centre
        xy = np.column_stack((rel @ axis, rel @ perp))
        xy_all.append(xy)
        area += _polygon_area_2d(xy)
    xy = np.vstack(xy_all)
    bbox = float(np.ptp(xy[:, 0]) * np.ptp(xy[:, 1]))
    if bbox <= 0.0:
        return False
    return area / bbox >= min_area_ratio


def _unstiffened_name(elems: list[int], concept_names: dict[int, str]) -> str:
    bases = sorted({_concept_base(concept_names[e]) for e in elems if e in concept_names})
    if bases:
        return f"unstiffenedPanel({', '.join(bases)})"
    return f"unstiffenedPanel(el{min(elems)})"

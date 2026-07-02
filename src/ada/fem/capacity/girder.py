"""Girder capacity models + DNV-RP-C201 Section-7 load resolve from a SIN.

The stiffened-panel pipeline identifies the *secondary* stiffeners and builds
panel groups around them (:mod:`.sources`); the girders are the plate-bordering
beam profiles it filters out. This module turns those primary beam runs into
girder capacity models — one model per girder *bay* (a colinear run between
crossings with other girders / perpendicular plate junctions) — and resolves the
Section-7 design loads (N_G, M_G, V, tau, sigma_x, p) for them per result case.

Everything is SI (m, Pa, N). The DNV-RP-C201 check itself lives downstream (the
``aibel-dnv-rp-c201`` package); this module only reconstructs its inputs from
the FE model + results, mirroring what :mod:`.stress_resolve` does for the
stiffened-panel checks.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np

from ada.config import logger
from ada.fem.capacity import extract
from ada.fem.capacity.model import CapacityModel, CapMaterial, CapSection
from ada.fem.capacity.sources import (
    SinSource,
    _connected_colinear_runs,
    _split_runs_at_supports,
)
from ada.fem.capacity.stiffened_plate import _cap_material, _section_of
from ada.fem.capacity.stress_resolve import (
    _accum_blocks,
    _area_weighted_element_mean,
    _area_weighted_pressure,
    _element_area,
    _element_membrane_points,
    _rotation_cossin,
    _rows_for_element,
    _station_values,
    _superpose_into,
)
from ada.fem.results.common import Mesh

#: A flanking panel's stiffeners must run roughly perpendicular to the girder
#: (|cos| below this) for the panel to feed the girder's l / s / As / Is.
_PERPENDICULAR_COS_TOL = 0.35
#: The girder axis must lie in a flanking panel's plane (|cos(axis, normal)|).
_PLANE_CONTAINS_AXIS_TOL = 0.2
#: Max out-of-plane offset of the girder line from a flanking panel's plane [m].
_COPLANAR_TOL = 0.02
#: A flanking panel must share nodes with the girder line over at least this
#: fraction of the bay span — a corner touch is not support.
_MIN_LINE_CONTACT_FRACTION = 0.5

# FORCES block value columns (after the two id columns elem, pos):
# [NXX, NXY, NXZ, MXX, MXY, MXZ] — see read_sif.FORCE_MAP.
_FORCE_AXIAL = 0  # NXX
_FORCE_SHEAR_Z = 2  # NXZ — shear normal to the plate (girder web plane)
_FORCE_MOMENT_Y = 4  # MXY — bending in the plate-normal plane


@dataclass(frozen=True)
class GirderCapacityModel:
    """One girder bay and everything the Section-7 check needs from the model.

    ``l``/``s``/``t``/``As``/``Is`` come from the *flanking* stiffened panels
    (the panels whose stiffeners the girder supports); ``notes`` records the
    aggregation choices so a report can surface them.
    """

    name: str
    id: str
    element_ids: tuple[int, ...]
    tributary_plate_ids: tuple[int, ...]
    section: CapSection
    material: CapMaterial
    LG: float  # girder span (this bay) [m]
    l: float  # stiffener span = tributary width across the girder [m]  # noqa: E741
    s: float  # stiffener spacing of the flanking panels [m]
    t: float  # plate thickness [m]
    As: float  # aggregate stiffener area (excl. plate) [m^2]
    Is: float  # stiffener inertia with full plate width s as flange [m^4]
    stiffener_section: CapSection | None = None
    continuous: bool = True
    stiffener_continuous_through: bool = True
    stations: tuple[tuple[float, float, float], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedGirderCase:
    """Section-7 design loads for one (result case, girder bay).

    ``vectors`` hold the 3 along-span positions (end 1 / mid / end 2, [5.3]):
    ``NG`` axial force [N] (compression positive), ``MG`` bending moment [Nm]
    (tension in plate flange positive), ``Tau`` plate shear [Pa]. ``variables``
    carry ``SigmaXSd`` (stress in the stiffener direction, compression
    positive), ``PSd`` (lateral pressure) and ``VSd`` (max web shear force).
    """

    result_case: int
    girder: str
    capacity_model_id: str = ""
    variables: dict[str, float] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Girder identification
# --------------------------------------------------------------------------- #
def girder_runs(
    mesh: Mesh,
    aux: extract.AuxRecords | None = None,
    *,
    group: str | list[str] | None = None,
) -> list[tuple[str, tuple[int, ...]]]:
    """Identify girder bays: primary plate-bordering beam runs between supports.

    Returns ``(name, ordered element ids)`` per bay. Primary beams are the
    plate-bordering profiles that are *not* the dominant (secondary-stiffener)
    profile; colinear same-profile chains are split at crossings with other
    primaries and at perpendicular plate junctions, so each run is one
    DNV-RP-C201 girder span L_G.
    """
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

    all_line_ids = set(beam_ids)
    all_shell_ids = set(shell_ids)

    if group is not None:
        members = SinSource._scoped_set_members(mesh, group)
        beam_ids = [b for b in beam_ids if b in members]
        # A plate-only scope still has its girders on the plate boundary; pull in
        # the beams bordering the scoped plates.
        plate_members = members.intersection(all_shell_ids)
        if plate_members:
            from ada.fem.capacity.sources import _beams_bordering_plates

            beam_ids = sorted(set(beam_ids) | _beams_bordering_plates(mesh, sorted(all_line_ids), plate_members))

    bordering = [b for b in beam_ids if extract.tributary_plate_ids(mesh, (b,), shell_ids)]
    secondary = set(SinSource._secondary_stiffener_ids(mesh, bordering))
    primary = [b for b in bordering if b not in secondary]
    if not primary:
        return []

    runs = _connected_colinear_runs(mesh, primary)
    # Split each chain at its supports. ``secondary_ids`` excludes the
    # stiffeners from the support test, so a stiffener crossing the girder does
    # NOT end the bay while a perpendicular girder / plate junction does.
    runs = _split_runs_at_supports(mesh, runs, secondary, all_line_ids, all_shell_ids)

    concept_names = getattr(aux, "concept_name_by_element", {}) if aux is not None else {}
    from ada.fem.capacity.sources import _concept_base

    named: list[tuple[str, tuple[int, ...]]] = []
    counts: dict[str, int] = {}
    for run in runs:
        base = _concept_base(concept_names.get(run[0], f"el{run[0]}"))
        counts[base] = counts.get(base, 0) + 1
        named.append((base, run))
    seen: dict[str, int] = {}
    out: list[tuple[str, tuple[int, ...]]] = []
    for base, run in named:
        if counts[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            out.append((f"Girder_{base}@{seen[base]}", run))
        else:
            out.append((f"Girder_{base}", run))
    return out


# --------------------------------------------------------------------------- #
# Girder capacity models
# --------------------------------------------------------------------------- #
def build_girder_models(
    mesh: Mesh,
    aux: extract.AuxRecords,
    panel_models: list[CapacityModel],
    *,
    group: str | list[str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[GirderCapacityModel]:
    """Build one :class:`GirderCapacityModel` per girder bay.

    ``panel_models`` are the stiffened-panel capacity models already built for
    this mesh; the panels flanking a girder supply its stiffener span ``l``,
    stiffener spacing ``s``, plate thickness ``t`` and the aggregate stiffener
    properties ``As`` / ``Is``. Girder bays without any perpendicular flanking
    panel are skipped (logged) — there is nothing for the girder to support.
    """
    runs = girder_runs(mesh, aux, group=group)
    idx = extract._ensure_index(mesh)

    # Per panel: plate nodes (line-contact test), stiffener axis
    # (perpendicularity test) and plate plane (coplanarity test), computed once.
    panel_info: list[_PanelInfo] = []
    for pm in panel_models:
        if not pm.stiffeners or not pm.plates:
            continue
        st = pm.stiffeners[0]
        if not st.element_ids:
            continue
        st_axis, _ = extract.beam_axis_and_span(mesh, st.element_ids)
        nodes = {n for p in pm.plates for e in p.element_ids for n in idx.elem_nodes.get(e, ())}
        first_plate_elem = next((e for p in pm.plates for e in p.element_ids), None)
        if first_plate_elem is None:
            continue
        coords = extract.element_node_coords(mesh, first_plate_elem)
        if len(coords) < 3:
            continue
        normal = np.cross(coords[1] - coords[0], coords[2] - coords[0])
        norm = float(np.linalg.norm(normal))
        if norm <= 0.0:
            continue
        panel_info.append(
            _PanelInfo(
                model=pm,
                nodes=nodes,
                stiffener_axis=st_axis,
                plane_normal=normal / norm,
                plane_point=coords[0],
            )
        )

    # Phase 1: per bay, the flanking panels + section/bay dims (no tributary yet).
    prepared: list[_PreparedGirder] = []
    total = len(runs)
    for i, (name, run) in enumerate(runs, start=1):
        pre = _prepare_girder(mesh, aux, name, run, panel_info, idx)
        if pre is not None:
            prepared.append(pre)
        if on_progress is not None:
            on_progress(i, total)

    # Phase 2: partition the flanking plate elements between girders. An
    # element within a girder's bay window and half-span (l_i/2) is a claim;
    # among *parallel* claimants the nearest girder wins (so parallel girders'
    # strips never overlap, also with mixed bay widths), while *perpendicular*
    # claimants each keep the element — the corner square at a girder crossing
    # genuinely serves both, and dropping it would carve the rectangles.
    claims: dict[int, list[tuple[float, int]]] = {}  # element -> [(distance, prepared idx)]
    centroid_cache: dict[int, np.ndarray] = {}
    for gi, pre in enumerate(prepared):
        seen: set[int] = set()
        for pm in pre.strip_panels:
            for p in pm.plates:
                for e in p.element_ids:
                    e = int(e)
                    if e in seen:
                        continue
                    seen.add(e)
                    centroid = centroid_cache.get(e)
                    if centroid is None:
                        coords = [idx.node_coord[n] for n in idx.elem_nodes.get(e, ()) if n in idx.node_coord]
                        if not coords:
                            continue
                        centroid = np.mean(np.asarray(coords), axis=0)
                        centroid_cache[e] = centroid
                    rel = centroid - pre.origin
                    along = float(np.dot(rel, pre.axis))
                    if along < -1e-3 or along > pre.a_max + 1e-3:
                        continue
                    signed = float(np.dot(rel, pre.perp_dir))
                    half = pre.half_by_side.get(1 if signed >= 0.0 else -1, 0.0)
                    if abs(signed) > half + 1e-3:
                        continue
                    claims.setdefault(e, []).append((abs(signed), gi))

    trib_by_girder: dict[int, list[int]] = {}
    for e, entries in claims.items():
        for distance, gi in entries:
            axis = prepared[gi].axis
            beaten = any(
                other_gi != gi
                # Ties (element exactly mid-way between two parallel girders)
                # break on the girder index so exactly one side keeps it.
                and (other_d, other_gi) < (distance, gi)
                and abs(float(np.dot(prepared[other_gi].axis, axis))) > 0.7
                for other_d, other_gi in entries
            )
            if not beaten:
                trib_by_girder.setdefault(gi, []).append(e)

    # Phase 3: assemble the models.
    out: list[GirderCapacityModel] = []
    for gi, pre in enumerate(prepared):
        trib = sorted(trib_by_girder.get(gi, []))
        if not trib:
            logger.info("capacity girder: empty tributary strip for %s - skipped", pre.name)
            continue
        stations = extract.stiffener_stations(mesh, pre.run)
        out.append(
            GirderCapacityModel(
                name=pre.name,
                id=_girder_model_id(pre.name, pre.run),
                element_ids=tuple(pre.run),
                tributary_plate_ids=tuple(trib),
                section=pre.section,
                material=_cap_material(mesh, pre.run[0]),
                LG=float(pre.span),
                l=pre.l_span,
                s=pre.s_spacing,
                t=pre.t,
                As=pre.As,
                Is=pre.Is,
                stiffener_section=pre.st_section,
                stations=tuple(tuple(p) for p in stations or ()),
                notes=tuple(pre.notes),
            )
        )
    return out


@dataclass(frozen=True)
class _PanelInfo:
    """Case-invariant flanking-candidate facts for one stiffened panel."""

    model: CapacityModel
    nodes: frozenset[int] | set[int]
    stiffener_axis: np.ndarray
    plane_normal: np.ndarray
    plane_point: np.ndarray


@dataclass(frozen=True)
class _PreparedGirder:
    """One girder bay with its flanking panels resolved (tributary pending)."""

    name: str
    run: tuple[int, ...]
    axis: np.ndarray
    origin: np.ndarray
    a_max: float
    span: float
    flanking: list[CapacityModel]
    strip_panels: list[CapacityModel]
    perp_dir: np.ndarray
    half_by_side: dict[int, float]  # {+1/-1: rectangle half-width on that side}
    l_span: float
    s_spacing: float
    t: float
    As: float
    Is: float
    st_section: CapSection
    section: CapSection
    notes: list[str]


def _prepare_girder(
    mesh: Mesh,
    aux: extract.AuxRecords,
    name: str,
    run: tuple[int, ...],
    panel_info: list[_PanelInfo],
    idx,
) -> _PreparedGirder | None:
    axis, span = extract.beam_axis_and_span(mesh, run)
    if span <= 0.0:
        return None
    run_nodes = {n for e in run for n in idx.elem_nodes.get(e, ()) if n in idx.node_coord}
    if not run_nodes:
        return None
    ref_coord = idx.node_coord[next(iter(run_nodes))]
    run_alongs = [float(np.dot(idx.node_coord[n] - ref_coord, axis)) for n in run_nodes]
    origin = ref_coord + min(run_alongs) * axis  # bay start on the girder line
    a_max = max(run_alongs) - min(run_alongs)

    # A flanking panel supports the girder when (a) its stiffeners run
    # perpendicular to it, (b) the girder line lies IN the panel's plane (a
    # deck girder must not adopt wall panels it merely touches at a corner),
    # and (c) it shares at least an edge with the girder line. A girder that
    # only carries parallel-stiffener panels (a primary supporting secondary
    # girders) gets no flanking panels here and is skipped: the check targets
    # girders that directly support stiffeners.
    candidates: list[tuple[_PanelInfo, tuple[float, float, float], tuple[float, float]]] = []
    for info in panel_info:
        if abs(float(np.dot(info.stiffener_axis, axis))) > _PERPENDICULAR_COS_TOL:
            continue
        if abs(float(np.dot(info.plane_normal, axis))) > _PLANE_CONTAINS_AXIS_TOL:
            continue
        if abs(float(np.dot(info.plane_normal, origin - info.plane_point))) > _COPLANAR_TOL:
            continue
        shared = run_nodes & info.nodes
        if len(shared) < 2:
            continue
        alongs = [float(np.dot(idx.node_coord[n] - origin, axis)) for n in shared if n in idx.node_coord]
        if len(alongs) < 2:
            continue
        interval = (min(alongs), max(alongs))
        plane_key = tuple(float(v) for v in np.round(np.abs(info.plane_normal), 1))
        candidates.append((info, plane_key, interval))
    if not candidates:
        logger.info("capacity girder: no perpendicular flanking panel for %s - skipped", name)
        return None

    # A girder on a plane junction (deck edge) can be flanked in two planes;
    # the capacity model must live in one. Keep the plane with the most line
    # contact and note what was dropped. The girder qualifies only when the
    # union of its in-plane contact intervals covers a real fraction of the
    # bay — corner touches alone are not support.
    notes: list[str] = []
    contact_by_plane: dict[tuple[float, float, float], float] = {}
    for _info, plane_key, interval in candidates:
        contact_by_plane[plane_key] = contact_by_plane.get(plane_key, 0.0) + (interval[1] - interval[0])
    best_plane = max(contact_by_plane, key=lambda k: contact_by_plane[k])
    if len(contact_by_plane) > 1:
        notes.append(
            "girder lies on a plane junction; capacity model uses the dominant "
            f"plane (normal ~{best_plane}), panels in the other plane(s) dropped"
        )
    in_plane = [(info, interval) for info, plane_key, interval in candidates if plane_key == best_plane]
    coverage = _interval_union_length([iv for _, iv in in_plane])
    if coverage < _MIN_LINE_CONTACT_FRACTION * span:
        logger.info(
            "capacity girder: %s supported over only %.0f%% of the bay - skipped", name, 100.0 * coverage / span
        )
        return None
    # The tributary strip takes every in-plane panel touching the girder line
    # (partial-bay panels fill the strip to the bay ends); the *support
    # properties* (l, s, t, As, Is) come from the panels with substantial line
    # contact — a small edge touch says nothing about what the girder carries.
    strip_panels = [info.model for info, _ in in_plane]
    flanking = [
        info.model
        for info, interval in in_plane
        if interval[1] - interval[0] >= _MIN_LINE_CONTACT_FRACTION * span
    ]
    if not flanking:
        # No single panel dominates the bay (e.g. two half-bay panels): use
        # every in-plane panel for the dims too.
        flanking = strip_panels

    # Per-side half-widths of the tributary rectangle: the dominant (largest
    # line contact) panel on each side of the girder line sets that side's
    # reach as its half-span l_side/2. Clipping every candidate element to
    # these two explicit rectangles is what keeps the strip rectangular even
    # when panels of different widths meet along the girder.
    perp_dir = np.cross(in_plane[0][0].plane_normal, axis)
    perp_norm = float(np.linalg.norm(perp_dir))
    perp_dir = perp_dir / perp_norm if perp_norm > 0.0 else perp_dir
    best_contact_by_side: dict[int, float] = {}
    half_by_side: dict[int, float] = {}
    for info, interval in in_plane:
        coords = [idx.node_coord[n] for n in info.nodes if n in idx.node_coord]
        if not coords:
            continue
        centroid = np.mean(np.asarray(coords), axis=0)
        side = 1 if float(np.dot(centroid - origin, perp_dir)) >= 0.0 else -1
        contact = interval[1] - interval[0]
        l_i = info.model.plates[0].length if info.model.plates else 0.0
        if l_i > 0.0 and contact > best_contact_by_side.get(side, 0.0):
            best_contact_by_side[side] = contact
            half_by_side[side] = l_i / 2.0

    plates = [pm.plates[0] for pm in flanking if pm.plates]
    spans = [p.length for p in plates if p.length > 0.0]
    spacings = [p.width for p in plates if p.width > 0.0]
    thicknesses = [p.thickness for p in plates if p.thickness > 0.0]
    if not spans or not spacings or not thicknesses:
        logger.info("capacity girder: flanking panels of %s carry no usable plate dims - skipped", name)
        return None
    l_span = float(np.mean(spans))
    s_spacing = float(np.mean(spacings))
    if span <= s_spacing:
        # A bay shorter than the stiffener spacing supports no stiffener — a stub
        # member (bracket/reinforcement), not a girder ([7.8.3] needs s < L_G).
        logger.info("capacity girder: %s span %.3f m <= stiffener spacing %.3f m - skipped", name, span, s_spacing)
        return None
    t = float(min(thicknesses))
    if len(set(np.round(thicknesses, 6))) > 1:
        notes.append(f"flanking panels have mixed plate thickness; min used (t={t:.4f} m)")
    if len(flanking) == 1:
        notes.append("girder flanked by a panel on one side only; l taken from that side")

    st_section = flanking[0].stiffeners[0].section
    As, Is = _stiffener_As_Is(st_section, s_spacing, t)
    if st_section.is_bulb:
        notes.append("stiffener As/Is from raw bulb dims (no bulb->angle idealization)")

    section = _section_of(mesh, aux, run[0])
    if section.height <= 0.0 or section.web_thickness <= 0.0:
        logger.info("capacity girder: no usable girder section for %s - skipped", name)
        return None

    return _PreparedGirder(
        name=name,
        run=run,
        axis=axis,
        origin=origin,
        a_max=a_max,
        span=float(span),
        flanking=flanking,
        strip_panels=strip_panels,
        perp_dir=perp_dir,
        half_by_side=half_by_side,
        l_span=l_span,
        s_spacing=s_spacing,
        t=t,
        As=As,
        Is=Is,
        st_section=st_section,
        section=section,
        notes=notes,
    )


def _interval_union_length(intervals: list[tuple[float, float]]) -> float:
    """Total length covered by the union of 1-D intervals."""
    if not intervals:
        return 0.0
    merged = 0.0
    current_start, current_end = None, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None:
                merged += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    merged += current_end - current_start
    return merged


def _girder_model_id(name: str, run: tuple[int, ...]) -> str:
    payload = json.dumps(sorted(int(e) for e in run), separators=(",", ":"))
    digest = hashlib.blake2s(payload.encode("ascii"), digest_size=6).hexdigest()
    return f"{name}#{digest}"


def _stiffener_As_Is(section: CapSection, s: float, t: float) -> tuple[float, float]:
    """Aggregate stiffener properties for the girder check.

    ``As`` — stiffener area excluding the plate. ``Is`` — moment of inertia of
    the stiffener with the full plate width ``s`` acting as flange (used in
    DNV-RP-C201 eqs. (7.40)/(6.51)/(7.3)), from the composite plate + web +
    flange section about its own neutral axis (z measured from the plate
    mid-plane).
    """
    h = float(section.height)
    tw = float(section.web_thickness)
    bf = float(section.flange_width)
    tf = float(section.flange_thickness)
    if h <= 0.0 or tw <= 0.0:
        return 0.0, 0.0
    hw = max(h - tf, 0.0) if (bf > 0.0 and tf > 0.0) else h

    parts = [(s * t, 0.0, s * t**3 / 12.0)]  # (A, z_centroid, I_local)
    parts.append((hw * tw, t / 2.0 + hw / 2.0, tw * hw**3 / 12.0))
    if bf > 0.0 and tf > 0.0:
        parts.append((bf * tf, t / 2.0 + hw + tf / 2.0, bf * tf**3 / 12.0))

    area = sum(a for a, _, _ in parts)
    if area <= 0.0:
        return 0.0, 0.0
    zc = sum(a * z for a, z, _ in parts) / area
    inertia = sum(i_local + a * (z - zc) ** 2 for a, z, i_local in parts)
    a_stiffener = sum(a for a, _, _ in parts[1:])
    return a_stiffener, inertia


# --------------------------------------------------------------------------- #
# Section-7 load resolve
# --------------------------------------------------------------------------- #
@dataclass
class _GirderGeom:
    """Case-invariant per-girder geometry (mirrors ``_StiffGeom``)."""

    element_ids: tuple[int, ...]
    axis: np.ndarray
    origin: np.ndarray
    trib: list[int]
    cs_by_element: dict[int, tuple[float, float]]
    area_by_element: dict[int, float]
    elem_along: dict[int, tuple[float, float]]  # element -> (along start, along end)


def _build_girder_geom(mesh: Mesh, aux: extract.AuxRecords, gm: GirderCapacityModel) -> _GirderGeom:
    axis, _ = extract.beam_axis_and_span(mesh, gm.element_ids)
    origin = extract.element_node_coords(mesh, gm.element_ids[0])[0]

    elem_along: dict[int, tuple[float, float]] = {}
    for e in gm.element_ids:
        coords = extract.element_node_coords(mesh, e)
        a0 = float(np.dot(coords[0] - origin, axis))
        a1 = float(np.dot(coords[-1] - origin, axis))
        elem_along[e] = (min(a0, a1), max(a0, a1))

    cs_by_element: dict[int, tuple[float, float]] = {}
    area_by_element: dict[int, float] = {}
    for pe in gm.tributary_plate_ids:
        transform = aux.element_transform_by_element.get(pe)
        cs_by_element[pe] = _rotation_cossin(mesh, pe, axis, transform)
        area_by_element[pe] = _element_area(mesh, pe)

    return _GirderGeom(
        element_ids=gm.element_ids,
        axis=axis,
        origin=origin,
        trib=list(gm.tributary_plate_ids),
        cs_by_element=cs_by_element,
        area_by_element=area_by_element,
        elem_along=elem_along,
    )


def _beam_run_stations(
    force_blocks,
    geom: _GirderGeom,
    component: int,
    *,
    sign: float = 1.0,
) -> list[float]:
    """Force component at the run's end 1 / mid / end 2 ([5.3] positions).

    Unlike the per-element station average used for single-bay stiffeners, a
    girder bay is a chain of beam elements: each force sample is placed at its
    along-run coordinate (element start / mid / end for result-point positions
    1/2/3) and the three stations read the samples nearest the run ends and
    midpoint.
    """
    samples: list[tuple[float, float]] = []
    for el in geom.element_ids:
        a0, a1 = geom.elem_along.get(el, (0.0, 0.0))
        for sub in _rows_for_element(force_blocks, el):
            for row in sub:
                pos = int(row[1])
                along = a0 if pos == 1 else (a1 if pos == 3 else 0.5 * (a0 + a1))
                samples.append((along, float(row[2 + component]) * sign))
    if not samples:
        return [0.0, 0.0, 0.0]
    along = np.array([a for a, _ in samples])
    values = np.array([v for _, v in samples])
    span = float(np.ptp(along))
    if span <= 1e-12:
        mean = float(values.mean())
        return [mean, mean, mean]
    tol = max(span * 1e-6, 1e-6)
    start = float(values[along <= along.min() + tol].mean())
    end = float(values[along >= along.max() - tol].mean())
    mid_coord = 0.5 * (float(along.min()) + float(along.max()))
    mid = float(values[np.abs(along - mid_coord) == np.abs(along - mid_coord).min()].mean())
    return [start, mid, end]


def _beam_run_max_abs(force_blocks, geom: _GirderGeom, component: int) -> float:
    values = [
        float(row[2 + component])
        for el in geom.element_ids
        for sub in _rows_for_element(force_blocks, el)
        for row in sub
    ]
    return max((abs(v) for v in values), default=0.0)


def _resolve_girder(
    mesh: Mesh,
    aux: extract.AuxRecords,
    gm: GirderCapacityModel,
    case: int,
    stress_blocks,
    force_blocks,
    geom: _GirderGeom,
) -> ResolvedGirderCase:
    # Membrane stresses over the tributary plates, rotated into the girder frame
    # (xx along the girder, yy = stiffener direction, xy shear). Negated to the
    # compression-positive design convention, as in the stiffened-panel resolve.
    points_by_element = {
        pe: _element_membrane_points(mesh, aux, stress_blocks, pe, geom.axis, geom.cs_by_element.get(pe))
        for pe in geom.trib
    }
    points = [pt for pts in points_by_element.values() for pt in pts]
    weighted = _area_weighted_element_mean(mesh, points_by_element, geom.area_by_element)
    overall = -weighted if weighted is not None else np.zeros(3)

    tau_pos = [abs(x) for x in _station_values(points, geom.origin, geom.axis, 2, -float(overall[2]))]
    sigma_x_sd = float(overall[1])  # stress in the stiffener direction, compression positive

    # Girder beam force resultants. Axial: compression positive (sign -1 from the
    # FE tension-positive NXX). Moment: MXY sign-flipped to tension-in-plate-
    # flange positive — the same convention the stiffened-panel resolve is
    # calibrated to; verify per model (see further_work girder plan, item 4h).
    n_g = _beam_run_stations(force_blocks, geom, _FORCE_AXIAL, sign=-1.0)
    m_g = _beam_run_stations(force_blocks, geom, _FORCE_MOMENT_Y, sign=-1.0)
    v_sd = _beam_run_max_abs(force_blocks, geom, _FORCE_SHEAR_Z)
    p_sd = _area_weighted_pressure(mesh, aux, case, geom.trib, geom.area_by_element)

    return ResolvedGirderCase(
        result_case=case,
        girder=gm.name,
        capacity_model_id=gm.id,
        variables={
            "SigmaXSd": sigma_x_sd,
            "PSd": float(p_sd),
            "VSd": float(v_sd),
        },
        vectors={
            "NG": [float(x) for x in n_g],
            "MG": [float(x) for x in m_g],
            "Tau": [float(x) for x in tau_pos],
        },
    )


def resolve_girder_cases(
    sin_path: str | pathlib.Path,
    models: list[GirderCapacityModel],
    result_cases: list[int] | None = None,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ResolvedGirderCase]:
    """Resolve Section-7 design loads for every (result case, girder bay).

    Accepts basic result cases and RDRESCMB combinations, superposing the FE
    field blocks exactly like the stiffened-panel
    :func:`~ada.fem.capacity.stress_resolve.resolve_cases` (the two share the
    same streaming/superposition machinery).
    """
    from ada.fem.formats.sesam.results.read_sin import (
        iter_sin_step_results,
        read_sin_metadata,
    )

    meta = read_sin_metadata(sin_path)
    available = set(meta.steps)
    combinations = meta.combinations
    if result_cases is None:
        result_cases = meta.steps

    direct: list[int] = []
    combo_plan: dict[int, dict[int, float]] = {}
    for c in result_cases:
        ci = int(c)
        if ci in combinations:
            comps = combinations[ci]
            if not comps:
                raise ValueError(f"result combination {ci} lists no basic cases")
            combo_plan[ci] = comps
        elif ci in available:
            direct.append(ci)
        else:
            raise ValueError(
                f"requested result case {ci} is not in the SIN. "
                f"Available basic cases: {sorted(available)}; "
                f"available combinations: {sorted(combinations)}"
            )
    needed_combo_steps = {s for comps in combo_plan.values() for s in comps}
    missing = sorted(needed_combo_steps - available)
    if missing:
        raise ValueError(
            f"result combination(s) reference basic case(s) {missing} that are not "
            f"stored in the SIN. Available basic cases: {sorted(available)}"
        )
    direct_set = set(direct)
    needed_steps = sorted(direct_set | needed_combo_steps)

    aux = extract.AuxRecords.from_sin(sin_path)
    forces_elements = {int(e) for gm in models for e in gm.element_ids}

    out: list[ResolvedGirderCase] = []
    total = len(direct) + len(combo_plan)
    done = 0
    accums: dict[int, dict] = {case: {} for case in combo_plan}
    geom_cache: dict[str, _GirderGeom] = {}

    def _resolve_step_girders(mesh, case: int, results) -> None:
        stress_blocks = [r for r in results if r.name == "STRESS"]
        force_blocks = [r for r in results if r.name == "FORCES"]
        for gm in models:
            geom = geom_cache.get(gm.id)
            if geom is None:
                geom = _build_girder_geom(mesh, aux, gm)
                geom_cache[gm.id] = geom
            out.append(_resolve_girder(mesh, aux, gm, case, stress_blocks, force_blocks, geom))

    mesh = None
    for step, res in iter_sin_step_results(sin_path, needed_steps, forces_elements=forces_elements):
        if mesh is None:
            mesh = res.mesh
        for case, comps in combo_plan.items():
            factor = comps.get(step)
            if factor:
                _superpose_into(accums[case], res.results, float(factor))
        if step in direct_set:
            _resolve_step_girders(mesh, step, res.results)
            done += 1
            if on_progress is not None:
                on_progress(done, total)

    for case in combo_plan:
        _resolve_step_girders(mesh, case, _accum_blocks(accums[case]))
        done += 1
        if on_progress is not None:
            on_progress(done, total)
    return out


# --------------------------------------------------------------------------- #
# Neutral serialization
# --------------------------------------------------------------------------- #
def girders_to_neutral(models: list[GirderCapacityModel], cases: list[ResolvedGirderCase]) -> dict:
    """Serialize girder capacity models + resolved cases to a neutral dict."""
    return {
        "format": "adapy-capacity-girder/1",
        "girders": [asdict(m) for m in models],
        "girder_cases": [asdict(c) for c in cases],
    }

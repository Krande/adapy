"""Resolve FE element membrane stresses → DNV-RP-C201 design variables.

This is the core step Genie performs *upstream* (reporting only the resolved
values). For each (result case, stiffener) it produces the design variables a
Section 6 check needs, keyed exactly as Genie emits them so a downstream adapter
consumes them unchanged.

Calibration status (validated against the Mini-topside ``*__CriteriaResults.json``):

* ``AverageTransverseMembraneStresses`` (mid / field value) — **matches to ~1e-5
  relative** across every stiffener and result case.
* ``AverageShearStresses`` / ``TauSd`` — **matches to ~1e-5 relative**.
* Axial force ``N`` = ``AvgLongitudinalMembrane·(t·s) + beam axial`` — reproduces
  Genie's ``AxialLoads`` (and hence ``Nsd = max(N, 0)``). The longitudinal
  membrane aggregation still carries a few-percent residual under calibration
  (see :func:`calibration_report`); the transverse/shear path is exact.

Remaining calibration: the along-span variation of the transverse stress (the
two *end* positions; the *mid* is exact) needs result-point binning — currently
the 3-position vectors carry the field value at all three positions.

Update: the resolver now emits Section-5 position vectors for axial force and
moment resultants, and midpoint membrane values are area-weighted by element.
The remaining tail is the irregular multi-element cut-plane interpolation.

Sign convention: Genie is compression-positive, so normal membrane stresses and
the plate axial contribution are negated relative to the FE tension-positive
convention.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from ada.fem.capacity import extract
from ada.fem.capacity.model import CapacityModel, CapSection, ResolvedCase
from ada.fem.results.common import Mesh


def _block_elem_index(block) -> dict[int, np.ndarray]:
    """Cache + return ``{element_id: rows}`` for a field block (column 0 = id).

    ``ElementFieldData.get_by_element_id`` scans the whole block, so calling it
    once per element across a model is quadratic. Resolving a multi-hundred-model
    SIN that way dominates the runtime; index each block's rows by element id
    once (cached on the block — blocks are rebuilt per step, so it stays fresh).
    """
    cached = getattr(block, "_cap_elem_index", None)
    if cached is not None:
        return cached
    vals = block.values
    built: dict[int, np.ndarray] = {}
    if vals.size:
        elem_col = vals[:, 0].astype(np.int64)
        order = np.argsort(elem_col, kind="stable")
        sorted_ids = elem_col[order]
        uniq, starts = np.unique(sorted_ids, return_index=True)
        bounds = np.append(starts, len(sorted_ids))
        for k, eid in enumerate(uniq):
            built[int(eid)] = vals[order[bounds[k] : bounds[k + 1]]]
    block._cap_elem_index = built
    return built


def _rows_for_element(blocks, element_id: int) -> list[np.ndarray]:
    out = []
    for b in blocks:
        sub = _block_elem_index(b).get(element_id)
        if sub is not None and sub.size:
            out.append(sub)
    return out


def _element_membrane_tensor(stress_blocks, element_id: int) -> np.ndarray | None:
    """Mean membrane (SIGXX, SIGYY, TAUXY) of an element over its result points,
    in the *element* coordinate frame."""
    rows = [sub[:, 2:5] for sub in _rows_for_element(stress_blocks, element_id)]  # SIGXX, SIGYY, TAUXY
    if not rows:
        return None
    return np.vstack(rows).mean(axis=0)


def _element_stress_rows(stress_blocks, element_id: int) -> dict[int, np.ndarray]:
    """Element stress tensor rows keyed by 1-based result-point id."""
    rows: dict[int, list[np.ndarray]] = {}
    for sub in _rows_for_element(stress_blocks, element_id):
        for row in sub:
            rows.setdefault(int(row[1]), []).append(np.asarray(row[2:5], dtype=float))
    return {point_id: np.vstack(values).mean(axis=0) for point_id, values in rows.items()}


def _rotation_cossin(
    mesh: Mesh,
    element_id: int,
    axis: np.ndarray,
    transform: np.ndarray | None = None,
) -> tuple[float, float]:
    """Cosine/sine of the rotation from the element frame to the stiffener frame.

    This depends only on element geometry (or its Sesam basis) and the stiffener
    axis — all step-invariant — so it is computed once per (stiffener, element)
    and reused across every result case. The actual stress rotation is the cheap
    closed form in :func:`_apply_rotation`.
    """
    if transform is not None and np.shape(transform) != (3, 3):
        transform = None
    if transform is not None:
        basis = np.asarray(transform, dtype=float)
        lx = basis[0]
        normal = basis[2]
        if np.linalg.norm(lx) <= 0.0 or np.linalg.norm(normal) <= 0.0:
            transform = None
    if transform is None:
        coords = extract.element_node_coords(mesh, element_id)
        lx = coords[1] - coords[0]
        normal = np.cross(coords[1] - coords[0], coords[2] - coords[0])
    lx = lx / (np.linalg.norm(lx) or 1.0)
    normal = normal / (np.linalg.norm(normal) or 1.0)
    ax = axis - normal * float(np.dot(axis, normal))  # project stiffener axis into plate plane
    ax = ax / (np.linalg.norm(ax) or 1.0)
    c = float(np.dot(lx, ax))
    s = float(np.dot(np.cross(lx, ax), normal))
    return c, s


def _apply_rotation(cs: tuple[float, float], tensor: np.ndarray) -> np.ndarray:
    """Rotate an element-frame membrane ``(sxx, syy, txy)`` into the stiffener
    frame given the precomputed ``(cos, sin)`` from :func:`_rotation_cossin`.

    Returns ``(sigma_long, sigma_trans, tau)`` where *long* is along the
    stiffener axis and *trans* is perpendicular (in the plate plane).
    """
    c, s = cs
    sxx, syy, txy = tensor
    s_long = sxx * c * c + syy * s * s + 2 * txy * s * c
    s_trans = sxx * s * s + syy * c * c - 2 * txy * s * c
    tau = (syy - sxx) * s * c + txy * (c * c - s * s)
    return np.array([s_long, s_trans, tau])


def _rotate_to_stiffener_frame(
    mesh: Mesh,
    element_id: int,
    tensor: np.ndarray,
    axis: np.ndarray,
    transform: np.ndarray | None = None,
) -> np.ndarray:
    """Backward-compatible one-shot rotate (basis + apply), for callers that do
    not have a cached ``(cos, sin)``."""
    return _apply_rotation(_rotation_cossin(mesh, element_id, axis, transform), tensor)


def _element_membrane_points(
    mesh: Mesh,
    aux: extract.AuxRecords,
    stress_blocks,
    element_id: int,
    axis: np.ndarray,
    cs: tuple[float, float] | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Membrane stress points as ``(xyz, rotated_tensor)``.

    Shell stress output is top/bottom surface data. Pair the two surfaces by
    result-point id order and average them into membrane values at the midpoint
    coordinate before rotating into the stiffener frame.

    ``cs`` is the precomputed ``(cos, sin)`` rotation for this element in the
    stiffener frame (step-invariant); pass it to skip recomputing the element
    basis on every result case. When ``None`` it is computed here.
    """
    rows = _element_stress_rows(stress_blocks, element_id)
    if not rows:
        return []

    if cs is None:
        transform = aux.element_transform_by_element.get(element_id)
        cs = _rotation_cossin(mesh, element_id, axis, transform)

    coords = aux.result_point_coords_by_element.get(element_id, {})
    point_ids = sorted(rows)
    if coords and len(point_ids) % 2 == 0:
        half = len(point_ids) // 2
        paired = []
        for a, b in zip(point_ids[:half], point_ids[half:]):
            if a not in coords or b not in coords:
                continue
            tensor = (rows[a] + rows[b]) / 2.0
            xyz = (coords[a] + coords[b]) / 2.0
            paired.append((xyz, _apply_rotation(cs, tensor)))
        if paired:
            return paired

    tensor = _element_membrane_tensor(stress_blocks, element_id)
    if tensor is None:
        return []
    xyz = extract.element_node_coords(mesh, element_id).mean(axis=0)
    return [(xyz, _apply_rotation(cs, tensor))]


def _element_area(mesh: Mesh, element_id: int) -> float:
    coords = extract.element_node_coords(mesh, element_id)
    if len(coords) < 3:
        return 0.0
    origin = coords[0]
    area = 0.0
    for i in range(1, len(coords) - 1):
        area += float(np.linalg.norm(np.cross(coords[i] - origin, coords[i + 1] - origin))) / 2.0
    return area


def _area_weighted_element_mean(
    mesh: Mesh,
    points_by_element: dict[int, list[tuple[np.ndarray, np.ndarray]]],
    area_by_element: dict[int, float] | None = None,
) -> np.ndarray | None:
    values = []
    weights = []
    for element_id, points in points_by_element.items():
        if not points:
            continue
        values.append(np.array([tensor for _, tensor in points]).mean(axis=0))
        weights.append(_elem_area(mesh, element_id, area_by_element))
    if not values:
        return None
    return np.average(np.array(values), axis=0, weights=np.array(weights))


def _elem_area(mesh: Mesh, element_id: int, area_by_element: dict[int, float] | None) -> float:
    """Element area, from the per-stiffener cache when available (step-invariant)."""
    if area_by_element is not None:
        cached = area_by_element.get(element_id)
        if cached is not None:
            return cached
    return _element_area(mesh, element_id)


def _area_weighted_pressure(
    mesh: Mesh,
    aux: extract.AuxRecords,
    result_case: int,
    element_ids: list[int],
    area_by_element: dict[int, float] | None = None,
) -> float:
    """Mean signed shell pressure over the adjacent plate field."""
    pressures = aux.pressure_by_case_element.get(result_case, {})
    values = []
    weights = []
    for element_id in element_ids:
        if element_id not in pressures:
            continue
        values.append(float(pressures[element_id]))
        weights.append(_elem_area(mesh, element_id, area_by_element))
    if not values:
        return 0.0
    return float(np.average(np.array(values), weights=np.array(weights)))


def _adjacent_plate_field_ids(model: CapacityModel, edge_plate_ids: list[int]) -> list[int]:
    """Expand edge-sharing plate elements to their full adjacent plate fields."""
    if not edge_plate_ids:
        return []
    edge = set(edge_plate_ids)
    out: list[int] = []
    seen: set[int] = set()
    for plate in model.plates:
        if not edge.intersection(plate.element_ids):
            continue
        for element_id in plate.element_ids:
            if element_id not in seen:
                out.append(element_id)
                seen.add(element_id)
    return out or edge_plate_ids


def _beam_origin(mesh: Mesh, element_ids: tuple[int, ...]) -> np.ndarray:
    return extract.element_node_coords(mesh, element_ids[0])[0]


def _edge_points(
    points: list[tuple[np.ndarray, np.ndarray]],
    origin: np.ndarray,
    axis: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Points closest to the stiffener line."""
    if not points:
        return []
    ax = np.asarray(axis, dtype=float)
    ax = ax / (np.linalg.norm(ax) or 1.0)
    distances = []
    for xyz, _ in points:
        offset = xyz - origin
        line_vec = offset - ax * float(np.dot(offset, ax))
        distances.append(float(np.linalg.norm(line_vec)))
    min_dist = min(distances)
    tol = max(1e-6, min_dist + 1e-5)
    return [point for point, distance in zip(points, distances) if distance <= tol]


def _station_values(
    points: list[tuple[np.ndarray, np.ndarray]],
    origin: np.ndarray,
    axis: np.ndarray,
    component: int,
    mid_value: float,
) -> list[float]:
    """Start/mid/end values from result-point coordinates along the stiffener."""
    if not points:
        return [mid_value] * 3
    ax = np.asarray(axis, dtype=float)
    ax = ax / (np.linalg.norm(ax) or 1.0)
    along = np.array([float(np.dot(xyz - origin, ax)) for xyz, _ in points])
    values = np.array([float(v[component]) for _, v in points])
    span = float(np.ptp(along))
    if span <= 1e-12:
        return [float(values.mean()), mid_value, float(values.mean())]
    tol = max(span * 1e-6, 1e-6)
    start = float(values[along <= along.min() + tol].mean())
    end = float(values[along >= along.max() - tol].mean())
    return [start, mid_value, end]


def _beam_component_positions(
    force_blocks,
    element_ids: tuple[int, ...],
    component: int,
    *,
    sign: float = 1.0,
) -> list[float]:
    """Mean beam force component at Section-5 positions 1/2/3."""
    by_pos: dict[int, list[float]] = {1: [], 2: [], 3: []}
    for el in element_ids:
        for sub in _rows_for_element(force_blocks, el):
            for row in sub:
                pos = int(row[1])
                if pos in by_pos:
                    by_pos[pos].append(float(row[2 + component]) * sign)

    values = [float(np.mean(by_pos[pos])) for pos in (1, 2, 3) if by_pos[pos]]
    if not values:
        return [0.0, 0.0, 0.0]
    mean = float(np.mean(values))
    return [float(np.mean(by_pos[pos])) if by_pos[pos] else mean for pos in (1, 2, 3)]


def _beam_axial_positions(force_blocks, element_ids: tuple[int, ...]) -> list[float]:
    """Beam axial force (NXX) at Section-5 positions, compression positive.

    Sign-flipped to Genie's compression-positive convention.
    """
    return _beam_component_positions(force_blocks, element_ids, 0, sign=-1.0)


def _beam_moment_positions(force_blocks, element_ids: tuple[int, ...]) -> list[float]:
    """Beam bending moment contribution at Section-5 positions.

    Sesam's line-force block reports ``MXY`` with the opposite sign to Genie's
    moment convention for these stiffened-plate checks.
    """
    return _beam_component_positions(force_blocks, element_ids, 4, sign=-1.0)


def _section_area_and_centroid(section: CapSection) -> tuple[float, float]:
    """Stiffener area and centroid distance from the plate reference surface."""
    h = float(section.height)
    tw = float(section.web_thickness)
    bf = float(section.flange_width)
    tf = float(section.flange_thickness)
    if h <= 0.0 or tw <= 0.0:
        return 0.0, 0.0

    if bf <= 0.0 or tf <= 0.0:
        area = h * tw
        return area, h / 2.0

    web_h = max(h - tf, 0.0)
    web_area = web_h * tw
    flange_area = bf * tf
    area = web_area + flange_area
    if area <= 0.0:
        return 0.0, 0.0
    centroid = (web_area * (web_h / 2.0) + flange_area * (web_h + tf / 2.0)) / area
    return area, centroid


@dataclass
class _StiffGeom:
    """Step-invariant geometry/topology for one stiffener.

    Everything here depends only on the mesh + capacity-model definition (not on
    any result case), so it is built once per stiffener and reused across every
    case in :func:`resolve_cases`. Only the field *values* vary per case.
    """

    element_ids: tuple[int, ...]
    axis: np.ndarray
    origin: np.ndarray
    edge_trib: list[int]
    field_trib: list[int]
    cs_by_element: dict[int, tuple[float, float]]
    area_by_element: dict[int, float]
    t: float
    s_spacing: float
    section_area: float
    section_centroid: float
    z_na: float
    span: float
    continuous: bool


def _build_stiff_geom(
    mesh: Mesh,
    aux: extract.AuxRecords,
    model: CapacityModel,
    stiff_name: str,
) -> _StiffGeom:
    """Resolve the case-invariant geometry for a stiffener (see :class:`_StiffGeom`)."""
    st = model.stiffener(stiff_name)
    axis, _ = extract.beam_axis_and_span(mesh, st.element_ids)

    candidate_plates = [e for p in model.plates for e in p.element_ids]
    # Tributary = the plates bordering the stiffener edge. ``tributary_plate_ids``
    # matches plates that contain *all* the given beam's nodes, which is the right
    # test for a single FE edge but never holds for a multi-element stiffener
    # chain (no 4-node plate spans every node of a 6-element run). Union the
    # per-element edge plates so a finely-meshed/multi-bay stiffener still gets
    # its bordering plate field — otherwise its plate stresses resolve to zero.
    edge_trib = sorted(
        {pe for be in st.element_ids for pe in extract.tributary_plate_ids(mesh, (be,), candidate_plates)}
    )
    field_trib = _adjacent_plate_field_ids(model, edge_trib)

    origin = _beam_origin(mesh, st.element_ids)
    # Tributary cross-section for the plate axial force (eq (5.1)): use the panel's
    # representative plate field — the SAME plate the capacity check uses
    # (``model.plates[0]`` -> Genie ``s``/``t``). Picking an arbitrary bordering
    # plate via ``next()`` could grab a multi-spacing (e.g. 1300 mm) or — before
    # the thickness-gated merge — a different-thickness plate, so ``s_spacing·t``
    # diverged from what the check applied (``AxialLoadsPlate`` then disagreed with
    # ``sigma_x·t·s`` by the same factor). Panels are single-thickness after the
    # merge, so ``plates[0]`` carries the correct thickness and stiffener spacing.
    plate = model.plates[0] if model.plates else None
    t = plate.thickness if plate else 0.0
    s_spacing = plate.width if plate else 0.0
    section_area, section_centroid = _section_area_and_centroid(st.section)
    plate_area = t * s_spacing
    z_na = section_area * section_centroid / (section_area + plate_area) if section_area + plate_area else 0.0

    # Per-element rotation (cos/sin) into the stiffener frame and area, for every
    # tributary plate element — the dominant per-case cost when recomputed.
    cs_by_element: dict[int, tuple[float, float]] = {}
    area_by_element: dict[int, float] = {}
    for pe in set(field_trib) | set(edge_trib):
        transform = aux.element_transform_by_element.get(pe)
        cs_by_element[pe] = _rotation_cossin(mesh, pe, axis, transform)
        area_by_element[pe] = _element_area(mesh, pe)

    return _StiffGeom(
        element_ids=st.element_ids,
        axis=axis,
        origin=origin,
        edge_trib=edge_trib,
        field_trib=field_trib,
        cs_by_element=cs_by_element,
        area_by_element=area_by_element,
        t=t,
        s_spacing=s_spacing,
        section_area=section_area,
        section_centroid=section_centroid,
        z_na=z_na,
        span=float(st.span or 0.0),
        continuous=st.continuous,
    )


def _resolve_stiffener(
    mesh: Mesh,
    aux: extract.AuxRecords,
    model: CapacityModel,
    stiff_name: str,
    result_case: int,
    stress_blocks,
    force_blocks,
    geom: _StiffGeom,
) -> ResolvedCase:
    axis = geom.axis
    edge_trib = geom.edge_trib
    field_trib = geom.field_trib
    cs = geom.cs_by_element
    areas = geom.area_by_element

    # Field transverse/shear membrane in the stiffener frame: each adjacent
    # plate field is averaged over its membrane result points, then
    # area-weighted and negated to Genie's compression-positive convention. If
    # an adjacent plate field is split into several shells, use the full field
    # rather than only the shell sharing the stiffener nodes; this matches
    # Genie's Section-5 field integration on irregular triangular/quadrilateral
    # edge fields. ``edge_trib ⊆ field_trib``, so resolve each element's points
    # once and slice both views from it.
    points_by_element = {
        pe: _element_membrane_points(mesh, aux, stress_blocks, pe, axis, cs.get(pe)) for pe in field_trib
    }
    field_points_by_element = points_by_element
    field_points = [point for points in field_points_by_element.values() for point in points]
    field_weighted = _area_weighted_element_mean(mesh, field_points_by_element, areas)
    overall = -field_weighted if field_weighted is not None else np.zeros(3)

    origin = geom.origin
    # Longitudinal axial/moment resultants are edge quantities: sample the plate
    # elements that share the stiffener line, then use result points closest to
    # that line for the calibrated Section-5 axial reconstruction.
    edge_points_by_element = {pe: points_by_element.get(pe, []) for pe in edge_trib}
    long_points = [point for points in edge_points_by_element.values() for point in points]
    long_weighted = _area_weighted_element_mean(mesh, edge_points_by_element, areas)
    long_overall = -long_weighted if long_weighted is not None else overall
    edge_points = _edge_points(long_points, origin, axis)
    edge_rotated = [tensor for _, tensor in edge_points]
    edge_mid = -float(np.array(edge_rotated).mean(axis=0)[0]) if edge_rotated else float(long_overall[0])
    all_long_pos = [-x for x in _station_values(long_points, origin, axis, 0, -float(long_overall[0]))]
    edge_long_pos = [-x for x in _station_values(edge_points, origin, axis, 0, -edge_mid)]
    long_pos = [(a + e) / 2.0 for a, e in zip(all_long_pos, edge_long_pos)]
    long_mid = float(long_pos[1])
    trans_pos = [-x for x in _station_values(field_points, origin, axis, 1, -float(overall[1]))]
    tau_pos = [abs(x) for x in _station_values(field_points, origin, axis, 2, -float(overall[2]))]
    v_mid = overall

    # Plate tributary area = thickness x spacing of the bordering plate(s).
    t = geom.t
    s_spacing = geom.s_spacing
    p_sd = _area_weighted_pressure(mesh, aux, result_case, field_trib, areas)
    q_dir = p_sd * s_spacing
    n_plate_positions = [float(x * t * s_spacing) for x in long_pos]
    n_beam_positions = _beam_axial_positions(force_blocks, geom.element_ids)
    n_axial_positions = [float(n_plate + n_beam) for n_plate, n_beam in zip(n_plate_positions, n_beam_positions)]
    n_axial = n_axial_positions[1]
    n_plate = long_mid * t * s_spacing
    n_beam = n_beam_positions[1]
    n_sd = (
        0.2 * max(n_axial_positions[0], 0.0)
        + 0.6 * max(n_axial_positions[1], 0.0)
        + 0.2 * max(n_axial_positions[2], 0.0)
    )

    # section_area, section_centroid = geom.section_area, geom.section_centroid
    section_centroid = geom.section_centroid
    z_na = geom.z_na
    m_plate = [-float(n * z_na) for n in n_plate_positions]
    m_beam_force = [float(n * (section_centroid - z_na)) for n in n_beam_positions]
    m_beam = _beam_moment_positions(force_blocks, geom.element_ids)
    moments = [float(a + b + c) for a, b, c in zip(m_plate, m_beam_force, m_beam)]
    span = geom.span
    q_fe = (0.5 * (moments[0] + moments[2]) - moments[1]) * 8.0 / (span * span) if span else 0.0

    variables = {
        "SigmaYSd": float(v_mid[1]),
        "SigmaY1Sd": float(max(trans_pos)),
        "SigmaY2Sd": float(min(trans_pos)),
        "TauSd": float(tau_pos[1]),
        "PSd": float(p_sd),
        "Qdir": float(q_dir),
        "QFE": float(q_fe),
        "Nsd": float(n_sd),
        "Naxial": float(n_axial),
        "AxialLoadsBeam": float(n_beam),
        "AxialLoadsPlate": float(n_plate),
    }
    vectors = {
        "AverageLongitudinalMembraneStresses": [float(x) for x in long_pos],
        "AverageTransverseMembraneStresses": [float(x) for x in trans_pos],
        "AverageShearStresses": [float(x) for x in tau_pos],
        "AxialLoads": n_axial_positions,
        "AxialLoadsPlate": n_plate_positions,
        "AxialLoadsBeam": n_beam_positions,
        "MomentsAboutNeutralAxis": moments,
        "MomentsAboutNeutralAxisPlate": m_plate,
        "MomentsAboutNeutralAxisBeamForce": m_beam_force,
        "MomentsAboutNeutralAxisBeamMoment": m_beam,
    }
    return ResolvedCase(
        result_case=0,
        stiffener=stiff_name,
        panel_group=model.name,
        capacity_model_id=model.id or model.name,
        continuous=geom.continuous,
        variables=variables,
        vectors=vectors,
    )


def _recovered_stress_blocks(mesh, aux, res, case, material_by_element, *, log: bool):
    """Synthesize STRESS blocks from nodal displacements for a stress-less SIN.

    Returns an empty list when there are no displacements / no plate materials to
    recover from (the caller then resolves to zero, as before).
    """
    from ada.config import logger
    from ada.fem.capacity.stress_recovery import (
        build_recovered_stress,
        displacements_by_node,
    )

    disp_blocks = [r for r in res.results if r.name == "RVNODDIS"]
    if not disp_blocks or not material_by_element:
        return []
    if log:
        logger.info(
            "SIN has no element stresses (SESTRA ISEL4=-1); recovering membrane "
            "stresses for %d plate elements from nodal displacements",
            len(material_by_element),
        )
    disp = displacements_by_node(disp_blocks[0])
    block, coords_by_element = build_recovered_stress(
        mesh, disp, material_by_element, case, aux.element_transform_by_element
    )
    # The resolver reads result-point coords from aux; the recovered corner
    # points are geometry (case-invariant), so merging once is enough.
    for element_id, cmap in coords_by_element.items():
        aux.result_point_coords_by_element.setdefault(element_id, cmap)
    return [block] if block.values.size else []


# FE field blocks that participate in load-combination superposition: the plate
# membrane stresses, the beam/line force resultants, and (for stress-less SINs)
# the nodal displacements the recovery fallback reads. Resolution is linear in
# these fields up to the per-station min/max, so a combination must superpose the
# *fields* (Σ fᵢ·blockᵢ) and resolve once — not linearly combine resolved scalars.
_SUPERPOSE_BLOCKS = ("STRESS", "FORCES", "RVNODDIS")


def _superpose_into(accum: dict, results, factor: float) -> None:
    """Add ``factor`` × each superposable block in ``results`` into ``accum``.

    ``accum`` maps ``(name, index) -> [template_block, values_ndarray, n_id_cols]``.
    The basic steps of one SIN share a single mesh / RDPOINTS layout, so each
    block's rows align 1:1 across steps by their leading id columns (``elem,
    point`` for element fields; ``node`` for nodal) — the fast path just adds the
    scaled value columns. A key-based merge covers any row-order drift.
    """
    by_name: dict[str, list] = {}
    for blk in results:
        if blk.name in _SUPERPOSE_BLOCKS:
            by_name.setdefault(blk.name, []).append(blk)
    for name, blocks in by_name.items():
        for idx, blk in enumerate(blocks):
            key = (name, idx)
            n_id = len(blk.COLS)
            slot = accum.get(key)
            if slot is None:
                arr = np.array(blk.values, dtype=float)
                if arr.size:
                    arr[:, n_id:] *= factor
                accum[key] = [blk, arr, n_id]
                continue
            _, arr, n_id = slot
            src = np.asarray(blk.values, dtype=float)
            if src.shape == arr.shape and np.array_equal(src[:, :n_id], arr[:, :n_id]):
                if src.size:
                    arr[:, n_id:] += factor * src[:, n_id:]
            else:
                _key_superpose(arr, src, n_id, factor)


def _key_superpose(dst: np.ndarray, src: np.ndarray, n_id: int, factor: float) -> None:
    """Row-order-independent fallback for :func:`_superpose_into`.

    Adds ``factor`` × ``src`` value columns onto ``dst`` rows matched by their
    integer id columns. ``dst`` is seeded from the first contributing step, so any
    ``src`` key absent from it (impossible for a shared mesh) is simply dropped.
    """
    if not dst.size or not src.size:
        return
    index = {tuple(int(round(v)) for v in row[:n_id]): i for i, row in enumerate(dst)}
    for row in src:
        i = index.get(tuple(int(round(v)) for v in row[:n_id]))
        if i is not None:
            dst[i, n_id:] += factor * row[n_id:]


def _accum_blocks(accum: dict) -> list:
    """Materialise superposed blocks as fresh field-data objects (id columns from
    the template, value columns from the accumulator)."""
    out = []
    for (_name, _idx), (template, arr, _n_id) in sorted(accum.items(), key=lambda kv: kv[0]):
        out.append(dataclasses.replace(template, values=arr))
    return out


def _resolve_step(
    mesh: Mesh,
    aux: extract.AuxRecords,
    models: list[CapacityModel],
    case: int,
    results,
    material_by_element: dict[int, tuple[float, float]],
    geom_cache: dict[tuple, _StiffGeom],
    *,
    log: bool,
) -> list[ResolvedCase]:
    """Resolve every (model, stiffener) for one case from its field blocks.

    ``results`` is a step's ``FEAResult.results`` list (a basic case) or the
    superposed block list of a combination — both expose ``STRESS`` / ``FORCES``
    (and ``RVNODDIS`` for the stress-less recovery fallback) by ``name``.

    ``geom_cache`` holds the step-invariant per-stiffener geometry, built on the
    first case and reused across every subsequent one.
    """
    stress_blocks = [r for r in results if r.name == "STRESS"]
    force_blocks = [r for r in results if r.name == "FORCES"]
    if not stress_blocks:
        shim = SimpleNamespace(results=list(results))
        stress_blocks = _recovered_stress_blocks(mesh, aux, shim, case, material_by_element, log=log)
    out: list[ResolvedCase] = []
    for model in models:
        for st in model.stiffeners:
            key = (model.id or model.name, st.name)
            geom = geom_cache.get(key)
            if geom is None:
                geom = _build_stiff_geom(mesh, aux, model, st.name)
                geom_cache[key] = geom
            rc = _resolve_stiffener(mesh, aux, model, st.name, case, stress_blocks, force_blocks, geom)
            out.append(
                ResolvedCase(
                    result_case=case,
                    stiffener=rc.stiffener,
                    panel_group=rc.panel_group,
                    capacity_model_id=rc.capacity_model_id,
                    continuous=rc.continuous,
                    variables=rc.variables,
                    vectors=rc.vectors,
                )
            )
    return out


def resolve_cases(
    sin_path: str | pathlib.Path,
    models: list[CapacityModel],
    result_cases: list[int] | None = None,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ResolvedCase]:
    """Resolve design variables for every (result case, stiffener).

    ``result_cases`` may list basic result cases (stored RV* steps) and/or
    load-case *combinations* (defined in the SIN's RDRESCMB records but, for
    SESTRA "smart" combinations, not themselves stored). A combination is
    resolved by superposing its basic cases' FE fields (Σ fᵢ·blockᵢ) before the
    Section-6 resolve, which is exact because resolution is linear in the field.

    ``on_progress(completed, total)`` is called once per resolved case so callers
    can drive a progress bar.
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

    # Split requests into directly-stored basic cases and combinations to
    # superpose. Basic cases are resolved as they stream past (memory-bounded);
    # only the few combination accumulators are held across the read.
    direct: list[int] = []
    combo_plan: dict[int, dict[int, float]] = {}
    for c in result_cases:
        ci = int(c)
        if ci in combinations:
            comps = combinations[ci]
            if not comps:
                raise ValueError(f"result combination {ci} ({meta.result_names.get(ci, '?')!r}) lists no basic cases")
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
    # Plate element -> (E, poisson), used to recover membrane stresses from nodal
    # displacements when the SIN carries no element stresses (SESTRA ISEL4=-1).
    material_by_element: dict[int, tuple[float, float]] = {
        e: (p.material.E, p.material.poisson) for model in models for p in model.plates for e in p.element_ids
    }

    out: list[ResolvedCase] = []
    total = len(direct) + len(combo_plan)
    done = 0
    accums: dict[int, dict] = {case: {} for case in combo_plan}
    # Per-stiffener geometry is step-invariant; build it on the first case and
    # reuse across all of them (the rotation bases / tributary topology dominate
    # the per-case resolve when recomputed every time).
    geom_cache: dict[tuple, _StiffGeom] = {}
    # Line forces are only ever read for stiffener beam elements, so narrow the
    # per-step RVFORCES decode to them — skips unpacking the whole model's
    # forces on every step (the dominant SIN-read cost on a large model).
    forces_elements = {int(e) for model in models for st in model.stiffeners for e in st.element_ids}
    # Read the SIN once and reuse the (step-invariant) mesh across cases — on a
    # multi-hundred-MB SIN re-opening + rebuilding the mesh per case dominates.
    mesh = None
    for step, res in iter_sin_step_results(sin_path, needed_steps, forces_elements=forces_elements):
        if mesh is None:
            mesh = res.mesh
        # Accumulate this step into every combination that references it (before
        # the direct resolve below caches per-block element indices on res).
        for case, comps in combo_plan.items():
            factor = comps.get(step)
            if factor:
                _superpose_into(accums[case], res.results, float(factor))
        if step in direct_set:
            out.extend(
                _resolve_step(mesh, aux, models, step, res.results, material_by_element, geom_cache, log=done == 0)
            )
            done += 1
            if on_progress is not None:
                on_progress(done, total)

    for case, comps in combo_plan.items():
        combined = _accum_blocks(accums[case])
        out.extend(_resolve_step(mesh, aux, models, case, combined, material_by_element, geom_cache, log=done == 0))
        done += 1
        if on_progress is not None:
            on_progress(done, total)
    return out


# --------------------------------------------------------------------------- #
# Calibration against Genie CriteriaResults
# --------------------------------------------------------------------------- #
@dataclass
class VarResidual:
    case: int
    stiffener: str
    variable: str
    ours: float
    genie: float

    @property
    def abs_delta(self) -> float:
        return abs(self.ours - self.genie)

    @property
    def rel_delta(self) -> float:
        return self.abs_delta / max(abs(self.genie), 1e-9)


def calibration_report(
    resolved: list[ResolvedCase],
    genie_cases: list,
    variables: tuple[str, ...] = ("SigmaYSd", "TauSd"),
) -> list[VarResidual]:
    """Compare our resolved scalars against Genie's, per (case, stiffener).

    ``genie_cases`` items expose ``result_case``, ``stiffener`` and a
    ``variables`` mapping (the shape ``aibel_dnv_rp_c201.reference.genie`` emits).
    """
    index = {(c.result_case, c.stiffener): c for c in resolved}
    residuals: list[VarResidual] = []
    for g in genie_cases:
        ours = index.get((int(g.result_case), g.stiffener))
        if ours is None:
            continue
        for v in variables:
            residuals.append(
                VarResidual(
                    int(g.result_case), g.stiffener, v, ours.variables.get(v, 0.0), float(g.variables.get(v, 0.0))
                )
            )
    return residuals

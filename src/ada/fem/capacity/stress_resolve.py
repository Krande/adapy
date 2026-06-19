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

import pathlib
from dataclasses import dataclass

import numpy as np

from ada.fem.capacity import extract
from ada.fem.capacity.model import CapacityModel, CapSection, ResolvedCase
from ada.fem.results.common import Mesh


def _element_membrane_tensor(stress_blocks, element_id: int) -> np.ndarray | None:
    """Mean membrane (SIGXX, SIGYY, TAUXY) of an element over its result points,
    in the *element* coordinate frame."""
    rows = []
    for b in stress_blocks:
        sub = b.get_by_element_id([element_id])
        if sub.values.size:
            rows.append(sub.values[:, 2:5])  # SIGXX, SIGYY, TAUXY
    if not rows:
        return None
    return np.vstack(rows).mean(axis=0)


def _element_stress_rows(stress_blocks, element_id: int) -> dict[int, np.ndarray]:
    """Element stress tensor rows keyed by 1-based result-point id."""
    rows: dict[int, list[np.ndarray]] = {}
    for b in stress_blocks:
        sub = b.get_by_element_id([element_id])
        if not sub.values.size:
            continue
        for row in sub.values:
            rows.setdefault(int(row[1]), []).append(np.asarray(row[2:5], dtype=float))
    return {point_id: np.vstack(values).mean(axis=0) for point_id, values in rows.items()}


def _rotate_to_stiffener_frame(
    mesh: Mesh,
    element_id: int,
    tensor: np.ndarray,
    axis: np.ndarray,
    transform: np.ndarray | None = None,
) -> np.ndarray:
    """Rotate an element-frame membrane tensor into the stiffener frame.

    Returns ``(sigma_long, sigma_trans, tau)`` where *long* is along the
    stiffener axis and *trans* is perpendicular (in the plate plane).
    """
    sxx, syy, txy = tensor
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
    s_long = sxx * c * c + syy * s * s + 2 * txy * s * c
    s_trans = sxx * s * s + syy * c * c - 2 * txy * s * c
    tau = (syy - sxx) * s * c + txy * (c * c - s * s)
    return np.array([s_long, s_trans, tau])


def _element_membrane_points(
    mesh: Mesh,
    aux: extract.AuxRecords,
    stress_blocks,
    element_id: int,
    axis: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Membrane stress points as ``(xyz, rotated_tensor)``.

    Shell stress output is top/bottom surface data. Pair the two surfaces by
    result-point id order and average them into membrane values at the midpoint
    coordinate before rotating into the stiffener frame.
    """
    rows = _element_stress_rows(stress_blocks, element_id)
    if not rows:
        return []

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
            transform = aux.element_transform_by_element.get(element_id)
            paired.append((xyz, _rotate_to_stiffener_frame(mesh, element_id, tensor, axis, transform)))
        if paired:
            return paired

    tensor = _element_membrane_tensor(stress_blocks, element_id)
    if tensor is None:
        return []
    xyz = extract.element_node_coords(mesh, element_id).mean(axis=0)
    transform = aux.element_transform_by_element.get(element_id)
    return [(xyz, _rotate_to_stiffener_frame(mesh, element_id, tensor, axis, transform))]


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
) -> np.ndarray | None:
    values = []
    weights = []
    for element_id, points in points_by_element.items():
        if not points:
            continue
        values.append(np.array([tensor for _, tensor in points]).mean(axis=0))
        weights.append(_element_area(mesh, element_id))
    if not values:
        return None
    return np.average(np.array(values), axis=0, weights=np.array(weights))


def _area_weighted_pressure(
    mesh: Mesh,
    aux: extract.AuxRecords,
    result_case: int,
    element_ids: list[int],
) -> float:
    """Mean signed shell pressure over the adjacent plate field."""
    pressures = aux.pressure_by_case_element.get(result_case, {})
    values = []
    weights = []
    for element_id in element_ids:
        if element_id not in pressures:
            continue
        values.append(float(pressures[element_id]))
        weights.append(_element_area(mesh, element_id))
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
        for b in force_blocks:
            sub = b.get_by_element_id([el])
            if not sub.values.size:
                continue
            for row in sub.values:
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


def _resolve_stiffener(
    mesh: Mesh,
    aux: extract.AuxRecords,
    model: CapacityModel,
    stiff_name: str,
    result_case: int,
    stress_blocks,
    force_blocks,
) -> ResolvedCase:
    st = model.stiffener(stiff_name)
    axis, _ = extract.beam_axis_and_span(mesh, st.element_ids)

    candidate_plates = [e for p in model.plates for e in p.element_ids]
    edge_trib = extract.tributary_plate_ids(mesh, st.element_ids, candidate_plates)
    field_trib = _adjacent_plate_field_ids(model, edge_trib)

    # Field transverse/shear membrane in the stiffener frame: each adjacent
    # plate field is averaged over its membrane result points, then
    # area-weighted and negated to Genie's compression-positive convention. If
    # an adjacent plate field is split into several shells, use the full field
    # rather than only the shell sharing the stiffener nodes; this matches
    # Genie's Section-5 field integration on irregular triangular/quadrilateral
    # edge fields.
    field_points_by_element = {pe: _element_membrane_points(mesh, aux, stress_blocks, pe, axis) for pe in field_trib}
    field_points = [point for points in field_points_by_element.values() for point in points]
    field_weighted = _area_weighted_element_mean(mesh, field_points_by_element)
    overall = -field_weighted if field_weighted is not None else np.zeros(3)

    origin = _beam_origin(mesh, st.element_ids)
    # Longitudinal axial/moment resultants are edge quantities: sample the plate
    # elements that share the stiffener line, then use result points closest to
    # that line for the calibrated Section-5 axial reconstruction.
    edge_points_by_element = {pe: _element_membrane_points(mesh, aux, stress_blocks, pe, axis) for pe in edge_trib}
    long_points = [point for points in edge_points_by_element.values() for point in points]
    long_weighted = _area_weighted_element_mean(mesh, edge_points_by_element)
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
    plate = next((p for p in model.plates if any(e in edge_trib for e in p.element_ids)), None)
    t = plate.thickness if plate else 0.0
    s_spacing = plate.width if plate else 0.0
    p_sd = _area_weighted_pressure(mesh, aux, result_case, field_trib)
    q_dir = p_sd * s_spacing
    n_plate_positions = [float(x * t * s_spacing) for x in long_pos]
    n_beam_positions = _beam_axial_positions(force_blocks, st.element_ids)
    n_axial_positions = [float(n_plate + n_beam) for n_plate, n_beam in zip(n_plate_positions, n_beam_positions)]
    n_axial = n_axial_positions[1]
    n_plate = long_mid * t * s_spacing
    n_beam = n_beam_positions[1]
    n_sd = (
        0.2 * max(n_axial_positions[0], 0.0)
        + 0.6 * max(n_axial_positions[1], 0.0)
        + 0.2 * max(n_axial_positions[2], 0.0)
    )

    section_area, section_centroid = _section_area_and_centroid(st.section)
    plate_area = t * s_spacing
    z_na = section_area * section_centroid / (section_area + plate_area) if section_area + plate_area else 0.0
    m_plate = [-float(n * z_na) for n in n_plate_positions]
    m_beam_force = [float(n * (section_centroid - z_na)) for n in n_beam_positions]
    m_beam = _beam_moment_positions(force_blocks, st.element_ids)
    moments = [float(a + b + c) for a, b, c in zip(m_plate, m_beam_force, m_beam)]
    span = float(st.span or 0.0)
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
        continuous=st.continuous,
        variables=variables,
        vectors=vectors,
    )


def resolve_cases(
    sin_path: str | pathlib.Path,
    models: list[CapacityModel],
    result_cases: list[int] | None = None,
) -> list[ResolvedCase]:
    """Resolve design variables for every (result case, stiffener)."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_file, read_sin_metadata

    if result_cases is None:
        result_cases = read_sin_metadata(sin_path).steps

    aux = extract.AuxRecords.from_sin(sin_path)
    mesh = read_sin_file(sin_path, step=result_cases[0]).mesh if result_cases else None
    out: list[ResolvedCase] = []
    for case in result_cases:
        res = read_sin_file(sin_path, step=case)
        stress_blocks = [r for r in res.results if r.name == "STRESS"]
        force_blocks = [r for r in res.results if r.name == "FORCES"]
        for model in models:
            for st in model.stiffeners:
                rc = _resolve_stiffener(mesh, aux, model, st.name, case, stress_blocks, force_blocks)
                out.append(
                    ResolvedCase(
                        result_case=case,
                        stiffener=rc.stiffener,
                        panel_group=rc.panel_group,
                        continuous=rc.continuous,
                        variables=rc.variables,
                        vectors=rc.vectors,
                    )
                )
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

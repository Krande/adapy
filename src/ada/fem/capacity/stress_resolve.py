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

Sign convention: Genie is compression-positive, so normal membrane stresses and
the plate axial contribution are negated relative to the FE tension-positive
convention.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import numpy as np

from ada.fem.capacity import extract
from ada.fem.capacity.model import CapacityModel, ResolvedCase
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


def _rotate_to_stiffener_frame(mesh: Mesh, element_id: int, tensor: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Rotate an element-frame membrane tensor into the stiffener frame.

    Returns ``(sigma_long, sigma_trans, tau)`` where *long* is along the
    stiffener axis and *trans* is perpendicular (in the plate plane).
    """
    sxx, syy, txy = tensor
    coords = extract.element_node_coords(mesh, element_id)
    lx = coords[1] - coords[0]
    lx = lx / (np.linalg.norm(lx) or 1.0)
    normal = np.cross(coords[1] - coords[0], coords[2] - coords[0])
    normal = normal / (np.linalg.norm(normal) or 1.0)
    ax = axis - normal * float(np.dot(axis, normal))  # project stiffener axis into plate plane
    ax = ax / (np.linalg.norm(ax) or 1.0)
    c = float(np.dot(lx, ax))
    s = float(np.dot(np.cross(lx, ax), normal))
    s_long = sxx * c * c + syy * s * s + 2 * txy * s * c
    s_trans = sxx * s * s + syy * c * c - 2 * txy * s * c
    tau = (syy - sxx) * s * c + txy * (c * c - s * s)
    return np.array([s_long, s_trans, tau])


def _beam_axial_force(force_blocks, element_ids: tuple[int, ...]) -> float:
    """Mean beam axial force (NXX) over a stiffener's beam element(s).

    Sign-flipped to Genie's compression-positive convention.
    """
    vals = []
    for el in element_ids:
        for b in force_blocks:
            sub = b.get_by_element_id([el])
            if sub.values.size:
                vals.append(sub.values[:, 2])  # NXX
    if not vals:
        return 0.0
    return -float(np.concatenate(vals).mean())


def _resolve_stiffener(
    mesh: Mesh,
    model: CapacityModel,
    stiff_name: str,
    stress_blocks,
    force_blocks,
) -> ResolvedCase:
    st = model.stiffener(stiff_name)
    axis, _ = extract.beam_axis_and_span(mesh, st.element_ids)

    candidate_plates = [e for p in model.plates for e in p.element_ids]
    trib = extract.tributary_plate_ids(mesh, st.element_ids, candidate_plates)

    # Field membrane in the stiffener frame: mean of each tributary plate's
    # element-average membrane, rotated and negated (compression positive). This
    # reproduces Genie's transverse-mid and shear to float precision; the along-
    # span transverse profile (start/end) and the longitudinal edge value carry a
    # residual still under calibration — see module docstring.
    rotated = [
        _rotate_to_stiffener_frame(mesh, pe, t, axis)
        for pe in trib
        if (t := _element_membrane_tensor(stress_blocks, pe)) is not None
    ]
    overall = -np.array(rotated).mean(axis=0) if rotated else np.zeros(3)

    long_pos = [float(overall[0])] * 3
    trans_pos = [float(overall[1])] * 3
    tau_pos = [abs(float(overall[2]))] * 3
    v_mid = overall

    # Plate tributary area = thickness x spacing of the bordering plate(s).
    plate = next((p for p in model.plates if any(e in trib for e in p.element_ids)), None)
    t = plate.thickness if plate else 0.0
    s_spacing = plate.width if plate else 0.0
    n_plate = float(v_mid[0]) * t * s_spacing
    n_beam = _beam_axial_force(force_blocks, st.element_ids)
    n_axial = n_plate + n_beam
    n_sd = max(n_axial, 0.0)  # buckling axial counts compression only (Genie Nsd)

    variables = {
        "SigmaYSd": float(v_mid[1]),
        "SigmaY1Sd": float(max(trans_pos)),
        "SigmaY2Sd": float(min(trans_pos)),
        "TauSd": float(tau_pos[1]),
        "Qdir": 0.0,
        "Nsd": float(n_sd),
        "Naxial": float(n_axial),
        "AxialLoadsBeam": float(n_beam),
        "AxialLoadsPlate": float(n_plate),
    }
    vectors = {
        "AverageLongitudinalMembraneStresses": [float(x) for x in long_pos],
        "AverageTransverseMembraneStresses": [float(x) for x in trans_pos],
        "AverageShearStresses": [float(x) for x in tau_pos],
        "AxialLoads": [float(n_axial)] * 3,
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

    mesh = read_sin_file(sin_path, step=result_cases[0]).mesh if result_cases else None
    out: list[ResolvedCase] = []
    for case in result_cases:
        res = read_sin_file(sin_path, step=case)
        stress_blocks = [r for r in res.results if r.name == "STRESS"]
        force_blocks = [r for r in res.results if r.name == "FORCES"]
        for model in models:
            for st in model.stiffeners:
                rc = _resolve_stiffener(mesh, model, st.name, stress_blocks, force_blocks)
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
                VarResidual(int(g.result_case), g.stiffener, v, ours.variables.get(v, 0.0), float(g.variables.get(v, 0.0)))
            )
    return residuals

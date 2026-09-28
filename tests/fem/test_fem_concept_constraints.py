"""Beam end supports from concepts, verified by Code_Aster eigen analyses against Euler-Bernoulli.

A shell/solid beam end is supported through a reference node coupled to its section, so a pinned end is free to
rotate. Restraining the section nodes directly would clamp it: a simply supported beam would then show the
clamped-clamped weak-axis frequency, (4.730 / pi)^2 ~ 2.27 times the pinned one.
"""

from __future__ import annotations

import math
import pathlib
from typing import Literal

import pytest

import ada
from ada.api.fem_tasks import BEAM_NAME, PART_NAME, design_cantilever
from ada.base.types import GeomRepr
from ada.fem.meshing import GmshOptions

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/concept_constraints"
EIGEN_MODES = 4

Case = Literal["cantilever", "simply_supported"]
Mesher = Literal["part", "beam"]

#: Relative tolerance on the weak-axis bending frequency. Line elements are Euler-Bernoulli. The shell model
#: overlaps flanges and web at the junctions, and the solids are second order to avoid tetrahedral locking.
REL_TOL = {GeomRepr.LINE: 0.002, GeomRepr.SHELL: 0.06, GeomRepr.SOLID: 0.03}


def _dofs(fixed: set[str]) -> list[ada.ConstraintConceptDofType]:
    return [
        ada.ConstraintConceptDofType(d, "fixed" if d in fixed else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")
    ]


def _solve(geom_repr: GeomRepr, case: Case, mesher: Mesher) -> tuple[list[float], float]:
    a = design_cantilever()
    p = a.get_part(PART_NAME)
    bm = next(b for b in p.get_all_physical_objects() if b.name == BEAM_NAME)

    if case == "cantilever":
        bm.concept_fem.fix_end("n1")
    else:
        # Pinned at both ends, with torsion held (else it is a rigid body mode) and n2 free to slide axially
        bm.concept_fem.add_end_constraint("n1", _dofs({"dx", "dy", "dz", "rx"}))
        bm.concept_fem.add_end_constraint("n2", _dofs({"dy", "dz", "rx"}))

    elem_order = 2 if geom_repr == GeomRepr.SOLID else 1
    mesh_obj = p if mesher == "part" else bm
    p.fem = mesh_obj.to_fem_obj(0.07, geom_repr, options=GmshOptions(Mesh_ElementOrder=elem_order))

    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=EIGEN_MODES))
    name = f"concept_{case}_{mesher}_{geom_repr.value}"
    res = a.to_fem(name, "code_aster", overwrite=True, execute=True, scratch_dir=SCRATCH_DIR, exit_on_complete=False)
    freqs = [m.f_hz for m in res.get_eig_summary().modes]

    props = bm.section.properties
    ei_per_m = bm.material.model.E * min(props.Iy, props.Iz) / (bm.material.model.rho * props.Ax)
    length = bm.length
    if case == "cantilever":
        f_ref = (1.8751**2 / (2 * math.pi)) * math.sqrt(ei_per_m / length**4)
    else:
        f_ref = (math.pi / (2 * length**2)) * math.sqrt(ei_per_m)

    return freqs, f_ref


@pytest.mark.parametrize("mesher", ["part", "beam"])
@pytest.mark.parametrize("case", ["cantilever", "simply_supported"])
@pytest.mark.parametrize("geom_repr", [GeomRepr.LINE, GeomRepr.SHELL, GeomRepr.SOLID])
def test_beam_end_supports_give_the_euler_bernoulli_frequency(geom_repr, case, mesher):
    freqs, f_ref = _solve(geom_repr, case, mesher)

    # The weak-axis bending mode is there ...
    closest = min(freqs, key=lambda f: abs(f - f_ref))
    assert closest == pytest.approx(f_ref, rel=REL_TOL[geom_repr]), f"{freqs} vs {f_ref:.3f} Hz"
    # ... and nothing is stiffer than it should be: a clamped "pin" would lift the lowest mode far above it
    assert freqs[0] <= f_ref * (1 + REL_TOL[geom_repr]), f"{freqs} vs {f_ref:.3f} Hz"

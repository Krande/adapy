from __future__ import annotations

import pathlib
from typing import Literal

import pytest

import ada
from ada.api.fem_tasks import BEAM_NAME, PART_NAME, design_cantilever
from ada.base.types import GeomRepr
from ada.fem.meshing import GmshOptions

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/concept_constraints"
EIGEN_MODES = 6

Mesher = Literal["part", "beam"]
BcSource = Literal["explicit", "part_point", "beam_end"]


def _solve_cantilever_eig(geom_repr: GeomRepr, mesher: Mesher, bc_source: BcSource) -> list[float]:
    a = design_cantilever()
    p = a.get_part(PART_NAME)
    bm = next(b for b in p.get_all_physical_objects() if b.name == BEAM_NAME)

    if bc_source == "part_point":
        encastre = ada.ConstraintConceptDofType.encastre()
        p.concept_fem.constraints.add_point_constraint(ada.ConstraintConceptPoint("Fixed", bm.n1.p, encastre))
    elif bc_source == "beam_end":
        bm.concept_fem.fix_end("n1")

    mesh_obj = p if mesher == "part" else bm
    p.fem = mesh_obj.to_fem_obj(0.07, geom_repr, options=GmshOptions(Mesh_ElementOrder=1))

    if bc_source == "explicit":
        fix_set = p.fem.add_set(ada.fem.FemSet("bc_nodes", bm.bbox().sides.back(return_fem_nodes=True, fem=p.fem)))
        a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    else:
        assert len(p.fem.bcs) == 1

    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=EIGEN_MODES))
    name = f"concept_bc_{mesher}_{bc_source}_{geom_repr.value}"
    res = a.to_fem(name, "code_aster", overwrite=True, execute=True, scratch_dir=SCRATCH_DIR, exit_on_complete=False)

    return [m.f_hz for m in res.get_eig_summary().modes]


@pytest.mark.parametrize(
    "mesher, bc_source",
    [("part", "part_point"), ("part", "beam_end"), ("beam", "beam_end")],
)
@pytest.mark.parametrize("geom_repr", [GeomRepr.LINE, GeomRepr.SHELL, GeomRepr.SOLID])
def test_concept_constraint_matches_explicit_bc(geom_repr, mesher, bc_source):
    ref = _solve_cantilever_eig(geom_repr, mesher, "explicit")
    concept = _solve_cantilever_eig(geom_repr, mesher, bc_source)

    # A free-free beam would give rigid-body modes at ~0 Hz
    assert ref[0] > 1.0
    assert concept == pytest.approx(ref, rel=1e-6)

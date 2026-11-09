"""Three-node circular and flat-bar beams solved by CalculiX: bending about the right axes, against Euler-Bernoulli."""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import LoadConceptCase, LoadConceptPoint
from ada.fem.exceptions.fea_software import FEASolverNotInstalled
from ada.fem.meshing.concepts import GmshOptions
from ada.materials.metals import CarbonSteel

LENGTH = 4.0
FORCE = 100.0


@pytest.mark.parametrize(
    "section, direction",
    [("FB100x10", "z"), ("FB100x10", "y"), ("CIRC50", "z"), ("CIRC50", "y")],
)
def test_tip_deflection_of_a_cantilever(section, direction, tmp_path):
    beam = ada.Beam("bm", (0, 0, 0), (LENGTH, 0, 0), section, ada.Material("S355", CarbonSteel("S355")))
    part = ada.Part("beam") / beam
    assembly = ada.Assembly("a") / part
    fixed = [Dof(d, "fixed") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
    part.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("fix", (0, 0, 0), fixed))
    force = (0, -FORCE, 0) if direction == "y" else (0, 0, -FORCE)
    load = LoadConceptPoint("tip", (LENGTH, 0, 0), force, (0, 0, 0))
    part.concept_fem.loads.add_load_case(LoadConceptCase("LC", [load], fem_loadcase_number=1))
    part.fem = part.to_fem_obj(LENGTH / 8, bm_repr="line", options=GmshOptions(Mesh_ElementOrder=2))
    try:
        result = assembly.to_fem(
            "beam", "calculix", overwrite=True, execute=True, scratch_dir=tmp_path, exit_on_complete=False
        )
    except FEASolverNotInstalled:
        pytest.skip("calculix is not installed")

    coords = np.asarray(result.mesh.nodes.coords, dtype=float)
    field = [f for f in result.results if f.name.startswith("DISP")][-1]
    tip = np.abs(coords[:, 0] - LENGTH) < 1e-6
    measured = abs(np.asarray(field.values, dtype=float)[tip][:, 2 if direction == "y" else 3].mean())
    inertia = beam.section.properties.Iz if direction == "y" else beam.section.properties.Iy
    theory = FORCE * LENGTH**3 / (3 * beam.material.model.E * inertia)
    # Measured 0.97 to 0.99: the expanded bricks include shear, which Euler-Bernoulli doesn't. A swapped axis is off by 100 times.
    assert 0.96 < measured / theory < 1.0

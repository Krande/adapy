"""A point mass inside a plate, solved by Code_Aster under gravity: the base reactions carry the whole weight.

A 2 x 2 m steel plate, 10 mm thick, meshed at 0.5 m and fixed along its edges, carries a 100 kg ``MassPoint`` at
(0.9, 1.1), which is no vertex of the mesh. Measured before the fix (Code_Aster 18.1): the mass was on a node of its
own that no element held, its ``DIS_T`` had no stiffness, and the static solve stopped at ``FACTOR_11`` (singular
matrix) naming that node's id. Now the mass's position is a mesh vertex the shells hold, and the vertical reactions
sum to the weight of the plate and the mass.

A mass off the structure is left out by the Code_Aster writer, and said so: the rest of the model still solves, and
its reactions are the plate's weight alone.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.mass import MassPoint
from ada.fem import Bc, FemSet, StepImplicitStatic
from ada.fem.formats import conversion_report

MASS, G, SIDE, T = 100.0, 9.81, 2.0, 0.01


def _model(mass_at) -> tuple[ada.Assembly, float]:
    p = ada.Part("P")
    pl = p.add_plate(ada.Plate("pl", [(0, 0), (SIDE, 0), (SIDE, SIDE), (0, SIDE)], T))
    p.add_mass(MassPoint("m", mass_at, MASS))
    p.fem = p.to_fem_obj(0.5)
    edge = [n for n in p.fem.nodes if n.z == 0.0 and (n.x in (0.0, SIDE) or n.y in (0.0, SIDE))]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("edge", edge, "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("static")).add_load(ada.fem.LoadGravity("grav", -G))
    return a, pl.material.model.rho * SIDE * SIDE * T


def _vertical_reaction(a: ada.Assembly, tmp_path, name: str) -> float:
    res = a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    if res is None:
        from ada.fem.formats.utils import default_fem_res_path

        res = ada.from_fem_res(default_fem_res_path(name, scratch_dir=tmp_path, fem_format="code_aster"))
    reac = next(np.asarray(r.values, float) for r in res.results if "REAC_NODA" in r.name)
    return float(np.nansum(reac[:, 3]))


def test_a_mass_inside_a_plate_solves_and_carries_its_weight(tmp_path, require_solver):
    require_solver("code_aster")
    a, plate_mass = _model((0.9, 1.1, 0.0))
    with conversion_report.collect() as rep:
        reaction = _vertical_reaction(a, tmp_path, "pm_plate")
    assert reaction == pytest.approx((plate_mass + MASS) * G, rel=1e-6)
    assert not [f for f in rep.findings if f.keyword == "Mass"]


def test_a_mass_off_the_structure_is_left_out_and_the_rest_solves(tmp_path, require_solver):
    require_solver("code_aster")
    a, plate_mass = _model((0.9, 1.1, 0.5))
    with conversion_report.collect() as rep:
        reaction = _vertical_reaction(a, tmp_path, "pm_off")
    assert reaction == pytest.approx(plate_mass * G, rel=1e-6)
    (finding,) = [f for f in rep.findings if f.keyword == "Mass" and f.kind == "omitted"]
    assert finding.details["positions"] == [[0.9, 1.1, 0.5]]

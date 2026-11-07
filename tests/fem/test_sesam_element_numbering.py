"""Sestra solves a model whose element ids are not 1..n, and its results come back under the model's ids.

Sestra wants internal element numbers 1..n ("Internal element ids must be contiguous in the range [1, number of
elements]"); the writer numbers them so and keeps the model's ids as the external ones (write/element_numbers). A
2 x 1 m plate of shells numbered from 100 with gaps, clamped along x = 0 and pressed by p: the reactions hold back
exactly p A, and every shell stress the SIN gives is on one of the model's shells.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPressure, StepImplicitStatic, Surface
from ada.materials.metals import CarbonSteel


@pytest.mark.parametrize("solver", ["sesam"])
def test_a_plate_numbered_with_gaps_is_solved_and_read_back_under_its_own_ids(tmp_path, require_solver, solver):
    require_solver(solver)
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01, mat=mat)
    p = ada.Part("P") / pl
    p.fem = pl.to_fem_obj(0.25, "shell")
    fem = p.fem
    shells = sorted(fem.elements.shell, key=lambda e: e.id)
    for i, el in enumerate(shells):
        el.id = 100 + 3 * i + (i % 2)
    fem.elements.renumber(renumber_map={e.id: e.id for e in fem.elements})
    ids = {int(e.id) for e in shells}
    fem.add_bc(Bc("fix", fem.add_set(FemSet("edge", [n for n in fem.nodes if n.x < 1e-9], "nset")), [1, 2, 3, 4, 5, 6]))
    every = fem.add_set(FemSet("all_shells", shells, "elset"))
    surf = fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, every, el_face_index=1))
    pressure = 1000.0
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("press")).add_load(LoadPressure("q", pressure, surf))

    res = a.to_fem("gaps", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    ((_, fields),) = res.case_fields("nodal_reaction", "shell_stress").items()
    by_role = {getattr(f.role, "value", f.role): f for f in fields}
    reactions = np.asarray(by_role["nodal_reaction"].values, float)
    assert abs(reactions[:, 3].sum()) == pytest.approx(pressure * 2.0 * 1.0, rel=1e-5)
    stressed = {int(r[0]) for r in np.asarray(by_role["shell_stress"].values, float)}
    assert stressed == ids, "the SIN's element results, under the model's element ids"

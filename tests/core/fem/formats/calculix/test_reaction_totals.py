"""CalculiX prints each support's total force (``*NODE PRINT, TOTALS=ONLY``), summed before printing.

The .frd's nodal forces carry six digits each, and a sum over the expanded nodes of second-order shells kept only what
those allow (1999.8828 N for 2000 N on 8-node shells); the totals in the .dat file: 999.9992 + 999.9994 N
(``tests/fem/test_calculix_code_aster_solve.py``, the edge-load test).
"""

from __future__ import annotations

import re

import numpy as np

import ada
from ada.fem import FieldOutput
from ada.fem.concept.constraints import ConstraintConceptCurve
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import LoadConceptCase, LoadConceptPoint, LoadConceptSurface
from ada.materials.metals import CarbonSteel

#: ccx 2.23's .dat for the edge-load strip on 8-node shells, both load cases (two *STEPs), as written.
DAT = """

                        S T E P       1


                                INCREMENT     1


 total force (fx,fy,fz) for set X0_SET and time  0.1000000E+03

        2.125470E-05  3.000881E-05  9.999992E+02

 total force (fx,fy,fz) for set XL_SET and time  0.1000000E+03

       -1.143844E-07  1.115476E-05  9.999994E+02

                        S T E P       2


                                INCREMENT     1


 total force (fx,fy,fz) for set X0_SET and time  0.2000000E+03

        4.303303E-05  5.932946E-05  1.833332E+03

 total force (fx,fy,fz) for set XL_SET and time  0.2000000E+03

       -6.720678E-08  2.243021E-05  2.166665E+03
"""


def _dofs(fixed):
    return [Dof(d, "fixed" if d in fixed else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]


def test_the_totals_are_read_by_step_and_set(tmp_path):
    from ada.fem.formats.calculix.results.read_dat import read_reaction_totals

    path = tmp_path / "edge.dat"
    path.write_text(DAT)
    totals = read_reaction_totals(path)
    assert sorted(totals) == [(1, "X0_SET"), (1, "XL_SET"), (2, "X0_SET"), (2, "XL_SET")]
    assert np.array_equal(totals[(2, "XL_SET")], [-6.720678e-08, 2.243021e-05, 2166.665])


def test_a_static_step_with_rf_prints_each_support_sets_total(tmp_path):
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 0.5), (0, 0.5)], 0.01, mat=mat)
    p = ada.Part("P") / pl
    a = ada.Assembly("A") / p
    c = p.concept_fem.constraints
    c.add_curve_constraint(ConstraintConceptCurve("x0", (0, 0, 0), (0, 0.5, 0), _dofs(("dx", "dy", "dz"))))
    c.add_curve_constraint(ConstraintConceptCurve("xL", (4, 0, 0), (4, 0.5, 0), _dofs(("dy", "dz"))))
    p.concept_fem.loads.add_load_case(LoadConceptCase("LC", [LoadConceptSurface("P", pl, pressure=1e3, side="front")]))
    p.fem = p.to_fem_obj(0.5, use_quads=True)
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    a.to_fem("d", "calculix", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "d" / "d.inp").read_text()
    step = deck[deck.index("*Step") :]
    assert re.findall(r"\*Node print, nset=(\w+), totals=only\nRF\n", step) == ["x0_set", "xL_set"]


def test_u1_beam_supports_print_no_total(tmp_path):
    """RF at a U1 beam's node is the element's end force, not a reaction (measured +500 / -500 N for two 500 N
    reactions): no total is printed for such a set."""
    bm = ada.Beam("bm", (0, 0, 0), (4, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    a = ada.Assembly("ss") / p
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("pin", (0, 0, 0), Dof.encastre()))
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC", [LoadConceptPoint("pm", (4, 0, 0), (0, 0, -1e3), (0, 0, 0))])
    )
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    p.fem.steps[0].add_field_output(FieldOutput("rf", nodal=["U", "RF"]))
    a.to_fem("d", "calculix", scratch_dir=tmp_path, overwrite=True)
    assert "*Node print" not in (tmp_path / "d" / "d.inp").read_text()

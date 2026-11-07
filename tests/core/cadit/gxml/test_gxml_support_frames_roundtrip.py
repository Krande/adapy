"""A support in a rotated frame through GeniE XML and back: the ``local_system`` is written and read again."""

from __future__ import annotations

import numpy as np

import ada
from ada import ConstraintConceptCurve, ConstraintConceptPoint
from ada import ConstraintConceptDofType as Dof
from ada.materials.metals import CarbonSteel

R = np.sqrt(0.5)
DIAGONAL = ((R, R, 0.0), (-R, R, 0.0), (0.0, 0.0, 1.0))
SLOPE = ((0.6, 0.0, 0.8), (0.0, 1.0, 0.0), (-0.8, 0.0, 0.6))


def test_rotated_point_and_curve_supports_survive_a_genie_xml_round_trip(tmp_path):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (3, 0, 4), "IPE300", mat)
    p = ada.Part("P") / bm
    c = p.concept_fem.constraints
    c.add_point_constraint(
        ConstraintConceptPoint(
            "sp",
            (0, 0, 0),
            [Dof("dx", "fixed"), Dof("dz", "fixed")] + [Dof(d, "free") for d in ("dy", "rx", "ry", "rz")],
            local_system=DIAGONAL,
        )
    )
    c.add_curve_constraint(
        ConstraintConceptCurve(
            "sc",
            (0, 0, 0),
            (3, 0, 4),
            [Dof("dx", "fixed")] + [Dof(d, "free") for d in ("dy", "dz", "rx", "ry", "rz")],
            local_system=SLOPE,
        )
    )
    (ada.Assembly("A") / p).to_genie_xml(tmp_path / "frames.xml")

    back = ada.from_genie_xml(tmp_path / "frames.xml")
    (part,) = back.get_all_subparts()
    got = part.concept_fem.constraints
    sp, sc = got.point_constraints["sp"], got.curve_constraints["sc"]
    assert np.allclose(sp.local_system, DIAGONAL, atol=1e-9)
    assert {d.dof for d in sp.dof_constraints if d.constraint_type == "fixed"} == {"dx", "dz"}
    assert np.allclose(sc.local_system, SLOPE, atol=1e-9)
    assert {d.dof for d in sc.dof_constraints if d.constraint_type == "fixed"} == {"dx"}

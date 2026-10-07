"""The Calculix deck's beams, steps and loads, by their text (``tests/fem/test_calculix_code_aster_solve.py`` solves).

* A two-node beam is a ``U1`` general section ``A, Iy, 0, Iz, 1e8`` with local z: the fifth value is U1's shear
  coefficient, not the torsion constant, and the first inertia carries bending along the direction given (ccx 2.23,
  measured; the deck before deflected 11.79 m under 1 kN).
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem.concept.constraints import ConstraintConceptDofType as Dof
from ada.fem.concept.constraints import ConstraintConceptPoint
from ada.fem.concept.loads import LoadConceptCase, LoadConceptLine, LoadConceptPoint
from ada.fem.formats import conversion_report
from ada.fem.formats.calculix.write.writer import U1_SHEAR_COEFFICIENT
from ada.materials.metals import CarbonSteel

L = 4.0


def _beam(cases=True):
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", ada.Material("S355", CarbonSteel("S355")))
    p = ada.Part("beam") / bm
    a = ada.Assembly("ss") / p
    fixed = [Dof(d, "fixed" if d in ("dx", "dy", "dz", "rx") else "free") for d in ("dx", "dy", "dz", "rx", "ry", "rz")]
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("pin", (0, 0, 0), fixed))
    p.concept_fem.constraints.add_point_constraint(ConstraintConceptPoint("roll", (L, 0, 0), fixed[:3]))
    if cases:
        ld = p.concept_fem.loads
        q = (0, 0, -1000.0)
        ld.add_load_case(LoadConceptCase("LC_u", [LoadConceptLine("u", (0, 0, 0), (L, 0, 0), q, q)]))
        ld.add_load_case(LoadConceptCase("LC_p", [LoadConceptPoint("pm", (2.0, 0, 0), (0, 0, -1e4), (0, 0, 0))]))
    p.fem = p.to_fem_obj(0.5, bm_repr="line")
    return a, p, bm


def _deck(a, tmp_path, name="d"):
    with conversion_report.collect() as report:
        a.to_fem(name, "calculix", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.inp").read_text(), report


def test_a_two_node_beam_is_a_u1_general_section_with_its_shear_coefficient_and_local_z(tmp_path):
    a, p, bm = _beam()
    inp, report = _deck(a, tmp_path)
    assert "*ELEMENT, type=U1, ELSET=elbm_set_bm" in inp
    head, values, direction = re.search(r"(\*Beam Section, [^\n]*section=GENERAL)\n([^\n]*)\n([^\n]*)", inp).groups()
    props = bm.section.properties
    a_, i1, i12, i2, kappa = (float(v) for v in values.split(","))
    assert (a_, i1, i12, i2) == pytest.approx((props.Ax, props.Iy, 0.0, props.Iz))
    assert kappa == U1_SHEAR_COEFFICIENT == 1e8, "U1's fifth value is its shear coefficient"
    assert [float(v) for v in direction.split(",")] == pytest.approx([0.0, 0.0, 1.0]), "Iy bends along local z"
    torsion = [f for f in report.findings if f.keyword == "*BEAM SECTION" and f.kind == "approximated"]
    assert torsion and torsion[0].details["ratio"] == pytest.approx((props.Iy + props.Iz) / props.Ix)

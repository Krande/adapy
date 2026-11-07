"""Abaqus and CalculiX write an eccentric beam on its nodes, and say so in the conversion report.

``to_fem_obj`` gives a stiffener flush on its plate the offset to its neutral axis (``Elem.eccentricity``); Sesam
writes it as GECCEN and Code_Aster as offset nodes with rigid links. The Abaqus writer (no ``*CENTROID`` yet) and
the CalculiX writer (``OFFSET1``/``2`` cover rectangles, boxes and pipes only) have no form for it and put the beam in
the plane of its nodes -- a stiffened panel then deflects several times what the eccentric model gives. A model
written to either gets one ``omitted`` finding per FEM naming how many elements' offsets were dropped
(``ada.fem.formats.utils.report_dropped_eccentricities``), so the loss is on record rather than silent.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Bc, FemSet, StepImplicitStatic
from ada.fem.formats import conversion_report

PL = [(0, 0), (2.0, 0), (2.0, 1.0), (0, 1.0)]


def _model(flush: bool):
    pl = ada.Plate("pl", PL, 0.006, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
    # A symmetric section: the CalculiX writer refuses an angle's product of inertia (U1 takes principal axes only),
    # which is a different finding from the one measured here.
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "IPE300", up=(0, 0, 1))
    if flush:
        bm.justification = "flush top"
    p = ada.Part("P") / [pl, bm]
    p.fem = p.to_fem_obj(0.25, bm_repr="line", pl_repr="shell")
    edge = [n for n in p.fem.nodes if abs(n.x) < 1e-9]
    p.fem.add_bc(Bc("root", p.fem.add_set(FemSet("root", edge, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("grav")).add_load(ada.fem.LoadGravity("g", -9.81))
    return a, p


def _dropped(report, stage):
    return [f for f in report.findings if f.kind == "omitted" and f.keyword == "Element" and f.stage == stage]


@pytest.mark.parametrize("fem_format, stage", [("abaqus", "abaqus writer"), ("calculix", "calculix writer")])
def test_dropped_eccentricities_are_reported(fem_format, stage, tmp_path):
    a, p = _model(flush=True)
    n_ecc = len(list(p.fem.elements.lines_ecc))
    assert n_ecc == len(list(p.fem.elements.lines)) > 1

    with conversion_report.collect() as report:
        a.to_fem("ecc", fem_format, scratch_dir=tmp_path, overwrite=True)

    (finding,) = _dropped(report, stage)
    assert finding.count == n_ecc
    assert finding.subject == p.fem.name
    assert "eccentricity" in finding.reason and "Code_Aster" in finding.reason


@pytest.mark.parametrize("fem_format, stage", [("abaqus", "abaqus writer"), ("calculix", "calculix writer")])
def test_a_beam_on_its_axis_is_not_reported(fem_format, stage, tmp_path):
    a, p = _model(flush=False)
    with conversion_report.collect() as report:
        a.to_fem("flat", fem_format, scratch_dir=tmp_path, overwrite=True)
    assert not _dropped(report, stage)

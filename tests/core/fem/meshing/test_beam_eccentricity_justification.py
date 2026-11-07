"""A beam placed flush on its plate by justification is meshed with its eccentricity.

``to_fem_obj`` meshes a beam on its nodal line and gives the line elements the offset from that line to the
beam's neutral axis (``Elem.eccentricity``), the way a FE program models a stiffener whose axis is not in the
plate: nodes shared with the shell mesh, the element eccentric. It used to read ``Beam.e1``/``e2`` alone, so a
stiffener placed ``"flush top"`` by justification -- a deck generator's and a GeniE import's way -- was meshed
with its neutral axis in the plate, and a stiffened panel deflected several times what the eccentric model
gives. The vectors here are what GeniE writes as GECCEN for the same sections on a plate: for an angle the
centroid is ``Cgz`` from the free leg, so the axis hangs ``h - Cgz`` below the plate it is flush with.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem.formats.sesam.read import cards

PL = [(0, 0), (2.0, 0), (2.0, 1.0), (0, 1.0)]


def _panel(beam: ada.Beam) -> ada.Part:
    pl = ada.Plate("pl", PL, 0.006, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
    return ada.Part("p") / [pl, beam]


def _line_elems(fem):
    return list(fem.elements.lines)


def _assert_all_eccentric(fem, expected: np.ndarray):
    lines = _line_elems(fem)
    assert len(lines) > 1
    for el in lines:
        ecc = el.eccentricity
        assert ecc is not None, f"element {el.id} has no eccentricity"
        for end, node in ((ecc.end1, el.nodes[0]), (ecc.end2, el.nodes[-1])):
            assert end is not None
            assert end.node.id == node.id
            assert np.allclose(end.ecc_vector, expected, atol=1e-9), f"{end.ecc_vector} != {expected}"


def _shared_with_shell(fem) -> bool:
    shell_nodes = {n.id for el in fem.elements.shell for n in el.nodes}
    return all(n.id in shell_nodes for el in _line_elems(fem) for n in el.nodes)


def test_angle_flush_top_hangs_below_the_plate_by_h_minus_cgz():
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "HP160x8", up=(0, 0, 1))
    bm.justification = "flush top"
    p = _panel(bm)
    fem = p.to_fem_obj(0.25, bm_repr="line", pl_repr="shell")

    sec = bm.section
    expected = np.array([0.0, 0.0, -(sec.h - sec.properties.Cgz)])
    assert expected[2] == pytest.approx(-0.09623, abs=1e-5)  # GeniE's GECCEN for an HP160x8 on a plate
    _assert_all_eccentric(fem, expected)
    assert _shared_with_shell(fem), "the eccentric beam keeps the plate's nodes; it is not meshed on offset nodes"


def test_t_girder_flush_bottom_with_up_reversed_hangs_below_by_cgz():
    # Flange down, placed under the plate: ``up`` points away from the plate and the web end is flush with it.
    bm = ada.Beam("g", (0, 0.5, 0), (2.0, 0.5, 0), "TG600x300x20x32", up=(0, 0, -1))
    bm.justification = "flush bottom"
    p = _panel(bm)
    fem = p.to_fem_obj(0.5, bm_repr="line", pl_repr="shell")

    cgz = bm.section.properties.Cgz
    expected = np.array([0.0, 0.0, -cgz])
    assert expected[2] == pytest.approx(-0.4214, abs=1e-4)  # GeniE's GECCEN for the same girder
    _assert_all_eccentric(fem, expected)
    assert _shared_with_shell(fem)


def test_symmetric_section_flush_top_hangs_by_half_its_height():
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "IPE300", up=(0, 0, 1))
    bm.justification = "flush top"
    fem = _panel(bm).to_fem_obj(0.25, bm_repr="line", pl_repr="shell")
    _assert_all_eccentric(fem, np.array([0.0, 0.0, -bm.section.h / 2]))


def test_beam_on_its_neutral_axis_has_no_eccentricity():
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "IPE300", up=(0, 0, 1))
    fem = _panel(bm).to_fem_obj(0.25, bm_repr="line", pl_repr="shell")
    lines = _line_elems(fem)
    assert lines and all(el.eccentricity is None for el in lines)


def test_explicit_e_is_the_negated_eccentricity():
    # ``Beam.e1``/``e2`` are the negation of the file's vector (see test_write_geccen); the justification path must
    # not change that.
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "IPE300", up=(0, 0, 1), e1=(0, 0, 0.15), e2=(0, 0, 0.15))
    fem = _panel(bm).to_fem_obj(0.25, bm_repr="line", pl_repr="shell")
    _assert_all_eccentric(fem, np.array([0.0, 0.0, -0.15]))


def test_angle_eccentricity_survives_a_sesam_round_trip(tmp_path):
    """Written as GECCEN and read back, an angle's beam sits where the deck had its neutral axis.

    ``line_elem_to_beam`` turns the file's node-to-axis vector into ``e1``/``e2``; for an angle it has to take the
    centroid shift off, or the beam comes back ``Cgz - h`` further out and writes a different GECCEN next time.
    """
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "HP160x8", up=(0, 0, 1))
    bm.justification = "flush top"
    p = _panel(bm)
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, bm_repr="line", pl_repr="shell")
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    deck = tmp_path / "m" / "mT1.FEM"

    vectors = {
        int(float(m.groupdict()["eccno"])): (float(m.groupdict()[c]) for c in ("ex", "ey", "ez"))
        for m in cards.re_geccen.finditer(deck.read_text())
    }
    assert len(vectors) == 1
    assert np.allclose(list(next(iter(vectors.values()))), bm.offset_helper.curve_offset_global()[0])

    b = ada.from_fem(deck, create_concept_objects=True)
    beams = list(b.get_all_physical_objects(by_type=ada.Beam))
    assert beams
    for back in beams:
        got1, got2 = back.offset_helper.curve_offset_global()
        want1, want2 = bm.offset_helper.curve_offset_global()
        assert np.allclose(got1, want1, atol=1e-6) and np.allclose(got2, want2, atol=1e-6)

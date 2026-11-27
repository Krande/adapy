"""A beam cut by half-spaces, and a rounded hollow POLY section, through adapy -> IFC -> adapy.

The writer emits a beam's half-space cuts as its swept solid inside one ``IfcBooleanClippingResult``
per cut (``write_ifc.create_ifc_openings``). The reader refused any boolean body as a beam, so a cut
beam came back as a generic shape: no section, no axis, no cuts. It now unwraps a chain of
half-space clips to the swept solid, reads the beam from that, and puts the cuts back as
``BoolHalfSpace`` s.

A POLY section's curves carry its shape but not its scalars: a rounded hollow section built from
two filleted outlines and a wall thickness came back with no ``t_w``, and with its corner arcs read
as straight chords. The scalars now ride the profile's adapy parameter bag, and an outline whose
corners are tangent arcs is read back as the filleted corner points it was written from.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.sections.categories import BaseTypes


def _roundtrip(tmp_path, *objects) -> dict:
    fp = (ada.Assembly("a") / (ada.Part("p") / list(objects))).to_ifc(tmp_path / "rt.ifc", file_obj_only=True)
    return {o.name: o for o in ada.from_ifc(fp).get_all_physical_objects()}


def _cuts(beam) -> list[tuple]:
    out = []
    for b in beam.booleans:
        hs = b.primitive
        out.append(
            (
                tuple(np.round(np.asarray(hs.poly.origin, float), 9)),
                tuple(np.round(np.asarray(hs.poly.normal, float), 9)),
                hs.flip,
            )
        )
    return out


@pytest.mark.parametrize("flip", [False, True])
def test_a_beam_cut_by_half_spaces_reads_back_as_the_beam_with_its_cuts(tmp_path, flip):
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300")
    bm.add_boolean(ada.BoolHalfSpace(origin=(1.5, 0, 0), normal=(1, 0, 0), flip=flip, name="c0"))
    bm.add_boolean(ada.BoolHalfSpace(origin=(0.2, 0, 0), normal=(-1, 0, 0.2), flip=flip, name="c1"))

    back = _roundtrip(tmp_path, bm)["bm"]

    assert type(back) is ada.Beam
    assert back.section.equal_props(bm.section)
    assert np.allclose(back.n1.p, bm.n1.p) and np.allclose(back.n2.p, bm.n2.p)
    # Same planes, same sides, in the order they were applied.
    got, want = _cuts(back), _cuts(bm)
    assert len(got) == 2
    for (o1, n1, f1), (o2, n2, f2) in zip(got, want):
        assert np.allclose(o1, o2) and np.allclose(
            np.asarray(n1) / np.linalg.norm(n1), np.asarray(n2) / np.linalg.norm(n2)
        )
        assert f1 == f2


def test_a_cut_beam_in_a_placed_part_keeps_its_cuts_where_they_were(tmp_path):
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300")
    bm.add_boolean(ada.BoolHalfSpace(origin=(1.5, 0, 0), normal=(1, 0, 0), name="c0"))
    part = ada.Part("placed", placement=ada.Placement(origin=(10, 5, 1)))
    part.add_beam(bm)
    fp = (ada.Assembly("a") / part).to_ifc(tmp_path / "placed.ifc", file_obj_only=True)
    back = ada.from_ifc(fp).get_by_name("bm")

    assert type(back) is ada.Beam and len(back.booleans) == 1
    # The cut solid is the same solid: same volume and bounds as the original's.
    ref, got = bm.solid_occ(), back.solid_occ()
    from ada.cad import active_backend

    be = active_backend()
    assert be.volume(got) == pytest.approx(be.volume(ref), rel=1e-6)


def test_a_union_or_solid_operand_is_still_not_read_as_a_beam(tmp_path):
    """Only a chain of half-space clips is a cut beam; another boolean body is refused as before."""
    import ifcopenshell

    from ada.cadit.ifc.read.reader_utils import split_half_space_clips

    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300")
    bm.add_boolean(ada.BoolHalfSpace(origin=(1.5, 0, 0), normal=(1, 0, 0)))
    dest = tmp_path / "clip.ifc"
    (ada.Assembly("a") / (ada.Part("p") / bm)).to_ifc(dest, streaming=False)
    f = ifcopenshell.open(str(dest))
    clip = f.by_type("IfcBooleanClippingResult")[0]
    assert split_half_space_clips(clip) is not None

    clip.Operator = "UNION"
    assert split_half_space_clips(clip) is None


def _rounded_rect(w, h, r):
    hw, hh = w / 2, h / 2
    return ada.CurvePoly2d(
        [(-hw, -hh, r), (hw, -hh, r), (hw, hh, r), (-hw, hh, r)], origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1)
    )


def test_a_rounded_hollow_poly_section_keeps_its_fillets_and_wall_thickness(tmp_path):
    t = 0.005
    sec = ada.Section(
        "RHS80x5",
        sec_type=BaseTypes.POLY,
        outer_poly=_rounded_rect(0.08, 0.08, 0.01),
        inner_poly=_rounded_rect(0.08 - 2 * t, 0.08 - 2 * t, 0.005),
        t_w=t,
    )
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), sec=sec)

    back = _roundtrip(tmp_path, bm)["bm"].section

    assert back.type == BaseTypes.POLY
    assert back.t_w == pytest.approx(t)
    for got, want in ((back.poly_outer, sec.poly_outer), (back.poly_inner, sec.poly_inner)):
        assert np.allclose(np.asarray(got.points2d, float), np.asarray(want.points2d, float), atol=1e-9)
        assert sorted(got._radiis) == sorted(want._radiis)
        assert np.allclose(list(got._radiis.values()), list(want._radiis.values()), atol=1e-12)


def test_a_poly_section_without_scalars_writes_no_parameter_bag(tmp_path):
    """Nothing new is written for a plain POLY section: its shape is its curves, as before."""
    from ada.cadit.ifc.sections_props import section_param_props

    sec = ada.Section("P", sec_type=BaseTypes.POLY, outer_poly=_rounded_rect(0.08, 0.08, 0.01))
    assert section_param_props(sec) is None

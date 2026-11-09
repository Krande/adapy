"""A beam's flush alignment and its section centroid reach the FEM as an eccentricity (issue #419).

GeniE measures a beam's eccentricity from the node to the section **centroid**. A stiffener that
GeniE places flush against its plate therefore comes out of ``T1.FEM`` with a ``GECCEN`` record,
while adapy used to read the alignment into ``Beam.justification`` and mesh the beam on its node line
with nothing on the elements -- so every FEM writer left it out and the model came out too soft.

The numbers are the ones the issue measured against GeniE V9.2-01 (z of the GECCEN, beam along +x,
up = +z):

=================  ===========  =============
section            flush_top    flush_bottom
=================  ===========  =============
HP220x10           -0.1354302   +0.0845698
TG300x500x20x32    -0.0536405   +0.2463596
IPE300             -0.15        +0.15
=================  ===========  =============

and, for an explicit curve offset of 0.3 (what GeniE reads out of the XML adapy writes), a GECCEN of
0.3 for the HP and the T alike.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.beams.justification import Justification
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read import cards

GENIE = {
    "HP220x10": {"flush_top": -0.1354302, "flush_bottom": 0.0845698},
    "TG300x500x20x32": {"flush_top": -0.0536405, "flush_bottom": 0.2463596},
    "IPE300": {"flush_top": -0.15, "flush_bottom": 0.15},
}
FLUSH = {"flush_top": Justification.FLUSH_TOP, "flush_bottom": Justification.FLUSH_BOTTOM}
CASES = [(sec, align) for sec in GENIE for align in FLUSH]


def _model(*beams: ada.Beam, mesh_size: float = 5.0) -> tuple[ada.Assembly, ada.Part]:
    p = ada.Part("p") / list(beams)
    p.fem = p.to_fem_obj(mesh_size, "line")
    return ada.Assembly("a") / p, p


def _beam(section: str, **kwargs) -> ada.Beam:
    return ada.Beam("bm", (0, 0, 0), (4, 0, 0), section, up=(0, 0, 1), **kwargs)


def _ecc_vectors(part: ada.Part) -> list[tuple[np.ndarray, np.ndarray]]:
    out = []
    for el in part.fem.elements.lines_ecc:
        ecc = el.eccentricity
        zero = np.zeros(3)
        out.append(
            (
                np.asarray(ecc.end1.ecc_vector if ecc.end1 else zero, dtype=float),
                np.asarray(ecc.end2.ecc_vector if ecc.end2 else zero, dtype=float),
            )
        )
    return out


def _geccen_z(deck_text: str) -> list[float]:
    return sorted(float(m.groupdict()["ez"]) for m in cards.re_geccen.finditer(deck_text))


def _sesam_deck(assembly: ada.Assembly, tmp_path):
    assembly.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    return tmp_path / "m" / "mT1.FEM"


# ---- the meshing: justification + centroid -> eccentricity ---------------------------------


@pytest.mark.parametrize("section, alignment", CASES)
def test_a_flush_beam_is_meshed_with_geniEs_eccentricity(section, alignment):
    _, p = _model(_beam(section, justification=FLUSH[alignment]))

    ((e1, e2),) = _ecc_vectors(p)

    for vec in (e1, e2):
        assert vec[0] == pytest.approx(0.0, abs=1e-9)
        assert vec[1] == pytest.approx(0.0, abs=1e-9)
        assert vec[2] == pytest.approx(GENIE[section][alignment], abs=1e-6)


@pytest.mark.parametrize("section", sorted(GENIE))
def test_no_flush_writes_no_eccentricity(section):
    """``no_flush``/NA: GeniE writes no GECCEN, the centroid sits on the node line."""
    _, p = _model(_beam(section, justification=Justification.NA))

    assert _ecc_vectors(p) == []


@pytest.mark.parametrize("section", ["HP220x10", "TG300x500x20x32"])
def test_an_explicit_offset_carries_the_centroid_correction(section):
    """The issue's second claim, confirmed: ``GECCEN = -e1`` alone gave 0.4354 (HP) and 0.2036 (T)
    where GeniE has 0.3. The e1 that makes GeniE's offset 0.3 is the one that makes ours 0.3."""
    e1 = {"HP220x10": (0.0, 0.0, -0.4354302), "TG300x500x20x32": (0.0, 0.0, -0.2036404)}[section]
    _, p = _model(_beam(section, e1=e1, e2=e1))

    ((v1, v2),) = _ecc_vectors(p)

    assert v1[2] == pytest.approx(0.3, abs=1e-6)
    assert v2[2] == pytest.approx(0.3, abs=1e-6)


def test_an_offset_on_a_section_through_its_centroid_is_just_minus_e():
    """IPE300 has its origin on its centroid, so the correction is zero and nothing changed."""
    _, p = _model(_beam("IPE300", e1=(0.0, 0.0, -0.3), e2=(0.0, 0.0, -0.3)))

    ((v1, v2),) = _ecc_vectors(p)

    assert tuple(v1) == pytest.approx((0.0, 0.0, 0.3))
    assert tuple(v2) == pytest.approx((0.0, 0.0, 0.3))


def test_a_flush_beam_along_y_is_offset_along_its_own_up():
    """The vector is in the beam's frame, not the world's."""
    bm = ada.Beam("bm", (0, 0, 0), (0, 4, 0), "IPE300", up=(0, 0, 1), justification=Justification.FLUSH_TOP)
    _, p = _model(bm)

    ((v1, _),) = _ecc_vectors(p)

    assert tuple(v1) == pytest.approx((0.0, 0.0, -0.15))


def test_every_node_of_a_split_flush_beam_carries_the_offset():
    _, p = _model(_beam("HP220x10", justification=Justification.FLUSH_TOP), mesh_size=1.0)

    vectors = _ecc_vectors(p)

    assert len(vectors) == 4
    assert all(v[2] == pytest.approx(-0.1354302, abs=1e-6) for pair in vectors for v in pair)


@pytest.mark.parametrize("section", sorted(GENIE))
def test_top_of_steel_is_meshed_as_flush_top(section):
    """Top of steel is the top of the profile on the node line (E3D's CTOP): GeniE's flush_top. It used to be
    reported and meshed on the node line, because ``curve_offset_local`` and ``resolve_justification`` gave it
    opposite signs."""
    with conversion_report.collect() as report:
        _, p = _model(_beam(section, justification=Justification.TOS))

    ((e1, e2),) = _ecc_vectors(p)
    for vec in (e1, e2):
        assert vec[2] == pytest.approx(GENIE[section]["flush_top"], abs=1e-6)
    assert not [f for f in report.findings if f.kind == "omitted" and f.keyword == "Beam.justification"]


# ---- the Sesam deck -----------------------------------------------------------------------


@pytest.mark.parametrize("section, alignment", CASES)
def test_the_sesam_deck_has_geniEs_geccen(tmp_path, section, alignment):
    a, _ = _model(_beam(section, justification=FLUSH[alignment]))

    text = _sesam_deck(a, tmp_path).read_text()

    assert _geccen_z(text) == pytest.approx([GENIE[section][alignment]], abs=1e-6)


def test_a_beam_with_justification_alone_used_to_write_no_geccen(tmp_path):
    """The headline of the issue: 0 GECCEN records for 'justification alone'."""
    a, _ = _model(_beam("HP220x10", justification=Justification.FLUSH_BOTTOM))

    assert "GECCEN" in _sesam_deck(a, tmp_path).read_text()


@pytest.mark.parametrize("section", sorted(GENIE))
def test_no_offset_beam_has_no_geccen(tmp_path, section):
    a, _ = _model(_beam(section))

    assert "GECCEN" not in _sesam_deck(a, tmp_path).read_text()


@pytest.mark.parametrize("section, alignment", CASES)
def test_a_flush_beam_reads_back_with_the_origin_shift_it_was_built_with(tmp_path, section, alignment):
    """GECCEN is node-to-centroid; ``Beam.e1`` is the shift of the section *origin*. Reading the
    deck back has to take the centroid correction off again, or an HP comes back 0.1354 off."""
    a, _ = _model(_beam(section, justification=FLUSH[alignment]))
    deck = _sesam_deck(a, tmp_path)

    b = ada.from_fem(deck, create_concept_objects=True)
    (bm,) = b.get_all_physical_objects(by_type=ada.Beam)

    built = _beam(section, justification=FLUSH[alignment])
    # what the geometry path applies for the flush case, expressed as the origin shift e
    from ada.api.beams.justification import resolve_justification

    expected = np.asarray(resolve_justification(built, FLUSH[alignment]), dtype=float)
    assert np.allclose(np.asarray(bm.e1, dtype=float), expected, atol=1e-6)
    assert np.allclose(np.asarray(bm.e2, dtype=float), expected, atol=1e-6)


# ---- Abaqus, CalculiX, Code_Aster ----------------------------------------------------------


def _mpc_node_positions(part: ada.Part) -> list[np.ndarray]:
    out = []
    for con in part.fem.constraints.values():
        if con.type == con.TYPES.MPC:
            out.append(np.asarray(con.m_set.members[0].p, dtype=float))
    return out


def test_abaqus_ties_a_flush_t_by_an_mpc_that_puts_its_flange_on_the_node_line(tmp_path):
    """The T's origin is mid-height and its centroid 0.0964 above that; flush_top wants the
    centroid 0.0536 below the node, i.e. the origin 0.15 below it -- h/2, the flange on the node."""
    a, p = _model(_beam("TG300x500x20x32", justification=Justification.FLUSH_TOP))
    a.to_fem("m", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    deck = "".join(f.read_text() for f in (tmp_path / "m").rglob("*.inp"))
    positions = _mpc_node_positions(p)

    assert "*MPC" in deck
    assert sorted(round(float(v[2]), 6) for v in positions) == [-0.15, -0.15]


def test_abaqus_needs_no_mpc_for_a_flush_top_angle(tmp_path):
    """An angle hangs from its origin, so flush_top is exactly the profile origin on the node."""
    a, p = _model(_beam("HP220x10", justification=Justification.FLUSH_TOP))
    a.to_fem("m", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    assert _mpc_node_positions(p) == []


# ---- the GeniE XML writer ----------------------------------------------------------------------


def _curve_offset(beam: ada.Beam):
    import xml.etree.ElementTree as ET

    from ada.cadit.gxml.write.write_beams import add_curve_offset

    el = ET.Element("straight_beam")
    add_curve_offset(beam, el)
    (child,) = list(el.find("curve_offset"))
    return child


@pytest.mark.parametrize("section", sorted(GENIE))
def test_the_xml_writer_writes_no_offset_for_a_beam_with_none(section):
    """GeniE puts the centroid of a beam with no curve offset on its node line; the writer used to write an HP back
    with a -0.1354 constant offset (its origin on the node line)."""
    assert _curve_offset(_beam(section)).tag == "reparameterized_beam_curve_offset"


def test_the_xml_writer_writes_top_of_steel_as_flush_top():
    off = _curve_offset(_beam("HP220x10", justification=Justification.TOS))
    assert (off.tag, off.get("alignment")) == ("aligned_curve_offset", "flush_top")


def test_a_writer_without_beam_offsets_reports_them_omitted(tmp_path):
    # IPE300, not an HP: CalculiX refuses an angle's product of inertia for a reason of its own
    a, _ = _model(_beam("IPE300", justification=Justification.FLUSH_TOP))

    with conversion_report.collect() as report:
        a.to_fem("m", fem_format="calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    found = [f for f in report.findings if f.kind == "omitted" and f.keyword == "Beam eccentricity"]
    assert len(found) == 1 and found[0].count == 1
    assert "calculix" in found[0].stage


def test_code_aster_writes_beam_offsets_instead_of_omitting_them(tmp_path):
    """Code_Aster puts an eccentric beam on offset nodes tied to its mesh nodes (write_eccentricity): nothing omitted."""
    a, _ = _model(_beam("IPE300", justification=Justification.FLUSH_TOP))

    with conversion_report.collect() as report:
        a.to_fem("m", fem_format="code_aster", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)

    assert not [f for f in report.findings if f.kind == "omitted" and f.keyword == "Beam eccentricity"]
    assert "ecc_links" in (tmp_path / "m" / "m.comm").read_text()


def test_abaqus_reports_offsets_it_was_told_not_to_convert(tmp_path):
    from ada.config import Config

    a, _ = _model(_beam("HP220x10", justification=Justification.FLUSH_BOTTOM))
    key = "ADA_FEM_CONVERT_OPTIONS_ECC_TO_MPC"
    Config().update_config_globally("fem_convert_options_ecc_to_mpc", False)
    try:
        with conversion_report.collect() as report:
            a.to_fem("m", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    finally:
        import os

        os.environ.pop(key, None)
        Config().reload_config()

    assert [f for f in report.findings if f.kind == "omitted" and f.keyword == "Beam eccentricity"]

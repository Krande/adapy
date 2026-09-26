"""Plates in the Abaqus/CAE writer: the body, the points that locate its faces, and the stringers.

Licence-free. What is asserted here is what adapy computes *before* CAE sees anything -- the faces
the ACIS body carries, the point that finds each one, the thickness and material each gets, and which
members lie on a plate and therefore become ``Stringer`` features rather than wires. Whether CAE then
agrees is settled by running the script; ``test_cae_licensed_acceptance.py`` does that.

Three measured facts shape almost every test in this file, and each is stated where it is used:

* ``PartFromGeometryFile`` discards the ACIS face names -- ``part.sets.keys()`` is ``[]`` after the
  import -- so a face can only be located by a point on it;
* an ordinary edge shared with a shell face produces **no beam elements at all**, and
  ``Part.Stringer`` on the same edge produces them with every node shared. So a beam lying on a plate
  is a stringer, and the two kinds of member are built differently;
* a plate's own boundary edges legitimately carry no section, which is why "every edge is sectioned"
  had to become three clauses rather than being relaxed to "some are".
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import ada
from ada.api.plates.base_pl import PlateCurved
from ada.cadit.cae.plates import (
    PLATE_AREA_REL_TOL,
    PlateNotSupported,
    interior_point_2d,
    plate_body,
    stringer_members,
)
from ada.cadit.cae.writer import CaeWriteError, build_plan, write_cae_script
from ada.geom import Geometry

from .cae_script_graph import check_emitted_script
from .conftest import require_writer

require_writer()


# ------------------------------------------------------------------------------------ models


def a_deck(name="deck", t=0.012, y=0.0, mat="S355") -> ada.Plate:
    """A 3 x 2 m plate in the z = 0 plane, offset along y so several of them do not overlap.

    Coincident outlines are not a shortcut: the imprint fuses them into ONE face, and the writer then
    refuses the model because two plates would claim it -- see
    ``test_two_plates_at_the_same_place_are_refused_rather_than_sharing_one_face``.
    """
    return ada.Plate(name, [(0, y), (3, y), (3, y + 2), (0, y + 2)], t, mat=mat)


def one_part(*objects, name="Deck") -> ada.Part:
    part = ada.Part(name)
    part / objects
    ada.Assembly("A") / part
    return part


def emit(part, tmp_path, name="out.py", **kwargs):
    written = write_cae_script(part, tmp_path / name, **kwargs)
    return written, written[0].read_text(encoding="utf-8")


# ------------------------------------------------------------- the body and its face locators


def test_a_flat_plate_becomes_one_face_with_an_interior_point_and_its_own_area():
    body = plate_body(one_part(a_deck()))

    assert len(body.plates) == 1
    plate = body.plates[0]
    assert plate.kind == "Plate"
    assert plate.thickness == 0.012
    assert plate.area == pytest.approx(6.0)
    assert plate.normal == pytest.approx((0.0, 0.0, 1.0))
    assert len(plate.faces) == 1
    # The centre of a 3 x 2 rectangle is 1.0 from its nearest edge, which is the most interior
    # point there is -- the search maximises exactly that.
    assert plate.faces[0].point == pytest.approx((1.0, 1.0, 0.0))
    assert plate.faces[0].clearance == pytest.approx(1.0)


def test_a_stiffener_splits_its_plate_into_two_faces_each_with_its_own_point():
    """The imprint is in the body adapy authors, not in CAE. Measured: CAE imports it as 2 faces /
    7 edges / 6 vertices, the stiffener line ONE edge bounding both, areas 3.0 and 3.0."""
    body = plate_body(one_part(a_deck(), ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355")))

    plate = body.plates[0]
    assert [f.sat_face_name for f in plate.faces] == ["FACE00000001", "FACE00000002"]
    # One point per face, on opposite sides of the stiffener line at y = 1.
    ys = sorted(f.point[1] for f in plate.faces)
    assert ys[0] < 1.0 < ys[1]
    assert plate.area == pytest.approx(6.0), "adapy's own area is the whole plate's, not a face's"


def test_the_face_point_of_a_tilted_plate_is_on_its_plane():
    normal = (0.0, -math.sin(math.radians(30)), math.cos(math.radians(30)))
    plate = ada.Plate(
        "tilt", [(0, 0), (2, 0), (2, 1.5), (0, 1.5)], 0.02, mat="S355", origin=(1, 2, 3), xdir=(1, 0, 0), normal=normal
    )
    body = plate_body(one_part(plate))

    planned = body.plates[0]
    assert planned.area == pytest.approx(3.0)
    assert planned.normal == pytest.approx(normal)
    point = np.asarray(planned.faces[0].point, dtype=float)
    # On the plane the plate declares: (p - origin) . normal == 0.
    # 1e-08 and not 1e-12: adapy rounds a Direction to 7 decimals, so the plate's own declared normal
    # is not exactly the unit vector this test passed in. Measured residual 1.4e-09.
    assert float(np.dot(point - np.asarray([1.0, 2.0, 3.0]), np.asarray(normal))) == pytest.approx(0.0, abs=1e-08)


def test_a_declared_normal_of_minus_z_is_carried_rather_than_flattened():
    """Measured: the same outline declared -z imports with face.getNormal() == (0, 0, -1) and
    isNormalFlipped() False, so the sense adapy writes survives and nothing may flip it."""
    plate = ada.Plate("flip", [(0, 0), (3, 0), (3, 2), (0, 2)], 0.012, mat="S355", normal=(0, 0, -1), xdir=(1, 0, 0))

    assert plate_body(one_part(plate)).plates[0].normal == pytest.approx((0.0, 0.0, -1.0))


def test_the_interior_point_of_a_concave_outline_is_not_its_centroid():
    """The measurement that decided the search. The C-shaped outline below has its area centroid at
    (1.357, 1.5), which falls in the notch -- and ``findAt`` there returned 0 faces with only a
    warning, so a writer that used the centroid would have built a set with no faces in it and said
    nothing."""
    points = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3)]
    plate = ada.Plate("cshape", points, 0.015, mat="S355")
    centroid = np.asarray(plate.get_cog(), dtype=float)
    assert centroid[0] == pytest.approx(1.3571428571428572)

    located = np.asarray(plate_body(one_part(plate)).plates[0].faces[0].point, dtype=float)

    assert float(np.linalg.norm(located - centroid)) > 0.5
    # inside the left-hand limb, i.e. clear of the notch x in [1, 3], y in [1, 2]
    assert not (1.0 < located[0] < 3.0 and 1.0 < located[1] < 2.0)


def test_the_interior_point_search_maximises_clearance_rather_than_merely_being_inside():
    """A point just inside is not good enough: ``findAt`` on the edge two faces share returns one of
    them arbitrarily (measured), so the point has to be as far from every boundary as it can be."""
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]

    point, clearance = interior_point_2d([square])

    assert point == pytest.approx((2.0, 2.0))
    assert clearance == pytest.approx(2.0)


def test_a_hole_is_excluded_from_the_interior_point_search():
    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    hole = [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0)]

    point, clearance = interior_point_2d([outer, hole])

    assert not (3.0 < point[0] < 7.0 and 3.0 < point[1] < 7.0)
    assert clearance > 0.0


def test_two_plates_meeting_at_an_edge_each_keep_their_own_face_and_thickness():
    deck = a_deck("deck", t=0.012)
    bulkhead = ada.Plate(
        "bulkhead",
        [(0, 0), (3, 0), (3, 1.5), (0, 1.5)],
        0.010,
        mat="S355",
        origin=(0, 0, 0),
        xdir=(1, 0, 0),
        normal=(0, -1, 0),
    )

    body = plate_body(one_part(deck, bulkhead))

    assert [p.plate_name for p in body.plates] == ["bulkhead", "deck"], "sorted, for a stable emission"
    assert {p.plate_name: p.thickness for p in body.plates} == {"deck": 0.012, "bulkhead": 0.010}
    assert {p.plate_name: len(p.faces) for p in body.plates} == {"deck": 1, "bulkhead": 1}
    names = [f.sat_face_name for p in body.plates for f in p.faces]
    assert len(set(names)) == 2, "one face each, and no plate claiming the other's"


def test_the_body_reports_its_own_vertices_so_a_support_can_sit_on_a_plate_corner():
    body = plate_body(one_part(a_deck()))

    assert sorted(body.vertices) == [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (3.0, 0.0, 0.0), (3.0, 2.0, 0.0)]


# --------------------------------------------------------------------------- refusals at plan time


def test_a_part_whose_subpart_also_owns_plates_is_refused_by_name():
    inner = ada.Part("inner") / ada.Plate("p_inner", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, mat="S355")
    outer = ada.Part("outer") / (a_deck("p_outer"), inner)
    ada.Assembly("A") / outer

    with pytest.raises(PlateNotSupported, match="would be authored into two CAE parts"):
        plate_body(outer)


def test_adapys_sat_writer_does_not_put_a_curved_beam_axis_on_a_flat_plate():
    """Measured, and it is why the refusal below is a backstop rather than a live path.

    ``ada.cadit.sat.write.writer._beam_axes`` hands the imprint each beam's ``axis_global()`` -- two
    points, the chord -- so an arc member's curve is never imprinted onto a flat plate. Such a member
    is therefore drawn as a wire, and if its chord happens to lie in the plate it is absorbed into the
    plate's boundary, which the emitted script catches in the kernel: an ordinary edge shared with a
    face produces no beam elements at all.
    """
    from ada.api.beams import BeamRevolve
    from ada.api.curves import CurveRevolve

    curve = CurveRevolve((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), radius=2.0, rot_axis=(0.0, 0.0, 1.0))
    arc = BeamRevolve("arc_stf", curve, "HP200x10", mat="S355")
    plate = ada.Plate("deck", [(-1, -1), (3, -1), (3, 3), (-1, 3)], 0.012, mat="S355")

    body = plate_body(one_part(plate, arc))

    assert stringer_members(body) == {}
    assert len(body.plates[0].faces) == 1, "and the plate is not split by it either"


def test_a_curved_member_lying_on_a_plate_is_refused_because_its_sub_edge_count_is_unknowable(monkeypatch):
    """The backstop for the day the SAT writer learns to imprint an arc.

    A straight member on a plate is a stringer on edges whose count adapy can state. A *curved* one
    would be a stringer on a spline edge, and where along a spline CAE puts an imprinted vertex is the
    spline's own parameterisation's business -- measured, a straight wire landing on the midpoint of a
    spline wire took a part from 2 edges / 3 vertices to 3 / 4. So the sub-edge count this writer
    asserts for every member would not be knowable, and the member is refused rather than asserted
    loosely.

    The condition is injected because the test above measures that today's SAT writer cannot produce
    it. A guard whose branch has never been executed is a guard nobody has seen work.
    """
    from ada.api.beams import BeamRevolve
    from ada.api.curves import CurveRevolve
    from ada.cadit.cae import writer as writer_module

    curve = CurveRevolve((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), radius=2.0, rot_axis=(0.0, 0.0, 1.0))
    arc = BeamRevolve("arc_stf", curve, "HP200x10", mat="S355")
    plate = ada.Plate("deck", [(-1, -1), (3, -1), (3, 3), (-1, 3)], 0.012, mat="S355")
    part = one_part(plate, arc)
    monkeypatch.setattr(writer_module, "stringer_members", lambda body: {"arc_stf": ["EDGE00000001"]})

    with pytest.raises(CaeWriteError, match="whose axis lies on a plate"):
        build_plan(part)


def test_two_plates_at_the_same_place_are_refused_rather_than_sharing_one_face():
    """The imprint fuses coincident outlines into one face, and one face cannot carry two shell
    sections -- CAE would keep whichever assignment came last, silently."""
    part = one_part(a_deck("d1", t=0.012), a_deck("d2", t=0.020))

    with pytest.raises(PlateNotSupported, match="both claim SAT face"):
        plate_body(part)


# ------------------------------------------------------------------------ stringers, not refusals


def test_a_beam_lying_on_a_plate_is_a_stringer_and_a_beam_clear_of_it_is_a_wire():
    """The measurement this whole split exists for: an ordinary edge shared with a shell face
    produces ``{'S4R': 96}`` and no B31 at all, and ``Part.Stringer`` on the same edge of the same
    part produces ``{'S4R': 96, 'B31': 12}`` with all 13 nodes on the line shared."""
    part = one_part(
        a_deck(),
        ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355"),  # on the plate's interior
        ada.Beam("edge_stf", (0, 0, 0), (3, 0, 0), "FB100x10", "S355"),  # along its boundary
        ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"),  # clear of it
        ada.Beam("column", (0, 1, -0.4), (0, 1, 0), "IPE300", "S355"),  # touching it at a point
    )

    plan = build_plan(part)

    kinds = {m.beam_name: m.is_stringer for m in plan.parts[0].members}
    assert kinds == {"stf1": True, "edge_stf": True, "girder": False, "column": False}


def test_a_member_merely_touching_a_plate_is_not_made_a_stringer():
    """adapy authors a beam axis with no plate under it as a *wire* body, so such an edge still gets
    a name in ``edge_map``: the test has to be "does the edge bound a face", not "is it named"."""
    part = one_part(
        a_deck(),
        ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"),
        ada.Beam("column", (0, 1, -0.4), (0, 1, 0), "IPE300", "S355"),
    )

    assert stringer_members(plate_body(part)) == {}


def test_a_stringer_draws_no_wire_and_gets_a_stringer_region(tmp_path):
    part = one_part(a_deck(), ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355"))

    _, text = emit(part, tmp_path)

    assert "part_0.Stringer(name='stf1', edges=edges_0_0)" in text
    assert "part_0.Set(name='stf1', stringerEdges=(('stf1', edges_0_0),))" in text
    assert "WirePolyLine" not in text, "its edge is already in the imported body"
    assert "lies on a plate: its edge is already in the imported body" in text
    assert "_member_edges('Deck', 'stf1', edges_0_0, on_a_plate=True)" in text


def test_the_expected_topology_counts_a_stringers_edges_from_the_body_it_came_from(tmp_path):
    """A wire member starts as one edge; a stringer starts from however many the imprint split its
    axis into. Both then gain one per other member's endpoint landing inside them."""
    part = one_part(
        a_deck(),
        ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355"),
        ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"),
    )

    _, text = emit(part, tmp_path)

    start = text.index("EXPECTED_TOPOLOGY = {")
    block = text[start : text.index("\n}\n", start)]
    assert "'stringers': ['stf1']," in block
    assert "'wire_edges': 1," in block, "only the girder is drawn as a wire"
    assert "'faces': 2," in block


def test_the_whole_mixed_model_passes_the_graph_pass(tmp_path):
    part = one_part(
        a_deck(),
        ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355"),
        ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"),
        ada.Beam("column", (0, 1, -0.4), (0, 1, 0), "IPE300", "S355"),
    )

    _, text = emit(part, tmp_path, mesh_size=0.25)

    graph = check_emitted_script(text, name="mixed.py")
    assert len(graph.by_method("PartFromGeometryFile")) == 1
    assert len(graph.by_method("Part")) == 0
    assert len(graph.by_method("Stringer")) == 1
    assert len(graph.by_method("HomogeneousShellSection")) == 1
    assert len(graph.by_method("WirePolyLine")) == 2


# ------------------------------------------------------------------------------- shell sections


def test_one_shell_section_per_thickness_and_material_pair(tmp_path):
    # The thicknesses are given in an order whose *insertion* order is NOT their sorted order: the
    # thickest plate comes first. That is deliberate -- the already-sorted fixture is a trap this
    # project has been caught by three times, and with them the other way round a writer that emitted
    # its sections in insertion order would pass this test.
    part = one_part(
        a_deck("d1", t=0.020, y=0.0),
        a_deck("d2", t=0.012, y=3.0),
        a_deck("d3", t=0.012, y=6.0, mat="S420"),
        a_deck("d4", t=0.012, y=9.0),
    )

    plan = build_plan(part)

    assert [s.cae_section_name for s in plan.shell_sections] == [
        "sh_0p012_S355",
        "sh_0p012_S420",
        "sh_0p02_S355",
    ]
    assigned = {p.plate_name: p.cae_section_name for p in plan.parts[0].plates}
    assert assigned == {
        "d1": "sh_0p02_S355",
        "d2": "sh_0p012_S355",
        "d3": "sh_0p012_S420",
        "d4": "sh_0p012_S355",
    }


def test_two_thicknesses_that_round_to_one_name_are_refused_rather_than_silently_merged():
    """CAE replaces an object whose name is reused rather than refusing it (measured), so one of the
    two sections would simply vanish and half the plates would be the wrong gauge."""
    from ada.cadit.cae.names import CaeNameError

    part = one_part(a_deck("d1", t=0.0120000001, y=0.0), a_deck("d2", t=0.0120000002, y=3.0))

    with pytest.raises(CaeNameError, match="both name the shell section"):
        build_plan(part)


def test_every_plate_gets_a_set_of_its_own_name(tmp_path):
    part = one_part(
        a_deck("deck"),
        ada.Plate(
            "bulkhead",
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            0.01,
            mat="S355",
            origin=(0, 0, 0),
            xdir=(1, 0, 0),
            normal=(0, -1, 0),
        ),
    )

    _, text = emit(part, tmp_path)

    assert text.count("Set(name='deck'") == 1
    assert text.count("Set(name='bulkhead'") == 1


def test_a_plate_with_a_dot_in_its_name_is_sanitised(tmp_path):
    """Measured: CAE rejects a dot in a name outright with 'invalid name', while spaces and dashes
    are fine. A GeniE plate name carries dots routinely."""
    written, text = emit(one_part(a_deck("PL.1")), tmp_path)

    assert "Set(name='PL_1'" in text
    assert any(path.name.endswith("name_map.json") for path in written)


# --------------------------------------------------------------------------------- the emission


def test_the_sat_body_is_written_beside_the_script_and_imported_from_there(tmp_path):
    written, text = emit(one_part(a_deck()), tmp_path, name="model.py")

    assert [path.name for path in written] == ["model.py", "model_Deck.sat"]
    assert (tmp_path / "model_Deck.sat").read_text(encoding="utf-8").startswith("2000 0 1 0")
    assert "mdb.openAcis(_beside_script('model_Deck.sat'), scaleFromFile=OFF)" in text
    assert "model.PartFromGeometryFile(name='Deck'" in text


def test_a_part_name_a_filename_cannot_hold_is_sanitised_for_the_sidecar(tmp_path):
    """A CAE part name legally holds a slash -- measured, ``PL_1/2`` was accepted -- and a slash is a
    directory separator, not a filename."""
    part = ada.Part("Deck/2")
    part / a_deck()
    ada.Assembly("A") / part

    written, text = emit(part, tmp_path, name="model.py")

    assert [path.name for path in written] == ["model.py", "model_Deck_2.sat"]
    assert "mdb.openAcis(_beside_script('model_Deck_2.sat'), scaleFromFile=OFF)" in text


def test_two_parts_whose_sat_names_would_collide_are_refused():
    first = ada.Part("Deck/2")
    first / a_deck("d1")
    second = ada.Part("Deck 2")
    second / a_deck("d2")
    assembly = ada.Assembly("A")
    assembly.add_part(first)
    assembly.add_part(second)

    with pytest.raises(CaeWriteError, match="would both write their ACIS body to"):
        build_plan(assembly, sat_prefix="m")


def test_the_plates_table_carries_the_thickness_area_normal_and_one_point_per_face(tmp_path):
    part = one_part(a_deck(), ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355"))

    _, text = emit(part, tmp_path)

    namespace: dict = {}
    start = text.index("PLATES = {")
    exec(text[start : text.index("\n}\n", start) + 3], namespace)  # noqa: S102 - a literal dict
    row = namespace["PLATES"]["Deck"][0]
    assert row["name"] == "deck"
    assert row["t"] == 0.012
    assert row["area"] == 6.0
    assert row["normal"] == (0.0, 0.0, 1.0)
    assert row["kind"] == "Plate"
    assert len(row["points"]) == 2, "one point per face the stiffener split it into"
    assert len(row["sat_face"]) == 2


def test_the_shell_section_the_script_creates_carries_the_plates_own_thickness(tmp_path):
    _, text = emit(one_part(a_deck(t=0.0185)), tmp_path)

    assert "model.HomogeneousShellSection(name='sh_0p0185_S355', material='S355', thickness=0.0185)" in text
    assert "sectionName='sh_0p0185_S355', offsetType=MIDDLE_SURFACE" in text
    assert "thicknessAssignment=FROM_SECTION" in text


def test_the_area_tolerance_the_script_states_is_the_one_the_writer_holds(tmp_path):
    _, text = emit(one_part(a_deck()), tmp_path)

    assert "PLATE_AREA_REL_TOL = {0!r}".format(PLATE_AREA_REL_TOL).replace("'", "") in text or (
        "PLATE_AREA_REL_TOL = 1e-06" in text
    )


def test_plates_false_leaves_them_untranslated_and_emits_no_body(tmp_path):
    part = one_part(a_deck(), ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"))

    written, text = emit(part, tmp_path, plates=False)

    assert [path.name for path in written] == ["out.py"]
    assert "NOT translated by this writer" in text
    assert "Plate 'deck'" in text
    assert "mdb.openAcis(" not in text
    assert "PLATES = {" not in text
    assert "WirePolyLine" in text, "the beams are still built"


def test_the_emission_is_a_function_of_the_model_and_not_of_insertion_order(tmp_path):
    def build(order):
        part = ada.Part("Deck")
        offsets = {"alpha": 0.0, "zulu": 3.0}
        for name in order:
            part.add_plate(a_deck(name, t=0.012, y=offsets[name]))
        ada.Assembly("A") / part
        return part

    _, forwards = emit(build(["alpha", "zulu"]), tmp_path, name="f.py")
    _, backwards = emit(build(["zulu", "alpha"]), tmp_path, name="b.py")

    assert forwards.index("'alpha'") < forwards.index("'zulu'")
    normalised = backwards.replace("b.cae", "f.cae").replace("b.cae_build", "f.cae_build")
    normalised = normalised.replace("b_Deck.sat", "f_Deck.sat").replace("b.name_map", "f.name_map")
    normalised = normalised.replace("b.cae_displacements", "f.cae_displacements")
    assert normalised == forwards


# --------------------------------------------------------------------- the analysis sees a plate


def test_a_plate_corner_is_a_vertex_a_support_can_be_resolved_against():
    """Before plates there was nothing there to find, and such a record was refused for landing
    nowhere. A plate corner is a real geometric vertex, so it is in the index now."""
    from ada.cadit.cae.analysis import vertex_index

    plan = build_plan(one_part(a_deck()))

    index = vertex_index(plan.parts, 1e-04)
    positions = sorted(point for point, _ in index)
    assert (0.0, 0.0, 0.0) in positions
    assert (3.0, 2.0, 0.0) in positions


def test_the_bounding_box_the_script_checks_includes_the_plates(tmp_path):
    part = one_part(a_deck(), ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"))

    _, text = emit(part, tmp_path)

    assert "'Deck': ((0.0, 0.0, -0.4), (3.0, 2.0, 0.0))," in text


# ------------------------------------------------------------------------------- shell elements


def test_a_shell_element_code_outside_the_measured_set_is_refused():
    from ada.cadit.cae.analysis import CARRIED_SHELL_ELEMENT_TYPES

    assert CARRIED_SHELL_ELEMENT_TYPES == ("S4R", "S4", "S8R")
    with pytest.raises(CaeWriteError, match="shell_element_type='S3' is refused"):
        build_plan(one_part(a_deck()), shell_element_type="S3")


def test_the_mesh_block_types_the_faces_and_the_edges_separately(tmp_path):
    """CAE takes one ElemType per region shape, so a mixed part is typed twice."""
    part = one_part(a_deck(), ada.Beam("girder", (0, 1, -0.4), (3, 1, -0.4), "IPE300", "S355"))

    _, text = emit(part, tmp_path, mesh_size=0.25, element_type="B33", shell_element_type="S8R")

    assert "_mesh_part('Deck', part_0, 0.25, B33, 'B33', S8R, 'S8R')" in text


# ------------------------------------------------------------------------------- curved plates


def _curved_plate_from_sat(path, name="curved_plate", t=0.02) -> PlateCurved:
    """A real Genie ``curved_shell``: a rational NURBS patch with a 10-edge boundary whose edges
    carry both their parameter range and their pcurve. A synthetic patch without those is skipped by
    adapy's own SAT writer, so it would say nothing about this path."""
    from ada.cadit.sat.store import SatReaderFactory

    _, advanced_face = next(iter(SatReaderFactory(str(path)).iter_advanced_faces()))
    return PlateCurved(name, Geometry(1, advanced_face, None), t=t, mat="S355")


@pytest.mark.pyocc  # a curved plate is located by a point the pythonocc kernel finds on its
# spline face; without that kernel the writer refuses it by name, which is its own test above
def test_a_real_curved_plate_is_translated_with_the_area_the_cad_backend_measures(cae_files):
    """Measured against CAE on this very body: the point below resolved to exactly one face and the
    areas agreed to 2.5e-08 (adapy 6.915204361623685, CAE 6.91520453146419)."""
    plate = _curved_plate_from_sat(cae_files.parents[4] / "files/sat_files/curved_plate.sat")

    body = plate_body(one_part(plate, name="Hull"))

    planned = body.plates[0]
    assert planned.kind == "PlateCurved"
    assert planned.thickness == 0.02
    assert len(planned.faces) == 1
    assert planned.area == pytest.approx(6.915204361623685, rel=1e-09)
    assert planned.faces[0].point == pytest.approx(
        (23.503447064906872, 22.211667940320915, 11.042436011849915), rel=1e-09
    )
    assert planned.normal == pytest.approx((0.18602819108818064, 0.9783424781269513, -0.09077173355662654), rel=1e-09)


@pytest.mark.pyocc  # a curved plate is located by a point the pythonocc kernel finds on its
# spline face; without that kernel the writer refuses it by name, which is its own test above
def test_a_curved_plates_area_is_the_bare_face_and_not_the_thickened_shell(cae_files):
    """The trap: with ``Config().geom_thicken_curved_shells`` on -- the default -- ``solid_occ()``
    returns a thickness-``t`` ClosedShell whose *surface* area is about twice the face's. Measured on
    this body: 14.042782242424936 against the face's own 6.915204361623685, and CAE reported
    6.91520453146419. A check written against ``solid_occ()`` would have failed by a factor of two
    and looked like a units problem."""
    from ada.cad import active_backend

    plate = _curved_plate_from_sat(cae_files.parents[4] / "files/sat_files/curved_plate.sat")
    backend = active_backend()

    thickened = backend.area(plate.solid_occ())
    planned = plate_body(one_part(plate, name="Hull")).plates[0]

    assert thickened == pytest.approx(14.042782242424936, rel=1e-09)
    assert planned.area == pytest.approx(6.915204361623685, rel=1e-09)
    assert thickened / planned.area > 1.9


@pytest.mark.pyocc  # a curved plate is located by a point the pythonocc kernel finds on its
# spline face; without that kernel the writer refuses it by name, which is its own test above
def test_a_curved_plate_emits_a_shell_section_and_a_located_face(cae_files, tmp_path):
    plate = _curved_plate_from_sat(cae_files.parents[4] / "files/sat_files/curved_plate.sat")

    _, text = emit(one_part(plate, name="Hull"), tmp_path)

    check_emitted_script(text, name="curved.py")
    assert "model.HomogeneousShellSection(name='sh_0p02_S355', material='S355', thickness=0.02)" in text
    assert "'kind': 'PlateCurved'," in text
    assert "_plate_faces('Hull', 'curved_plate', part_0, PLATES['Hull'][0]['points'])" in text


def test_a_face_too_thin_to_be_located_is_refused_rather_than_located_wrongly():
    """The point has to be *usably* interior, not merely inside.

    ``findAt`` at a point on the edge two faces share returns one of them arbitrarily (measured), so a
    face whose best point is within rounding of its own boundary cannot be told from its neighbour.
    A sliver 3 m long and 3 micron wide has a best clearance of 1.5e-06, which is 1e-06 of its own
    3 m diameter -- exactly the floor.
    """
    sliver = ada.Plate("sliver", [(0, 0), (3, 0), (3, 3e-06), (0, 3e-06)], 0.012, mat="S355")

    with pytest.raises(PlateNotSupported, match="clear of its boundary"):
        plate_body(one_part(sliver))


def test_the_minimum_clearance_is_a_fraction_of_the_faces_own_size_and_not_an_absolute():
    """An absolute floor would turn a change of units into a change of behaviour, which is the class
    of defect this whole writer exists to refuse. The same outline in millimetres is the same plate."""
    from ada.cadit.cae.plates import MIN_INTERIOR_CLEARANCE_FRACTION

    assert MIN_INTERIOR_CLEARANCE_FRACTION == 1e-06
    small = ada.Plate("small", [(0, 0), (0.003, 0), (0.003, 0.002), (0, 0.002)], 1.2e-05, mat="S355")

    planned = plate_body(one_part(small)).plates[0]

    assert planned.faces[0].clearance == pytest.approx(0.001)


def test_a_face_that_no_plate_claims_is_refused(monkeypatch):
    """Every face of the imported part has to end with a shell section, and a face nobody owns is a
    face the emitted model would carry with no thickness and no material -- which Abaqus then refuses
    to mesh (``contains invalid geometry`` is the nearest it gets to saying so).

    The condition is injected, because it is a failure of the SAT writer's own ``face_map`` and not
    something a model can be built to provoke: it would mean adapy authored a face and then did not
    record which plate it came from. A guard whose branch has never been executed is a guard nobody
    has seen work.
    """
    from ada.cadit.sat.write import writer as sat_writer_module

    original = sat_writer_module.part_to_sat_writer

    def forgetful(part, imprint=True):
        writer = original(part, imprint=imprint)
        for guid in list(writer.face_map):
            writer.face_map[guid] = writer.face_map[guid][:-1]
        return writer

    monkeypatch.setattr(sat_writer_module, "part_to_sat_writer", forgetful)
    part = one_part(a_deck(), ada.Beam("stf1", (0, 1, 0), (3, 1, 0), "HP200x10", "S355"))

    with pytest.raises(PlateNotSupported, match="no plate claims"):
        plate_body(part)


# ------------------------------------------------------------------- a pressure on a plate's faces


def a_strip_with_a_pressure(*, partial=False, nodes_instead=False, two_plates=False) -> ada.Assembly:
    """A meshed strip carrying one ``Load`` of type ``pressure`` over its own element set.

    ``ada.fem.loads.LoadTypes.all`` does not list ``pressure`` -- the constructor sets the type
    without going through the validating setter -- so nothing in adapy builds one of these today.
    That is why the model is assembled here rather than taken from a fixture, and it is also why the
    translation is narrow: what it has to carry is the record's own shape, not a corpus.
    """
    from ada.fem import FemSet, Load, StepImplicitStatic

    plates = [ada.Plate("deck", [(0, 0), (4, 0), (4, 0.5), (0, 0.5)], 0.010, mat="S355")]
    if two_plates:
        plates.append(ada.Plate("other", [(0, 2), (4, 2), (4, 2.5), (0, 2.5)], 0.010, mat="S355"))
    part = ada.Part("Strip") / plates
    assembly = ada.Assembly("A") / part
    fem = part.to_fem_obj(0.25, "shell")
    part.fem = fem
    if nodes_instead:
        acting_on = fem.add_set(FemSet("some_nodes", list(fem.nodes)[:4], FemSet.TYPES.NSET))
    elif partial:
        whole = list(fem.elsets["eldeck_sh"].members)
        acting_on = fem.add_set(FemSet("half", whole[: len(whole) // 2], FemSet.TYPES.ELSET))
    elif two_plates:
        acting_on = fem.add_set(
            FemSet(
                "both",
                list(fem.elsets["eldeck_sh"].members) + list(fem.elsets["elother_sh"].members),
                FemSet.TYPES.ELSET,
            )
        )
    else:
        acting_on = fem.elsets["eldeck_sh"]
    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(Load("q", "pressure", 1000.0, fem_set=acting_on))
    return assembly


def test_a_pressure_on_a_plate_becomes_a_surface_and_a_pressure(tmp_path):
    assembly = a_strip_with_a_pressure()

    plan = build_plan(assembly)

    assert len(plan.analysis.pressures) == 1
    pressure = plan.analysis.pressures[0]
    assert pressure.cae_name == "q"
    assert pressure.cae_surface_name == "q_surf"
    assert pressure.plate_set_name == "deck"
    assert pressure.magnitude == 1000.0
    assert pressure.step == "static"
    assert pressure.element_count == 64
    assert len(pressure.points) == 1, "one point per face of the plate it acts on"

    _, text = emit(assembly, tmp_path)
    assert "_pressure_surface(assembly, 'q_surf', 'Strip-1', ((" in text
    assert "model.Pressure(name='q', createStepName='static'," in text
    assert "region=assembly.surfaces['q_surf'], magnitude=1000.0)" in text
    assert "'surfaces': ['q_surf']," in text
    assert "'loads': ['q']," in text


def test_the_emitted_pressure_states_which_way_a_positive_magnitude_pushes(tmp_path):
    """Abaqus' own convention, and it is not guessable from the call: a positive pressure acts INTO
    the surface, against ``side1``'s outward normal -- which is the plate's declared normal, measured
    to survive the ACIS import unflipped."""
    _, text = emit(a_strip_with_a_pressure(), tmp_path)

    assert "side1Faces, and that fixes the sign" in text
    assert "whose normal is +z pushes it in -z" in text


def test_a_pressure_on_a_node_set_is_refused():
    with pytest.raises(CaeWriteError, match="is a Node and not an element"):
        build_plan(a_strip_with_a_pressure(nodes_instead=True))


def test_a_pressure_over_part_of_a_plate_is_refused_rather_than_spread_over_all_of_it():
    """A CAE Surface is made of WHOLE faces, so this would become more load than the model describes,
    applied where it does not."""
    with pytest.raises(CaeWriteError, match="of the 64 elements the plate 'deck' was meshed into"):
        build_plan(a_strip_with_a_pressure(partial=True))


def test_a_pressure_spanning_two_plates_is_refused():
    with pytest.raises(CaeWriteError, match="elements come from 2 different plates"):
        build_plan(a_strip_with_a_pressure(two_plates=True))


def test_a_pressure_is_refused_when_the_plate_it_acts_on_is_not_emitted():
    """A pressure onto nothing is worse than a refusal. With the plate present but a *second* plate
    carrying the load, the record names a plate that is not in the emitted model."""
    from ada.fem import FemSet, Load, StepImplicitStatic

    kept = ada.Plate("deck", [(0, 0), (4, 0), (4, 0.5), (0, 0.5)], 0.010, mat="S355")
    part = ada.Part("Strip") / kept
    assembly = ada.Assembly("A") / part
    fem = part.to_fem_obj(0.25, "shell")
    part.fem = fem
    # A set of the deck's elements, but the plate they were meshed from is then taken out of the part
    # -- which is what a pressure naming a plate the writer did not emit looks like from here.
    elset = fem.elsets["eldeck_sh"]
    step = assembly.fem.add_step(StepImplicitStatic("s", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(Load("q", "pressure", 1000.0, fem_set=elset))
    # ``other`` is never added to the part, so the writer emits no face for it.
    other = ada.Plate("other", [(0, 2), (4, 2), (4, 2.5), (0, 2.5)], 0.010, mat="S355")
    for element in elset.members:
        element.refs[:] = [other if isinstance(ref, ada.Plate) else ref for ref in element.refs]
    assert isinstance(elset, FemSet)

    with pytest.raises(CaeWriteError, match=r"the plates it emitted are \['deck'\]"):
        build_plan(assembly)


def test_pressure_is_no_longer_in_the_refused_load_types():
    """The refusal message it used to carry said the emitted model 'has no face for it to act on',
    which stopped being true when plates arrived. A message that has become untrue is worse than no
    message, so the type is carried instead."""
    from ada.cadit.cae.analysis import CARRIED_LOAD_TYPES, REFUSED_LOAD_TYPES

    assert "pressure" in CARRIED_LOAD_TYPES
    assert "pressure" not in REFUSED_LOAD_TYPES

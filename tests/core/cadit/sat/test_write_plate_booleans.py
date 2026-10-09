"""A plate's holes -- its booleans -- reach the SAT body as inner loops (Krande/adapy#410).

The SAT writer authored a plate's outline alone, one loop per face and ``Loop.next_loop`` never
set, so a hole written to GeniE came back as solid plate with nothing said. The CAD backend now
makes the cut and what it leaves is authored as advanced faces, holes and all; what cannot be
stated exactly is written whole and said by name.
"""

from __future__ import annotations

import logging
import re

import numpy as np
import pytest

import ada
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.writer import part_to_sat_writer
from ada.geom import curves as geo_cu

from ..face_area import plate_area


@pytest.fixture
def ada_log():
    """adapy's logger does not propagate, so caplog cannot see it; collect its records directly."""
    records: list[logging.LogRecord] = []
    handler = logging.Handler(level=logging.WARNING)
    handler.emit = records.append
    logger = logging.getLogger("ada")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def _plate(name="pl", origin=(0, 0, 0)) -> ada.Plate:
    return ada.Plate(name, [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01, origin=origin)


def _loops_per_face(sw) -> list[int]:
    out = []
    for face in sw.get_entities_by_type(se.Face):
        n, loop = 0, face.loop
        while loop is not None:
            n, loop = n + 1, loop.next_loop
        out.append(n)
    return out


@pytest.mark.parametrize("imprint", [True, False])
def test_the_issue_410_hole_is_a_second_loop(imprint):
    """The issue's reproduction: one loop on either path before."""
    pl = _plate()
    pl.add_boolean(ada.PrimBox("hole", (1.5, 1.0, -0.5), (2.5, 2.0, 0.5)))
    a = ada.Assembly("A") / (ada.Part("P") / pl)
    sw = part_to_sat_writer(a, imprint=imprint)
    assert _loops_per_face(sw) == [2]
    assert len(re.findall(r"\bloop\b", sw.to_str())) == 2


@pytest.mark.parametrize("body", ["text", "binary"])
def test_holes_survive_a_genie_workspace(tmp_path, body):
    """A square hole and a round one: written, read back, each plate is its outline less its hole."""
    square, round_ = _plate("square"), _plate("round", origin=(0, 0, 5))
    square.add_boolean(ada.PrimBox("hole", (1.5, 1.0, -0.5), (2.5, 2.0, 0.5)))
    round_.add_boolean(ada.PrimCyl("hole", (2, 1.5, 4.5), (2, 1.5, 5.5), 0.4))
    a = ada.Assembly("A") / (ada.Part("P") / [square, round_])

    back = ada.from_gnx(a.to_gnx(tmp_path / "holes.gnx", binary_acis=body == "binary"))
    plates = {p.name: p for p in back.get_all_physical_objects(by_type=(ada.Plate, ada.PlateCurved))}
    assert sorted(plates) == ["round", "square"]
    for p in plates.values():
        assert type(p) is ada.PlateCurved and len(p.geom.geometry.bounds) == 2
    assert plate_area(plates["square"]) == pytest.approx(11.0, rel=1e-12)
    assert plate_area(plates["round"]) == pytest.approx(12.0 - np.pi * 0.16, rel=1e-12)


def test_the_hole_follows_the_part_placement():
    """The cut is made in the plate's frame and moved to global, as outline_global moves the outline."""
    pl = _plate()
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    p = ada.Part("P", placement=ada.Placement(origin=(10, 20, 30)))
    a = ada.Assembly("A") / (p / pl)
    sw = part_to_sat_writer(a)
    (face,) = sw.get_entities_by_type(se.Face)
    (circle_edge,) = [e for e in sw.get_entities_by_type(se.Edge) if isinstance(e.straight_curve, se.EllipseCurve)]
    assert np.allclose(np.asarray(circle_edge.start_pt, dtype=float), (12.4, 21.5, 30.0), atol=1e-12)
    outline, _ = pl.outline_global()
    assert np.allclose(np.min(outline, axis=0), (10, 20, 30)) and face.loop.next_loop is not None


def test_a_plate_cut_in_two_is_two_faces_of_one_plate():
    pl = _plate()
    pl.add_boolean(ada.PrimBox("slot", (1.5, -1.0, -0.5), (2.5, 4.0, 0.5)))
    a = ada.Assembly("A") / (ada.Part("P") / pl)
    sw = part_to_sat_writer(a)
    assert len(sw.face_map[pl.guid]) == 2
    assert _loops_per_face(sw) == [1, 1]


def test_an_edge_the_writer_cannot_state_is_said_by_name(ada_log):
    """A cylinder through the plate at a slant cuts an ellipse: the plate goes out whole, and says so."""
    pl = _plate()
    pl.add_boolean(ada.PrimCyl("slant", (1.5, 1.5, -0.5), (2.5, 1.5, 0.5), 0.3))
    a = ada.Assembly("A") / (ada.Part("P") / pl)
    sw = part_to_sat_writer(a)
    assert _loops_per_face(sw) == [1]
    (warning,) = [r.getMessage() for r in ada_log if "without its" in r.getMessage()]
    assert "'pl'" in warning and "1 boolean(s)" in warning


@pytest.mark.parametrize("normal", [(0, 0, 1), (0, 0, -1)])
def test_the_cut_face_faces_the_way_the_plate_does(normal):
    """Plane normal through the face sense: the plate's own normal, either way up."""
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01, normal=normal)
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    a = ada.Assembly("A") / (ada.Part("P") / pl)
    (face,) = part_to_sat_writer(a).get_entities_by_type(se.Face)
    got = np.asarray(face.surface.normal, dtype=float) * (1 if face.sense == "forward" else -1)
    assert np.allclose(got, np.asarray(pl.outline_global()[1], dtype=float))


def test_a_misread_winding_is_refused_not_written(monkeypatch):
    """The area check is the backstop: a hole wound like its outline measures outline plus hole."""
    import ada.cadit.sat.write.plate_booleans as pb

    monkeypatch.setattr(pb, "_reversed", lambda bound: bound)  # loops left as the cut faced them
    pl = _plate()
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    with pytest.raises(pb.PlateBooleanNotAuthored, match="measures"):
        pb.plate_faces_after_booleans(pl)


@pytest.fixture
def use_backend(monkeypatch):
    """Make the named backend the active one for the rest of the test."""
    import ada.cad as cad

    def use(name: str):
        monkeypatch.setenv("ADAPY_CAD_BACKEND", name)
        cad.reset_active_backend()

    yield use
    cad.reset_active_backend()


def test_a_hole_through_the_outline_is_a_notch_in_it(backend, use_backend):
    """The outline runs the hole's arc backwards: built from t_start down to t_end, the plate less half the disc.

    Refused before on both kernels (the rebuilt face measured 12.754 against the cut's 11.749): each
    built that arc the long way round.
    """
    from ada.cadit.sat.write.plate_booleans import plate_faces_after_booleans

    from ..face_area import planar_face_area

    use_backend("occ" if backend.name == "pythonocc-core" else "adacpp")
    pl = _plate()
    pl.add_boolean(ada.PrimCyl("notch", (0, 1.5, -0.5), (0, 1.5, 0.5), 0.4))
    (face,) = plate_faces_after_booleans(pl)
    (outline,) = face.bounds
    kinds = [type(oe.edge_element.edge_geometry).__name__ for oe in outline.bound.edge_list]
    assert sorted(kinds) == ["Circle"] * 2 + ["Line"] * 5  # the cylinder's seam splits its arc
    assert planar_face_area(face) == pytest.approx(12.0 - np.pi * 0.16 / 2, rel=1e-12)


def test_a_cut_along_the_cylinder_seam_is_authored(backend, use_backend):
    """A plate in y = 0 cut by a cylinder on z: the cut's edge at x = 0.5 is the cylinder's seam.

    Its seam record made the reader raise a bare ``ValueError`` and the SAT writer drop the plate
    from the body on both kernels; the plate is the strip 0.5 <= x <= 2 of it.
    """
    from ada.cadit.sat.write.plate_booleans import plate_faces_after_booleans

    from ..face_area import planar_face_area

    use_backend("occ" if backend.name == "pythonocc-core" else "adacpp")
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01, origin=(0, 0, 0.5), xdir=(1, 0, 0), normal=(0, 1, 0))
    pl.add_boolean(ada.PrimCyl("c", (0, 0, -1), (0, 0, 1), 0.5))
    (face,) = plate_faces_after_booleans(pl)
    (outline,) = face.bounds
    ends = np.array([list(oe.start) for oe in outline.bound.edge_list])
    assert [type(oe.edge_element.edge_geometry) for oe in outline.bound.edge_list] == [geo_cu.Line] * len(ends)
    # y on the seam is the kernel's 0.5 sin(2 pi) = -1.2e-16 (measured, both kernels)
    assert np.allclose(ends.min(axis=0), [0.5, 0.0, -0.5], rtol=0, atol=2e-16)
    assert np.allclose(ends.max(axis=0), [2.0, 0.0, 0.5], rtol=0, atol=2e-16)
    assert planar_face_area(face) == pytest.approx(1.5, rel=1e-12)

    a = ada.Assembly("A") / (ada.Part("P") / pl)
    sw = part_to_sat_writer(a)
    assert len(sw.face_map[pl.guid]) == 1 and _loops_per_face(sw) == [1]


@pytest.mark.parametrize("error", [ValueError("could not convert"), IndexError("index 8 is out of bounds")])
def test_a_reader_that_fails_is_a_refusal_by_name(monkeypatch, ada_log, error):
    """Whatever the reader raises, the plate is written whole and said by name -- never dropped."""
    import ada.cadit.sat.write.plate_booleans as pb

    def fails(text):
        raise error

    monkeypatch.setattr(pb.bt, "read_planar_face", fails)
    pl = _plate()
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    with pytest.raises(pb.PlateBooleanNotAuthored, match=re.escape(f"{type(error).__name__}: {error}")):
        pb.plate_faces_after_booleans(pl)
    sw = part_to_sat_writer(ada.Assembly("A") / (ada.Part("P") / pl))
    assert _loops_per_face(sw) == [1]
    (warning,) = [r.getMessage() for r in ada_log if "without its" in r.getMessage()]
    assert "'pl'" in warning


CUTS = {
    "box": lambda: [ada.PrimBox("h", (1.5, 1.0, -0.5), (2.5, 2.0, 0.5))],
    "round": lambda: [ada.PrimCyl("h", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4)],
    "two": lambda: [
        ada.PrimCyl("a", (1, 1.5, -0.5), (1, 1.5, 0.5), 0.4),
        ada.PrimBox("b", (2.5, 1, -0.5), (3.5, 2, 0.5)),
    ],
    "slot": lambda: [ada.PrimBox("s", (1.5, -1.0, -0.5), (2.5, 4.0, 0.5))],
    "notch": lambda: [ada.PrimCyl("n", (0, 1.5, -0.5), (0, 1.5, 0.5), 0.4)],
    "sphere": lambda: [ada.PrimSphere("s", (2, 1.5, 0.0), 0.5)],
    "irrational": lambda: [ada.PrimCyl("h", (np.sqrt(2), np.pi / 2, -0.5), (np.sqrt(2), np.pi / 2, 0.5), 1 / 3)],
}


def _numbers(faces) -> list:
    out = []
    for f in faces:
        p = f.face_surface.position
        out.append(("plane", [*p.location, *p.axis, *p.ref_direction]))
        for b in f.bounds:
            for oe in b.bound.edge_list:
                c = oe.edge_element.edge_geometry
                if isinstance(c, geo_cu.Line):
                    nums = [*c.pnt, *c.dir]
                else:
                    nums = [*c.position.location, *c.position.axis, *c.position.ref_direction, c.radius]
                out.append(
                    (type(c).__name__, oe.edge_element.same_sense, nums, [*oe.start, *oe.end, oe.t_start, oe.t_end])
                )
    return out


@pytest.mark.parametrize("cut", sorted(CUTS))
@pytest.mark.parametrize("placed", [False, True])
def test_both_kernels_read_the_same_faces(both_backends, use_backend, cut, placed):
    """The same loops, edges and curves from either kernel's cut, to the digits the BREP text keeps.

    Curves and planes go through the text with 17 significant digits and come out bit-identical;
    edge parameters and so the points evaluated at them with 15 (adacpp writes them so; pythonocc
    keeps more). 15 digits of a parameter up to 2 pi, or of a coordinate up to 32 here, are within
    5e-15 of it relative: measured, the worst difference over these cases is 3.6e-15.
    """
    from ada.cadit.sat.write.plate_booleans import plate_faces_after_booleans

    def faces():
        pl = _plate()
        for prim in CUTS[cut]():
            pl.add_boolean(prim)
        if placed:
            ada.Assembly("A") / (
                ada.Part("P", placement=ada.Placement(origin=(10, 20, 30), xdir=(0, 1, 0), zdir=(1, 0, 0))) / pl
            )
        return _numbers(plate_faces_after_booleans(pl))

    use_backend("occ")
    occ = faces()
    use_backend("adacpp")
    acp = faces()
    assert [(k[0], k[1] if len(k) > 2 else None) for k in occ] == [(k[0], k[1] if len(k) > 2 else None) for k in acp]
    for a, b in zip(occ, acp):
        assert a[-1 if len(a) == 2 else 2] == b[-1 if len(b) == 2 else 2]  # planes and curves: bit-identical
        if len(a) > 2:
            ta, tb = np.asarray(a[3], dtype=float), np.asarray(b[3], dtype=float)
            assert np.all(np.abs(ta - tb) <= 5e-15 * np.maximum(2 * np.pi, np.abs(ta)))


def test_a_cut_face_used_reversed_reads_as_the_same_face(monkeypatch):
    """A face whose plane faces -z used reversed is the face whose plane faces +z: same advanced face.

    The cut never hands one back so (measured: forward, plane on -z), so the reading is rewritten to
    say the same face the other way -- plane axis flipped, used reversed.
    """
    import dataclasses

    import ada.cadit.sat.write.plate_booleans as pb

    def faces():
        pl = _plate()
        pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
        return _numbers(pb.plate_faces_after_booleans(pl))

    as_cut = faces()
    read = pb.bt.read_planar_face

    def flipped(text):
        f = read(text)
        plane = dataclasses.replace(f.plane, axis=-f.plane.axis)
        return dataclasses.replace(f, plane=plane, reversed=not f.reversed)

    monkeypatch.setattr(pb.bt, "read_planar_face", flipped)
    assert faces() == as_cut

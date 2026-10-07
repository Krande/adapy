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


def _area(pl) -> float:
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    from ada.occ.geom import geom_to_occ_geom

    props = GProp_GProps()
    brepgprop.SurfaceProperties(geom_to_occ_geom(pl.geom), props, 1e-9)
    return props.Mass()


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
    assert _area(plates["square"]) == pytest.approx(11.0, rel=1e-12)
    assert _area(plates["round"]) == pytest.approx(12.0 - np.pi * 0.16, rel=1e-12)


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

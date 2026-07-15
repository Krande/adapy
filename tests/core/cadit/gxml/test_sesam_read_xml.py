from collections import Counter

import ada


def test_sesam_xml(example_files, tmp_path, monkeypatch):
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()

    monkeypatch.setenv("ADA_GXML_IMPORT_ADVANCED_FACES", "true")
    a = ada.from_genie_xml(xml_file)

    assert len(a.get_all_subparts()) == 1
    p = a.get_all_subparts()[0]
    objects = list(p.get_all_physical_objects())

    # The fixture has 3 curved_shell elements over 9 SAT faces. Every one of
    # those faces is bounded by at least one intcurve — including the four with
    # a PLANE surface — so all nine come in as PlateCurved.
    #
    # Those four used to merge into a single flat Plate: flat surface, so the
    # reader took the polygon path and their b-spline edges survived only as
    # polylines. That merge was cheaper but not what the file says, and a plate
    # whose edge has been straightened no longer shares that edge with the
    # curved face it was cut against.
    assert len(objects) == 9
    kinds = Counter(type(o).__name__ for o in objects)
    # two still fall back to a flat Plate, caught by the stretched-surface guard
    # rather than by having straight edges
    assert kinds == {"PlateCurved": 7, "Plate": 2}, kinds

    from ada.api.plates import PlateCurved

    surfaces = Counter(type(o.geom.geometry.face_surface).__name__ for o in objects if isinstance(o, PlateCurved))
    # the flat-surfaced ones are carried as advanced faces for their edges' sake
    assert surfaces["Plane"] > 0, surfaces

    a.to_ifc(tmp_path / "sesam_test.ifc", validate=True)


def test_deformed_face_rejection_can_be_turned_off(example_files, monkeypatch):
    """The guard is a heuristic over control points, so it must be escapable.

    A b-spline's control points need not lie near the surface, so a patch can be
    called "stretched" on a face that is perfectly good — and the cost of a false
    positive is silent: the plate keeps its position and loses its boundary.
    """
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()
    monkeypatch.setenv("ADA_GXML_IMPORT_ADVANCED_FACES", "true")
    monkeypatch.setenv("ADA_GXML_REJECT_DEFORMED_CURVED_FACES", "false")

    a = ada.from_genie_xml(xml_file)
    objects = list(a.get_all_subparts()[0].get_all_physical_objects())

    # the two the guard rejects are kept as advanced faces instead
    assert Counter(type(o).__name__ for o in objects) == {"PlateCurved": 9}

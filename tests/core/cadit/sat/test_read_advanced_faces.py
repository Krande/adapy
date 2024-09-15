import ada
import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada.cadit.sat.store import SatReaderFactory


def test_read_b_spline_surf_w_knots_2(example_files, tmp_path, monkeypatch):
    # monkeypatch.setenv("ADA_SAT_READ_CURVE_IGNORE_BSPLINE", "true")
    sat_reader = SatReaderFactory(example_files / "sat_files/curved_plate.sat")
    advanced_faces = list(sat_reader.iter_advanced_faces())

    assert len(advanced_faces) == 1
    _, adv_face = advanced_faces[0]

    assert isinstance(adv_face.face_surface, geo_su.RationalBSplineSurfaceWithKnots)
    face_surf = adv_face.face_surface
    assert len(face_surf.control_points_list) == 3
    assert len(face_surf.control_points_list[0]) == 4

    bounds = adv_face.bounds
    assert len(bounds) == 1

    edge_loop = bounds[0].bound

    assert isinstance(edge_loop, geo_cu.EdgeLoop)

    edge_list = edge_loop.edge_list
    assert len(edge_list) == 10

    edge1 = edge_list[0]
    assert isinstance(edge1, geo_cu.OrientedEdge)

    edge_element = edge1.edge_element
    assert isinstance(edge_element, geo_cu.EdgeCurve)

    edge_geom = edge_element.edge_geometry
    assert isinstance(edge_geom, geo_cu.RationalBSplineCurveWithKnots)

    shp = ada.Shape("plate", ada.geom.Geometry(1, adv_face, None))

    a = ada.Assembly() / shp
    a.to_ifc(tmp_path / "curved_plate.ifc", validate=True)


def test_read_b_spline_surf_w_knots(example_files, tmp_path):
    sat_reader = SatReaderFactory(example_files / "sat_files/bsplinesurfacewithknots.sat")
    advanced_faces = list(sat_reader.iter_advanced_faces())
    assert len(advanced_faces) == 1
    _, adv_face = advanced_faces[0]

    assert type(adv_face.face_surface) is geo_su.BSplineSurfaceWithKnots
    face_surf = adv_face.face_surface

    assert len(face_surf.control_points_list) == 4
    assert len(face_surf.control_points_list[0]) == 2

    shp = ada.Shape("plate", ada.geom.Geometry(1, adv_face, None))

    a = ada.Assembly() / shp
    a.to_ifc(tmp_path / "bsplinesurfacewithknots.ifc", validate=True)

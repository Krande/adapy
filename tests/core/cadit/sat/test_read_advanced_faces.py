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


def test_read_ellipse_face(example_files, tmp_path):
    sat_reader = SatReaderFactory(example_files / "sat_files/3_plates_ellipse.sat")
    faces = list(sat_reader.iter_all_faces())
    assert len(faces) == 3
    faces_map = {}
    for face in faces:
        face_record, face_obj = face
        face_name = sat_reader.sat_store.get_name(face_record.chunks[2])
        faces_map[face_name] = face_obj

    face_001 = faces_map["FACE00000001"]
    assert type(face_001) is geo_su.ClosedShell
    shp1 = ada.Shape("plate", ada.geom.Geometry(1, face_001, None))

    face_002 = faces_map["FACE00000002"]
    assert type(face_002.face_surface) is geo_su.RationalBSplineSurfaceWithKnots

    shp2 = ada.Shape("plate", ada.geom.Geometry(1, face_002, None))

    face_003 = faces_map["FACE00000003"]
    assert type(face_003.face_surface) is geo_su.RationalBSplineSurfaceWithKnots
    shp3 = ada.Shape("plate", ada.geom.Geometry(1, face_003, None))
    a = ada.Assembly() / (shp1, shp2, shp3)

    a.to_ifc(tmp_path / "3_plates_ellipse.ifc", validate=True)


def test_read_plate_1_flat(example_files, tmp_path):
    sat_reader = SatReaderFactory(example_files / "sat_files/plate_1_flat.sat")
    faces = list(sat_reader.iter_all_faces())
    assert len(faces) == 1

    face_001 = faces[0][1]
    assert type(face_001) is geo_su.ClosedShell

    face_surface = face_001.cfs_faces[0]
    assert type(face_surface) is geo_su.FaceSurface

    face_bounds = face_surface.bounds
    assert len(face_bounds) == 1
    face_bound = face_bounds[0]
    assert type(face_bound) is geo_su.FaceBound

    edge_loop = face_bound.bound
    assert type(edge_loop) is geo_cu.EdgeLoop

    edge_list = edge_loop.edge_list
    assert len(edge_list) == 4

    # Assert all orientations are True
    # for edge in edge_list:
    #     assert edge.orientation
    # for edge_1,edge_2 in zip(edge_list[:-1], edge_list[1:]):
    #     if edge_1.orientation and edge_2.orientation:
    #         assert edge_1.end.is_equal(edge_2.start)
    #     elif edge_1.orientation and not edge_2.orientation:
    #         assert edge_1.end.is_equal(edge_2.end)
    #     elif not edge_1.orientation and edge_2.orientation:
    #         assert edge_1.start.is_equal(edge_2.start)
    #     else:
    #         assert edge_1.start.is_equal(edge_2.end)
    shp1 = ada.Shape("plate", ada.geom.Geometry(1, face_001, None))

    a = ada.Assembly() / (shp1, )

    a.to_ifc(tmp_path / "plate_1_flat.ifc", validate=True)


def test_read_plate_2_curved_complex(example_files, tmp_path):
    sat_reader = SatReaderFactory(example_files / "sat_files/plate_2_curved_complex.sat")
    faces = list(sat_reader.iter_all_faces())
    assert len(faces) == 1

    face_001 = faces[0][1]
    assert type(face_001) is geo_su.AdvancedFace

    face_surface = face_001.face_surface
    assert type(face_surface) is geo_su.RationalBSplineSurfaceWithKnots

    face_bounds = face_001.bounds
    assert len(face_bounds) == 1
    face_bound = face_bounds[0]
    assert type(face_bound) is geo_su.FaceBound

    edge_loop = face_bound.bound
    assert type(edge_loop) is geo_cu.EdgeLoop

    edge_list = edge_loop.edge_list
    assert len(edge_list) == 4

    # Assert all orientations are True
    # for edge_1,edge_2 in zip(edge_list[:-1], edge_list[1:]):
    #     if not edge_1.end.is_equal(edge_2.start):
    #         assert not edge_1.orientation
    #         assert edge_2.orientation
    #     elif not edge_1.start.is_equal(edge_2.end):
    #         assert edge_1.orientation
    #         assert not edge_2.orientation

    shp1 = ada.Shape("plate", ada.geom.Geometry(1, face_001, None))

    a = ada.Assembly() / (shp1, )

    a.to_ifc(tmp_path / "plate_2_curved_complex.ifc", validate=True)

def test_read_plate_3_curved(example_files, tmp_path):
    sat_reader = SatReaderFactory(example_files / "sat_files/plate_3_curved.sat")
    faces = list(sat_reader.iter_all_faces())
    assert len(faces) == 1

    face_001 = faces[0][1]
    assert type(face_001) is geo_su.AdvancedFace
    shp1 = ada.Shape("plate", ada.geom.Geometry(1, face_001, None))

    a = ada.Assembly() / (shp1, )

    a.to_ifc(tmp_path / "plate_3_curved.ifc", validate=True)
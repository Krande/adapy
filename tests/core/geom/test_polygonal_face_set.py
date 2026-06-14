import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.surfaces import (
    polygonal_face_set as read_polygonal_face_set,
)
from ada.cadit.ifc.write.geom.surfaces import (
    polygonal_face_set as write_polygonal_face_set,
)
from ada.geom import Geometry
from ada.geom import surfaces as geo_su
from ada.geom.points import Point


def _unit_cube_pfs() -> geo_su.PolygonalFaceSet:
    """A unit cube as a polygonal face set: 8 corners, 6 quad faces (1-based indices)."""
    coordinates = [
        Point(0, 0, 0),
        Point(1, 0, 0),
        Point(1, 1, 0),
        Point(0, 1, 0),
        Point(0, 0, 1),
        Point(1, 0, 1),
        Point(1, 1, 1),
        Point(0, 1, 1),
    ]
    faces = [
        [1, 2, 3, 4],  # bottom
        [5, 6, 7, 8],  # top
        [1, 2, 6, 5],  # front
        [2, 3, 7, 6],  # right
        [3, 4, 8, 7],  # back
        [4, 1, 5, 8],  # left
    ]
    return geo_su.PolygonalFaceSet(coordinates=coordinates, faces=faces, closed=True)


def test_polygonal_face_set_occ_build():
    pfs = _unit_cube_pfs()
    occ_shape = active_backend().build(Geometry("pfs", pfs))
    # A sewn closed n-gon set comes back as a shell (or compound of shells).
    assert active_backend().shape_type(occ_shape) in ("shell", "solid", "compound")


def test_polygonal_face_set_ifc_roundtrip():
    pfs = _unit_cube_pfs()

    f = ifcopenshell.file(schema="IFC4")
    ifc_pfs = write_polygonal_face_set(pfs, f)

    assert ifc_pfs.is_a("IfcPolygonalFaceSet")
    assert ifc_pfs.Closed is True
    assert len(ifc_pfs.Faces) == 6
    assert len(ifc_pfs.Coordinates.CoordList) == 8

    read_back = read_polygonal_face_set(ifc_pfs)
    assert isinstance(read_back, geo_su.PolygonalFaceSet)
    assert len(read_back.coordinates) == 8
    assert read_back.faces == pfs.faces
    assert read_back.closed is True
    # First coordinate survives the round-trip.
    assert read_back.coordinates[0].is_equal(pfs.coordinates[0])

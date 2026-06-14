import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.solids import swept_disk_solid as read_swept_disk_solid
from ada.cadit.ifc.write.geom.solids import swept_disk_solid as write_swept_disk_solid
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom.points import Point


def _directrix() -> geo_cu.IndexedPolyCurve:
    # An L-shaped polyline directrix (exercises the bend transition handling).
    return geo_cu.IndexedPolyCurve(
        segments=[
            geo_cu.Edge(Point(0, 0, 0), Point(0, 0, 1)),
            geo_cu.Edge(Point(0, 0, 1), Point(0, 1, 1)),
        ]
    )


def test_swept_disk_solid_occ_build():
    sds = geo_so.SweptDiskSolid(directrix=_directrix(), radius=0.05)
    occ_shape = active_backend().build(Geometry("sds", sds))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")


def test_swept_disk_solid_annular_occ_build():
    sds = geo_so.SweptDiskSolid(directrix=_directrix(), radius=0.05, inner_radius=0.03)
    occ_shape = active_backend().build(Geometry("sds", sds))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")


def test_swept_disk_solid_ifc_roundtrip():
    sds = geo_so.SweptDiskSolid(directrix=_directrix(), radius=0.05, inner_radius=0.03)

    f = ifcopenshell.file(schema="IFC4")
    ifc_sds = write_swept_disk_solid(sds, f)

    assert ifc_sds.is_a("IfcSweptDiskSolid")
    assert ifc_sds.Radius == 0.05
    assert ifc_sds.InnerRadius == 0.03
    assert ifc_sds.Directrix.is_a("IfcIndexedPolyCurve")

    read_back = read_swept_disk_solid(ifc_sds)
    assert isinstance(read_back, geo_so.SweptDiskSolid)
    assert read_back.radius == 0.05
    assert read_back.inner_radius == 0.03
    assert isinstance(read_back.directrix, geo_cu.IndexedPolyCurve)

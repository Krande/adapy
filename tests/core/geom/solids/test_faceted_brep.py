import ifcopenshell

from ada.cad import active_backend
from ada.cadit.ifc.read.geom.solids import faceted_brep as read_faceted_brep
from ada.cadit.ifc.write.geom.solids import faceted_brep as write_faceted_brep
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.points import Point


def _cube_shell(origin=(0.0, 0.0, 0.0), size=1.0) -> geo_su.ClosedShell:
    ox, oy, oz = origin
    s = size
    p = [
        Point(ox, oy, oz),
        Point(ox + s, oy, oz),
        Point(ox + s, oy + s, oz),
        Point(ox, oy + s, oz),
        Point(ox, oy, oz + s),
        Point(ox + s, oy, oz + s),
        Point(ox + s, oy + s, oz + s),
        Point(ox, oy + s, oz + s),
    ]
    quads = [
        [0, 1, 2, 3],  # bottom
        [4, 5, 6, 7],  # top
        [0, 1, 5, 4],  # front
        [1, 2, 6, 5],  # right
        [2, 3, 7, 6],  # back
        [3, 0, 4, 7],  # left
    ]
    faces = [
        geo_su.Face(bounds=[geo_su.FaceBound(bound=geo_cu.PolyLoop(polygon=[p[i] for i in quad]), orientation=True)])
        for quad in quads
    ]
    return geo_su.ClosedShell(cfs_faces=faces)


def test_faceted_brep_occ_build():
    from ada.geom import solids as geo_so

    fb = geo_so.FacetedBrep(outer=_cube_shell())
    occ_shape = active_backend().build(Geometry("fb", fb))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")


def test_faceted_brep_ifc_roundtrip():
    from ada.geom import solids as geo_so

    fb = geo_so.FacetedBrep(outer=_cube_shell())

    f = ifcopenshell.file(schema="IFC4")
    ifc_fb = write_faceted_brep(fb, f)

    assert ifc_fb.is_a("IfcFacetedBrep")
    assert ifc_fb.Outer.is_a("IfcClosedShell")
    assert len(ifc_fb.Outer.CfsFaces) == 6
    # Each face is a plain IfcFace with an IfcPolyLoop bound of 4 points.
    first_face = ifc_fb.Outer.CfsFaces[0]
    assert first_face.is_a("IfcFace")
    assert first_face.Bounds[0].Bound.is_a("IfcPolyLoop")
    assert len(first_face.Bounds[0].Bound.Polygon) == 4

    read_back = read_faceted_brep(ifc_fb)
    assert isinstance(read_back, geo_so.FacetedBrep)
    assert len(read_back.outer.cfs_faces) == 6
    assert isinstance(read_back.outer.cfs_faces[0].bounds[0].bound, geo_cu.PolyLoop)
    assert read_back.voids == []


def test_faceted_brep_with_voids_roundtrip():
    from ada.geom import solids as geo_so

    # Outer 2.0 cube with an inner 1.0 void -> IfcFacetedBrepWithVoids (a hollow block).
    fb = geo_so.FacetedBrep(
        outer=_cube_shell(size=2.0),
        voids=[_cube_shell(origin=(0.5, 0.5, 0.5), size=1.0)],
    )

    f = ifcopenshell.file(schema="IFC4")
    ifc_fb = write_faceted_brep(fb, f)
    assert ifc_fb.is_a("IfcFacetedBrepWithVoids")
    assert len(ifc_fb.Voids) == 1

    read_back = read_faceted_brep(ifc_fb)
    assert len(read_back.voids) == 1
    assert len(read_back.voids[0].cfs_faces) == 6

    occ_shape = active_backend().build(Geometry("hollow", fb))
    assert active_backend().shape_type(occ_shape) in ("solid", "shell", "compound")

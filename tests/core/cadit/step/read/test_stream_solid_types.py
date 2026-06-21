"""STEP solid/model coverage: faceted brep, CSG primitives, swept solids, booleans, sets.

Each imports into a native ada.geom solid/model type (no geometry left behind).
"""

from __future__ import annotations

import ada.geom.solids as gso
from ada.cadit.step.read import stream_reader as sr
from ada.geom.booleans import BooleanResult, BoolOpEnum
from ada.geom.curves import GeometricCurveSet, Line
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.surfaces import ClosedShell


class _IdResolver:
    def deref(self, x):
        return x


def test_faceted_brep_imports():
    s = sr._b_faceted_brep(_IdResolver(), ["", ClosedShell(cfs_faces=[])])
    assert isinstance(s, gso.FacetedBrep)


def test_block_imports_as_box():
    s = sr._b_block(_IdResolver(), ["", Axis2Placement3D(), 1.0, 2.0, 3.0])
    assert isinstance(s, gso.Box) and (s.x_length, s.y_length, s.z_length) == (1.0, 2.0, 3.0)


def test_cylinder_and_cone_imports():
    cyl = sr._b_right_circular_cylinder(_IdResolver(), ["", Axis2Placement3D(), 5.0, 1.0])
    assert isinstance(cyl, gso.Cylinder) and cyl.height == 5.0 and cyl.radius == 1.0
    cone = sr._b_right_circular_cone(_IdResolver(), ["", Axis2Placement3D(), 4.0, 2.0, 0.3])
    assert isinstance(cone, gso.Cone) and cone.height == 4.0 and cone.bottom_radius == 2.0


def test_axis1_placement_coerced_for_csg_prim():
    # CSG primitives often carry an AXIS1_PLACEMENT; it must coerce to a full placement
    ax1 = Axis1Placement(location=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0))
    cyl = sr._b_right_circular_cylinder(_IdResolver(), ["", ax1, 5.0, 1.0])
    assert isinstance(cyl.position, Axis2Placement3D)


def test_sphere_and_torus_imports():
    sph = sr._b_sphere(_IdResolver(), ["", 2.0, (0.0, 0.0, 0.0)])
    assert isinstance(sph, gso.Sphere) and sph.radius == 2.0
    tor = sr._b_torus(_IdResolver(), ["", Axis1Placement((0, 0, 0), (0, 0, 1)), 5.0, 1.0])
    assert isinstance(tor, gso.Torus) and tor.major_radius == 5.0 and tor.minor_radius == 1.0


def test_swept_solids_import():
    ext = sr._b_extruded_area_solid(_IdResolver(), ["", "PROFILE", Axis2Placement3D(), (0, 0, 1), 10.0])
    assert isinstance(ext, gso.ExtrudedAreaSolid) and ext.depth == 10.0
    rev = sr._b_revolved_area_solid(_IdResolver(), ["", "PROFILE", Axis1Placement((0, 0, 0), (0, 0, 1)), 3.14])
    assert isinstance(rev, gso.RevolvedAreaSolid) and rev.angle == 3.14


def test_boolean_and_csg_import():
    br = sr._b_boolean_result(_IdResolver(), [sr._Enum("DIFFERENCE"), "A", "B"])
    assert isinstance(br, BooleanResult) and br.operator == BoolOpEnum.DIFFERENCE
    csg = sr._b_csg_solid(_IdResolver(), ["", br])
    assert csg is br


def test_geometric_set_imports():
    gset = sr._b_geometric_set(_IdResolver(), ["", [Line((0, 0, 0), (1, 0, 0))]])
    assert isinstance(gset, GeometricCurveSet) and len(gset.elements) == 1


def test_solid_types_registered():
    for t in ("FACETED_BREP", "BLOCK", "RIGHT_CIRCULAR_CYLINDER", "RIGHT_CIRCULAR_CONE",
              "SPHERE", "TORUS", "EXTRUDED_AREA_SOLID", "REVOLVED_AREA_SOLID", "CSG_SOLID",
              "BOOLEAN_RESULT", "GEOMETRIC_CURVE_SET", "GEOMETRIC_SET",
              "MANIFOLD_SURFACE_SHAPE_REPRESENTATION"):
        assert t in sr._BUILDERS

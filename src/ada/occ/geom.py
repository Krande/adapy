from typing import Iterable

from OCC.Core.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeSphere,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakeCone,
)
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir


def make_box(
        x: float,
        y: float,
        z: float,
        width: float,
        height: float,
        depth: float,
        axis1: Iterable[float] = None,
        axis2: Iterable[float] = None,
) -> TopoDS_Shape:
    vec1 = gp_Dir(0, 0, 1) if axis1 is None else gp_Dir(*axis1)
    vec2 = gp_Dir(0, 1, 0) if axis2 is None else gp_Dir(*axis2)
    box_maker = BRepPrimAPI_MakeBox(
        gp_Ax2(
            gp_Pnt(x, y, z),
            vec1,
            vec2,
        ),
        width,
        height,
        depth,
    )
    return box_maker.Shape()


def make_sphere(x: float, y: float, z: float, radius: float) -> TopoDS_Shape:
    sphere_maker = BRepPrimAPI_MakeSphere(gp_Pnt(x, y, z), radius)
    return sphere_maker.Shape()


def make_cylinder(
        x: float, y: float, z: float, radius: float, height: float, axis: Iterable[float] = None
) -> TopoDS_Shape:
    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    cylinder_maker = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), vec), radius, height)
    return cylinder_maker.Shape()


def make_cone(
        x: float, y: float, z: float, r1: float, height: float, r2=0, axis: Iterable[float] = None
) -> TopoDS_Shape:
    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    cone_maker = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, z), vec), r1, r2, height)
    return cone_maker.Shape()

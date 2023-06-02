from typing import Iterable

from OCC.Core.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeSphere,
)
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCC.Core.TopoDS import TopoDS_Shape


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

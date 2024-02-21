from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape


def serialize_shape(shape: TopoDS_Shape) -> str:
    breptools.Clean(shape)
    return breptools.WriteToString(shape)

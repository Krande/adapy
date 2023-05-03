from OCC.Core.BRepTools import breptools_Clean, breptools_WriteToString
from OCC.Core.TopoDS import TopoDS_Shape


def serialize_shape(shape: TopoDS_Shape) -> str:
    breptools_Clean(shape)
    return breptools_WriteToString(shape)

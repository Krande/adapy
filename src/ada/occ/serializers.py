from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape


def serialize_shape(shape: TopoDS_Shape) -> str:
    # OCC-internal: operates on raw pythonocc TopoDS shapes (STEP→IFC path),
    # so it serializes with OCC directly rather than the active CAD backend.
    breptools.Clean(shape)
    return breptools.WriteToString(shape)

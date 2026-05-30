from OCC.Core.TopoDS import TopoDS_Shape

from ada.cad import active_backend


def serialize_shape(shape: TopoDS_Shape) -> str:
    return active_backend().serialize(shape)

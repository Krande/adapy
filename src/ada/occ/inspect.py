"""Raw-OCC introspection fallbacks for :mod:`ada.cad.inspect`.

These handle a *raw* pythonocc ``TopoDS_Shape`` (not a backend ``ShapeHandle``)
passed by construction-internal callers. They live here in ``ada.occ`` so that
``ada.cad.inspect`` stays backend-neutral and carries no ``OCC`` import — only
reachable under the OCC backend, since adacpp construction yields handles.
"""

from __future__ import annotations


def raw_vertex_points(shape) -> list[tuple[float, float, float]]:
    """All vertex points of a raw ``TopoDS_Shape`` as ``(x, y, z)`` tuples."""
    from OCC.Core.BRep import BRep_Tool
    from OCC.Extend.TopologyUtils import TopologyExplorer

    points = []
    for v in TopologyExplorer(shape).vertices():
        apt = BRep_Tool.Pnt(v)
        points.append((apt.X(), apt.Y(), apt.Z()))
    return points


def raw_faces(shape) -> list:
    """All faces of a raw ``TopoDS_Shape``."""
    from OCC.Extend.TopologyUtils import TopologyExplorer

    return list(TopologyExplorer(shape).faces())

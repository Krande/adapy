"""OCC helpers backing :class:`ada.api.plates.PlateCurved`.

Keeps the OCC.Core.* imports out of the ``ada.api`` layer — the API
module just lazy-imports the helpers it needs so the heavy OCC dep is
only pulled when a render / boundary-node / extrusion call is actually
made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCC.Core.BRepTools import breptools
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.gp import gp_Vec
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Shape, topods
from OCC.Extend.TopologyUtils import TopologyExplorer

from ada.api.nodes import Node
from ada.occ.geom import geom_to_occ_geom

if TYPE_CHECKING:
    from ada.geom import Geometry


def boundary_nodes_of_face(occ_shape: TopoDS_Shape) -> list[Node]:
    """Outer-wire vertices of an OCC face shape, as adapy Nodes.

    Walks the first wire encountered in ``occ_shape`` (the outer
    boundary; any further wires would be hole loops). Empty list when
    no face / no wire is found, so the caller's render path can
    continue even on malformed input.
    """
    exp = TopExp_Explorer(occ_shape, TopAbs_FACE)
    if not exp.More():
        return []
    face = topods.Face(exp.Current())
    wires = list(TopologyExplorer(face).wires())
    if not wires:
        return []
    nodes: list[Node] = []
    for vertex in TopologyExplorer(wires[0]).vertices():
        pnt = BRep_Tool.Pnt(vertex)
        nodes.append(Node((pnt.X(), pnt.Y(), pnt.Z())))
    return nodes


def boundary_nodes_of(face_geom: "Geometry") -> list[Node]:
    """Same as :func:`boundary_nodes_of_face`, but takes an adapy
    :class:`~ada.geom.Geometry` and converts to OCC first.

    Used by :attr:`PlateCurved.nodes` for the AdvancedFace-backed
    construction path. The raw-OCC construction path (``from_occ_face``)
    skips this helper and calls :func:`boundary_nodes_of_face` on the
    stored face directly.
    """
    return boundary_nodes_of_face(geom_to_occ_geom(face_geom))


def extrude_face_along_normal(face_shape: TopoDS_Shape, thickness: float) -> TopoDS_Shape:
    """Prism-extrude a TopoDS_Face by ``thickness`` along its surface
    normal at the face centre. Falls back to the bare face shape when
    thickness is zero, the normal can't be evaluated at the parametric
    centre, or ``BRepPrimAPI_MakePrism`` reports failure.
    """
    if not thickness:
        return face_shape
    exp = TopExp_Explorer(face_shape, TopAbs_FACE)
    if not exp.More():
        return face_shape
    sub_face = exp.Current()
    surf = BRep_Tool.Surface(sub_face)
    try:
        u_min, u_max, v_min, v_max = breptools.UVBounds(sub_face)
    except Exception:
        u_min, u_max, v_min, v_max = 0.0, 1.0, 0.0, 1.0
    uc, vc = (u_min + u_max) / 2, (v_min + v_max) / 2
    props = GeomLProp_SLProps(surf, uc, vc, 1, 1e-7)
    if not props.IsNormalDefined():
        return face_shape
    n = props.Normal()
    t = float(thickness)
    vec = gp_Vec(n.X() * t, n.Y() * t, n.Z() * t)
    prism = BRepPrimAPI_MakePrism(face_shape, vec)
    if not prism.IsDone():
        return face_shape
    return prism.Shape()


def extrude_face_geom_along_normal(face_geom: "Geometry", thickness: float) -> TopoDS_Shape:
    """Same as :func:`extrude_face_along_normal`, but takes an adapy
    :class:`~ada.geom.Geometry` and converts to OCC first.

    The fallback behaviour mirrors the gxml importer's flat-plate
    fallback path: the caller's render pipeline always gets
    *something* drawable for every plate.
    """
    return extrude_face_along_normal(geom_to_occ_geom(face_geom), thickness)


__all__ = [
    "boundary_nodes_of",
    "boundary_nodes_of_face",
    "extrude_face_along_normal",
    "extrude_face_geom_along_normal",
]

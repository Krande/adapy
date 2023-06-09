from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.utils import ifc_p
from ada.geom import curves as geo_cu


def indexed_poly_curve(ipc: geo_cu.IndexedPolyCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an IndexedPolyCurve to an IFC representation"""
    points = [ifc_p(f, x) for x in ipc.segments]
    segments = [f.create_entity("IfcPolylineSegment", points=x) for x in ipc.segments]
    return f.create_entity("IfcIndexedPolyCurve", points, segments)

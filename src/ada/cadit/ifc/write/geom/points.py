from __future__ import annotations

import ifcopenshell

from ada.core.utils import to_real


def cpt(f: ifcopenshell.file, p):
    return f.create_entity("IfcCartesianPoint", to_real(p))


def vrtx(f: ifcopenshell.file, p):
    return f.create_entity("IfcVertexPoint", VertexGeometry=cpt(f, p))

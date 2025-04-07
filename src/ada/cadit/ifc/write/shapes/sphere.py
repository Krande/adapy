from __future__ import annotations

from ada import PrimSphere
from ada.cadit.ifc.utils import create_ifc_placement
from ada.core.constants import X, Z
from ada.core.utils import to_real


def generate_ifc_prim_sphere_geom(shape: PrimSphere, f):
    """Create IfcSphere from primitive PrimSphere"""
    opening_axis_placement = create_ifc_placement(f, to_real(shape.cog), Z, X)
    return f.createIfcSphere(opening_axis_placement, float(shape.radius))

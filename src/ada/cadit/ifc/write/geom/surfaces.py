from __future__ import annotations

import ifcopenshell

from ada.geom import surfaces as geo_su
from ada.geom import curves as geo_cu
from .curves import indexed_poly_curve


def arbitrary_profile_def(
    apd: geo_su.ArbitraryProfileDef, f: ifcopenshell.file
) -> ifcopenshell.entity_instance:
    """Converts an ArbitraryProfileDefWithVoids to an IFC representation"""
    if isinstance(apd.outer_curve, geo_cu.IndexedPolyCurve):
        outer_curve = indexed_poly_curve(apd.outer_curve, f)
    else:
        raise NotImplementedError(f"Unsupported outer curve type: {type(apd.outer_curve)}")

    inner_curves = []
    for ic in apd.inner_curves:
        if isinstance(ic, geo_cu.IndexedPolyCurve):
            inner_curves.append(indexed_poly_curve(ic, f))

    if len(inner_curves) == 0:
        return f.create_entity("IfcArbitraryClosedProfileDef", "AREA", ProfileName="test", OuterCurve=outer_curve)

    return f.create_entity("IfcArbitraryProfileDefWithVoids", "AREA", OuterCurve=outer_curve, InnerCurves=inner_curves)

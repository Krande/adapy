from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ada import Weld

from .reader_utils import get_axis_polyline_points_from_product, get_ifc_property_sets

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_ifc_fastener(product, name, ifc_store: IfcStore) -> Weld:
    """Reconstruct a Weld from an IfcFastener (PredefinedType WELD).

    The bead is rebuilt from the persisted profile + xdir and the Axis endpoints; members and
    groove are not round-tripped (the bead geometry doesn't need them)."""
    pts = get_axis_polyline_points_from_product(product)
    p1 = tuple(float(c) for c in pts[0])
    p2 = tuple(float(c) for c in pts[-1])

    props = get_ifc_property_sets(product).get("Properties", {})
    weld_type = props.get("weld_type", "FILLET")
    xdir = tuple(json.loads(props["xdir"])) if props.get("xdir") else None
    profile = [tuple(p) for p in json.loads(props["profile"])] if props.get("profile") else None

    weld = Weld(name, p1=p1, p2=p2, weld_type=weld_type, members=(), profile=profile, xdir=xdir)
    weld.guid = product.GlobalId
    return weld

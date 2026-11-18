from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ada import Weld

from .reader_utils import get_axis_polyline_points_from_product, get_ifc_property_sets

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def _body_is_swept(product) -> bool:
    """True when the fastener's Body is the weld profile swept or extruded (what ``Weld`` rebuilds from its
    profile and line), False when it is a solid of its own (a bead written as a B-rep or a tessellation)."""
    from .read_shapes import _body_items

    items = _body_items(product)
    return not items or all(item.is_a("IfcSweptAreaSolid") for item in items)


def import_ifc_fastener(product, name, ifc_store: IfcStore) -> Weld:
    """Reconstruct a Weld from an IfcFastener (PredefinedType WELD).

    The bead is rebuilt from the persisted profile + xdir and the Axis endpoints; members and
    groove are not round-tripped (the bead geometry doesn't need them). A weld whose bead was
    written as a solid of its own (``Weld(bead=...)``) gets that solid back as its bead, its
    line (when the Axis has more than two points) as its ``sweep_curve``."""
    pts = get_axis_polyline_points_from_product(product)
    p1 = tuple(float(c) for c in pts[0])
    p2 = tuple(float(c) for c in pts[-1])

    props = get_ifc_property_sets(product).get("Properties", {})
    weld_type = props.get("weld_type", "FILLET")
    xdir = tuple(json.loads(props["xdir"])) if props.get("xdir") else None
    profile = [tuple(p) for p in json.loads(props["profile"])] if props.get("profile") else None

    bead = None if _body_is_swept(product) else _bead_geometry(product, name)
    if bead is None:
        weld = Weld(name, p1=p1, p2=p2, weld_type=weld_type, members=(), profile=profile, xdir=xdir)
    else:
        if len(pts) > 2:
            from ada.api.curves import CurveOpen3d

            line = dict(sweep_curve=CurveOpen3d([tuple(float(c) for c in p) for p in pts]))
        else:
            line = dict(p1=p1, p2=p2)
        weld = Weld(name, weld_type=weld_type, members=(), profile=profile, xdir=xdir, bead=bead, **line)
        # The solid is written in the fastener's local system (an ObjectPlacement only when it is not the world's).
        if product.ObjectPlacement is not None:
            from ifcopenshell.util.placement import get_local_placement

            from ada.cadit.ifc.read.geom.placement import placement_from_ifc_4x4

            weld.geometry.placement = placement_from_ifc_4x4(get_local_placement(product.ObjectPlacement))
    weld.guid = product.GlobalId
    return weld


def _bead_geometry(product, name):
    """The bead solid of a fastener written as a solid of its own, as an ada.geom Geometry; None (logged) when
    the native reader cannot read it -- the weld is then rebuilt from its profile and line."""
    from ada.cadit.ifc.read.geom.geom_reader import get_product_definitions
    from ada.config import logger
    from ada.core.guid import create_guid
    from ada.geom import Geometry

    try:
        geometries = get_product_definitions(product)
    except NotImplementedError as exc:
        geometries, why = [], exc
    else:
        why = f"{len(geometries)} body items"
    if len(geometries) != 1:
        logger.warning(f'weld "{name}": its bead solid is not read ({why}); rebuilt from its profile and line')
        return None
    geometry = geometries[0]
    if isinstance(geometry, Geometry):
        geometry.id = create_guid()  # the weld's guid is the product's
        return geometry
    return Geometry(create_guid(), geometry)

from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell.geom
from ifcopenshell.util.placement import get_local_placement

from ada import Shape
from ada.api.transforms import Placement
from ada.cadit.ifc.read.geom.geom_reader import get_product_definitions
from ada.cadit.ifc.read.read_color import get_product_color
from ada.config import Config, logger
from ada.geom import Geometry

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_ifc_shape(product: ifcopenshell.entity_instance, name, ifc_store: IfcStore, force_geom: bool = False):
    logger.info(f'importing Shape "{name}"')

    color = get_product_color(product, ifc_store.f)

    if Config().ifc_import_shape_geom or force_geom:
        geometries = get_product_definitions(product)
        if len(geometries) > 1:
            logger.warning(
                f"Multiple geometries associated to product {product}. Choosing arbitrarily geometry @ index=0"
            )
        elif len(geometries) == 0:
            logger.warning(f"No geometry associated to product {product}")
            geometries = None
        geo_color = color if color is not None else None
        geometries = Geometry(product.GlobalId, geometries[0], geo_color)
    else:
        geometries = None

    extra_opts = {}
    obj_placement = product.ObjectPlacement
    if obj_placement.PlacementRelTo:
        local_placement = get_local_placement(obj_placement)
        place = Placement.from_4x4_matrix(local_placement)
        extra_opts["placement"] = place

    return Shape(
        name,
        geom=geometries,
        guid=product.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
        opacity=color.opacity if color is not None else 1.0,
        **extra_opts,
    )


def _body_items(product: ifcopenshell.entity_instance) -> list:
    if product.Representation is None:
        return []
    for rep in product.Representation.Representations:
        if rep.RepresentationIdentifier == "Body":
            return list(rep.Items)
    return []


def import_ifc_sphere(product: ifcopenshell.entity_instance, name, ifc_store: IfcStore):
    """Import a product whose body is a single ``IfcSphere`` as a ``PrimSphere``.

    Returns ``None`` if the product is not a plain sphere (so callers can fall
    through to the generic shape importer).
    """
    items = _body_items(product)
    if len(items) != 1 or not items[0].is_a("IfcSphere"):
        return None

    from ada.api.primitives import PrimSphere

    sphere = items[0]
    center = tuple(float(x) for x in sphere.Position.Location.Coordinates)
    color = get_product_color(product, ifc_store.f)

    extra_opts = {}
    obj_placement = product.ObjectPlacement
    if obj_placement is not None and obj_placement.PlacementRelTo:
        extra_opts["placement"] = Placement.from_4x4_matrix(get_local_placement(obj_placement))

    return PrimSphere(
        name,
        center,
        float(sphere.Radius),
        guid=product.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
        opacity=color.opacity if color is not None else 1.0,
        **extra_opts,
    )

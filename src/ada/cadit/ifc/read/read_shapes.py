from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell.geom
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape

from ada import Shape
from ada.cadit.ifc.read.geom.geom_reader import get_product_definitions
from ada.cadit.ifc.read.read_color import get_product_color
from ada.config import Config, logger
from ada.geom import Geometry
from ada.visit.colors import Color

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_ifc_shape(product: ifcopenshell.entity_instance, name, ifc_store: IfcStore):
    logger.info(f'importing Shape "{name}"')

    color = get_product_color(product, ifc_store.f)

    if Config().ifc_import_shape_geom:
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

    return Shape(
        name,
        geom=geometries,
        guid=product.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
        opacity=color.opacity if color is not None else 1.0,
    )


def get_ifc_geometry(ifc_elem, settings) -> tuple[TopoDS_Shape | TopoDS_Compound | None, Color | None]:
    pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    if pdct_shape is None:
        print(f'Unable to import geometry for ifc element "{ifc_elem}"')
        return pdct_shape, None

    occ_geom = get_geom(ifc_elem, settings)
    r, g, b, alpha = pdct_shape.styles[0]  # the shape color

    colour = None if (r, g, b) == (-1, -1, -1) else (r, g, b)

    return occ_geom, Color(*colour)


def get_geom(ifc_elem, settings):
    from ifcopenshell.geom.occ_utils import shape_tuple
    from OCC.Core import BRepTools
    from OCC.Core.TopoDS import TopoDS_Compound

    try:
        pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)
    except RuntimeError:
        print(f'unable to parse ifc_elem "{ifc_elem}"')
        return

    if isinstance(pdct_shape, shape_tuple):
        shape = pdct_shape[1]
    else:
        shape = pdct_shape.solid

    if type(shape) is not TopoDS_Compound:
        brep_data = pdct_shape.solid.brep_data
        ss = BRepTools.BRepTools_ShapeSet()
        ss.ReadFromString(brep_data)
        nb_shapes = ss.NbShapes()
        occ_shape = ss.Shape(nb_shapes)
    else:
        occ_shape = shape
    return occ_shape

from __future__ import annotations

import ifcopenshell

from ada.config import logger
from ada.visit.colors import Color


def get_product_color(product: ifcopenshell.entity_instance, ifc_file: ifcopenshell.file) -> Color | None:
    styles = []
    for geo in ifc_file.traverse(product):
        if hasattr(geo, "StyledByItem") is False:
            continue
        if len(geo.StyledByItem) != 0:
            cstyle = geo.StyledByItem[0].Styles[0]
            if cstyle not in styles:
                styles.append(cstyle)

    if len(styles) == 0:
        logger.info(f'No style associated with IFC element "{product}"')
        return None

    if len(styles) > 1:
        logger.warning(f"Multiple styles associated to element {product}. Choosing arbitrarily style @ index=0")

    style = styles[0]
    colour_rgb = list(filter(lambda x: x.is_a("IfcColourRgb"), ifc_file.traverse(style)))
    style_rendering = list(filter(lambda x: x.is_a("IfcSurfaceStyleRendering"), ifc_file.traverse(style)))
    style_rendering += list(filter(lambda x: x.is_a("IfcSurfaceStyleShading"), ifc_file.traverse(style)))

    if len(colour_rgb) == 0:
        logger.warning(f'ColourRGB not found for IFC product "{product}"')
        return None

    opacity = 1.0 if len(style_rendering) == 0 else 1 - style_rendering[0].Transparency

    return Color(colour_rgb[0].Red, colour_rgb[0].Green, colour_rgb[0].Blue, opacity)

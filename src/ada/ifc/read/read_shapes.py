import logging
from typing import Union

import ifcopenshell.geom

from ada import Assembly, Shape

from ..concepts import IfcRef


def import_ifc_shape(product: ifcopenshell.entity_instance, name, ifc_ref: IfcRef, assembly: Assembly):
    logging.info(f'importing Shape "{name}"')
    color_res = get_colour(product, assembly)
    color, opacity = color_res if color_res is not None else None, 1.0
    return Shape(
        name, None, guid=product.GlobalId, ifc_ref=ifc_ref, units=assembly.units, colour=color, opacity=opacity
    )


def get_ifc_geometry(ifc_elem, settings):
    pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    if pdct_shape is None:
        print(f'Unable to import geometry for ifc element "{ifc_elem}"')
        return pdct_shape, None, None

    geom = get_geom(ifc_elem, settings)
    r, g, b, alpha = pdct_shape.styles[0]  # the shape color

    colour = None if (r, g, b) == (-1, -1, -1) else (r, g, b)

    return geom, colour, alpha


def get_colour(product: ifcopenshell.entity_instance, assembly: Assembly) -> Union[None, tuple]:
    triface = list(filter(lambda x: x.is_a("IfcTriangulatedFaceSet"), assembly.ifc_file.traverse(product)))
    if len(triface) > 0:
        style = triface[0].StyledByItem[0].Styles[0]
        colour_rgb = list(filter(lambda x: x.is_a("IfcColourRgb"), assembly.ifc_file.traverse(style)))
        transparency = list(filter(lambda x: x.is_a("IfcSurfaceStyleRendering"), assembly.ifc_file.traverse(style)))
        if len(transparency) > 0 and len(colour_rgb) > 0:
            opacity = transparency[0].Transparency
            rgb = colour_rgb[0].Red, colour_rgb[0].Green, colour_rgb[0].Blue
            return rgb, opacity

    return None


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

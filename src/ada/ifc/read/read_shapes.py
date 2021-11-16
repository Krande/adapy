import logging

import ifcopenshell.geom

from ada import Assembly, Shape

from .reader_utils import get_name, getIfcPropertySets


def import_ifc_shape(product, assembly: Assembly):
    props = getIfcPropertySets(product)
    name = get_name(product)
    logging.info(f'importing Shape "{name}"')
    shp = Shape(
        name,
        None,
        guid=product.GlobalId,
        metadata=dict(props=props),
    )
    return shp


def get_ifc_geometry(ifc_elem, settings):
    """

    :param ifc_elem:
    :param settings:
    :return:
    """
    pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    if pdct_shape is None:
        print(f'Unable to import geometry for ifc element "{ifc_elem}"')
        return pdct_shape, None, None

    geom = get_geom(ifc_elem, settings)
    r, g, b, alpha = pdct_shape.styles[0]  # the shape color

    colour = None if (r, g, b) == (-1, -1, -1) else (r, g, b)

    return geom, colour, alpha


def get_geom(ifc_elem, settings):
    """

    :param ifc_elem:
    :param settings:
    :return:
    """
    from ifcopenshell.geom.occ_utils import shape_tuple
    from OCC.Core import BRepTools
    from OCC.Core.TopoDS import TopoDS_Compound

    try:
        pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)
    except RuntimeError:
        print(f'unable to parse ifc_elem "{ifc_elem}"')
        return

    if type(pdct_shape) is shape_tuple:
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

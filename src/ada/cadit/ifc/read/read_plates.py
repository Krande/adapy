from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell

from ada import Plate
from ada.config import logger
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .geom.geom_reader import get_product_definitions
from .read_materials import read_material
from .reader_utils import get_associated_material

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_ifc_plate(ifc_elem: ifcopenshell.entity_instance, name, ifc_store: IfcStore) -> Plate:
    logger.info(f"importing {name}")
    geometries = get_product_definitions(ifc_elem)
    if len(geometries) != 1:
        raise NotImplementedError("Plate geometry with multiple bodies is not currently supported")
    if not isinstance(geometries[0].swept_area, geo_su.ArbitraryProfileDef):
        raise NotImplementedError("Plate geometry with non-arbitrary profile is not currently supported")

    body: geo_so.ExtrudedAreaSolid = geometries[0]
    points2d = body.swept_area.outer_curve.to_points2d()
    ifc_mat = get_associated_material(ifc_elem)

    mat = None
    if ifc_store.assembly is not None:
        mat = ifc_store.assembly.get_by_name(name)

    if mat is None:
        mat = read_material(ifc_mat, ifc_store)

    return Plate(
        name,
        points2d,
        body.depth,
        origin=body.position.location,
        xdir=body.position.ref_direction,
        normal=body.position.axis,
        mat=mat,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger

from .exceptions import NoIfcAxesAttachedError, UnableToConvertBoolResToBeamException
from .read_beams import import_ifc_beam
from .read_pipe import import_pipe_segment
from .read_plates import import_ifc_plate
from .read_shapes import import_ifc_shape

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_physical_ifc_elem(product, name, ifc_store: IfcStore):
    pr_type = product.is_a()

    if pr_type in ["IfcBeamStandardCase", "IfcBeam"]:
        try:
            return import_ifc_beam(product, name, ifc_store)
        except (NoIfcAxesAttachedError, UnableToConvertBoolResToBeamException) as e:
            logger.debug(e)
            pass
    if pr_type in ["IfcPlateStandardCase", "IfcPlate"]:
        try:
            return import_ifc_plate(product, name, ifc_store)
        except NoIfcAxesAttachedError as e:
            logger.debug(e)
            pass

    if product.is_a("IfcOpeningElement") is True:
        logger.info(f'skipping opening element "{product}"')
        return None

    if product.is_a() in ("IfcPipeSegment", "IfcPipeFitting"):
        return import_pipe_segment(product, name, ifc_store)

    if product.is_a("IfcPipeFitting"):
        logger.info('"IfcPipeFitting" is not yet added')

    obj = import_ifc_shape(product, name, ifc_store)

    return obj

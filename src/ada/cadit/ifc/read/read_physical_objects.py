from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger

from .exceptions import NoIfcAxesAttachedError, UnableToConvertBoolResToBeamException
from .read_beams import import_ifc_beam
from .read_pipe import import_pipe_segment
from .read_plates import import_ifc_plate
from .read_shapes import _has_body_representation, import_ifc_shape, import_ifc_sphere
from .read_wall import import_ifc_wall

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

    if pr_type in ["IfcWall", "IfcWallStandardCase"]:
        try:
            return import_ifc_wall(product, name, ifc_store)
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

    # Non-physical products (alignment, annotation, grid, positioning) carry only
    # curve/axis representations, never body geometry. Importing them as empty Shapes
    # pollutes the model and makes downstream exporters choke on a geometry-less shape
    # (solid_geom() raises) — and feeding their curves to the IfcOpenShell geometry kernel
    # can hang (IfcCurveSegment runs a 9000-iter root find per sample). Skip any product
    # that is neither a physical IfcElement nor carries a Body representation.
    if not product.is_a("IfcElement") and not _has_body_representation(product):
        logger.info(f'skipping non-physical product "{name}" ({product.is_a()})')
        return None

    # Sphere-bodied products (e.g. node/support markers) -> parametric PrimSphere.
    sphere = import_ifc_sphere(product, name, ifc_store)
    if sphere is not None:
        return sphere

    obj = import_ifc_shape(product, name, ifc_store)

    return obj

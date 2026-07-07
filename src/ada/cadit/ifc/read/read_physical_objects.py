from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger

from .exceptions import NoIfcAxesAttachedError, UnableToConvertBoolResToBeamException
from .read_beams import import_ifc_beam
from .read_fasteners import import_ifc_fastener
from .read_pipe import import_pipe_segment
from .read_plates import import_ifc_plate
from .read_shapes import _has_body_representation, import_ifc_shape, import_ifc_sphere
from .read_wall import import_ifc_wall

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def _belongs_to_system(product) -> bool:
    """True if the product is grouped by an IfcSystem (e.g. a pipe segment in a distribution
    system) — reconstructed at the system layer, not as a loose element."""
    for rel in getattr(product, "HasAssignments", None) or []:
        if rel.is_a("IfcRelAssignsToGroup") and rel.RelatingGroup is not None and rel.RelatingGroup.is_a("IfcSystem"):
            return True
    return False


def import_physical_ifc_elem(product, name, ifc_store: IfcStore):
    pr_type = product.is_a()

    # Typed imports are best-effort: a product the concept importer can't
    # express (tessellated body, missing material/axis, unusual profile) must
    # still land as a generic Shape via the fall-through below — never be
    # dropped. The named exceptions are the expected downgrades (debug); any
    # other failure is logged so real importer bugs stay visible.
    if pr_type in ["IfcBeamStandardCase", "IfcBeam"]:
        try:
            return import_ifc_beam(product, name, ifc_store)
        except (NoIfcAxesAttachedError, UnableToConvertBoolResToBeamException) as e:
            logger.debug(e)
        except Exception as e:  # noqa: BLE001
            logger.warning(f'Beam import of "{name}" failed ("{e}"); importing as a generic shape')
    if pr_type in ["IfcPlateStandardCase", "IfcPlate"]:
        try:
            return import_ifc_plate(product, name, ifc_store)
        except NoIfcAxesAttachedError as e:
            logger.debug(e)
        except Exception as e:  # noqa: BLE001
            logger.warning(f'Plate import of "{name}" failed ("{e}"); importing as a generic shape')

    if pr_type in ["IfcWall", "IfcWallStandardCase"]:
        try:
            return import_ifc_wall(product, name, ifc_store)
        except NoIfcAxesAttachedError as e:
            logger.debug(e)
        except Exception as e:  # noqa: BLE001
            logger.warning(f'Wall import of "{name}" failed ("{e}"); importing as a generic shape')

    if product.is_a("IfcFastener"):
        return import_ifc_fastener(product, name, ifc_store)

    if product.is_a("IfcOpeningElement") is True:
        logger.info(f'skipping opening element "{product}"')
        return None

    if product.is_a() in ("IfcPipeSegment", "IfcPipeFitting"):
        # Segments grouped by an IfcDistributionSystem are reconstructed as a Pipe in
        # load_systems(); skip them here so they aren't also imported as loose segments.
        if _belongs_to_system(product):
            return None
        return import_pipe_segment(product, name, ifc_store)

    # Non-physical products (alignment, annotation, grid, positioning) carry only
    # curve/axis representations, never body geometry. Feeding their IfcCurveSegments to the
    # IfcOpenShell geometry kernel can hang (a 9000-iter root find per sample), so we never do —
    # instead the alignment reader evaluates the analytic curve to a polyline natively (no OCC)
    # and renders it as GL_LINES.
    if not product.is_a("IfcElement") and not _has_body_representation(product):
        from .read_alignment import import_ifc_alignment, is_alignment_curve_product

        if is_alignment_curve_product(product):
            alignment = import_ifc_alignment(product, name, ifc_store)
            if alignment is not None:
                return alignment
        # No evaluable curve geometry (a geometry-less positioning product, an annotation, …):
        # importing it as an empty Shape pollutes the model and makes exporters choke
        # (solid_geom() raises), so skip it.
        logger.info(f'skipping non-physical product "{name}" ({product.is_a()})')
        return None

    # Sphere-bodied products (e.g. node/support markers) -> parametric PrimSphere.
    sphere = import_ifc_sphere(product, name, ifc_store)
    if sphere is not None:
        return sphere

    obj = import_ifc_shape(product, name, ifc_store)

    return obj

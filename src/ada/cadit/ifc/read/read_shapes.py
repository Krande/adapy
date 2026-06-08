from __future__ import annotations

import os
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

    geom = None
    occ_body = None
    if Config().ifc_import_shape_geom or force_geom:
        geom, occ_body = _read_shape_geometry(product, color)

    extra_opts = {}
    # Only apply the IFC local placement when we keep the native (parametric) geometry,
    # which is expressed in the product's local coordinates. The kernel fallback below
    # bakes world coordinates into the OCC body (``USE_WORLD_COORDS``), so re-applying
    # the placement there would double-transform it.
    if occ_body is None:
        obj_placement = product.ObjectPlacement
        if obj_placement is not None and obj_placement.PlacementRelTo:
            local_placement = get_local_placement(obj_placement)
            extra_opts["placement"] = Placement.from_4x4_matrix(local_placement)

    shape = Shape(
        name,
        geom=geom,
        guid=product.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
        opacity=color.opacity if color is not None else 1.0,
        **extra_opts,
    )

    # Assign the kernel-built OCC body to the transient cache explicitly rather than via
    # the constructor: the constructor only routes ``geom`` to ``_occ_cache`` when the
    # *active* backend recognises it as a shape handle, which is false under adacpp (the
    # body is always a pythonocc TopoDS from IfcOpenShell). The tessellator/exporters read
    # ``_occ_cache`` and adopt a foreign OCC body across the kernel boundary as needed.
    if occ_body is not None:
        shape._occ_cache = occ_body

    return shape


def _read_shape_geometry(product: ifcopenshell.entity_instance, color):
    """Resolve a product's body geometry, preferring adapy's native (parametric) reader.

    Returns ``(geom, occ_body)``: a native :class:`~ada.geom.Geometry` when every body
    item is a type adapy reads natively, else ``(None, occ_body)`` where ``occ_body`` is a
    world-placed OCC ``TopoDS`` built by the IfcOpenShell geometry kernel. The kernel
    fallback is what lets ``ifc.import_shape_geom`` be on by default: any IFC geometry
    representation (swept/half-space/mapped/b-spline/...) still imports, just as a faceted
    B-rep rather than a parametric one. Returns ``(None, None)`` only when no geometry can
    be produced at all (logged)."""
    if product.Representation is None:
        return None, None

    try:
        geometries = get_product_definitions(product)
    except NotImplementedError as e:
        logger.debug(f"native IFC geom reader unsupported for {product.is_a()}; using kernel fallback ({e})")
        geometries = []

    if geometries:
        if len(geometries) > 1:
            logger.warning(f"Multiple geometries on product {product}. Choosing geometry @ index=0")
        geometry = geometries[0]
        # A boolean result (e.g. IfcBooleanClippingResult) already comes back as a Geometry
        # carrying its bool_operations — adopt the product's guid/color rather than re-wrap.
        if isinstance(geometry, Geometry):
            geometry.id = product.GlobalId
            geometry.color = color
            return geometry, None
        return Geometry(product.GlobalId, geometry, color), None

    # Only fall back to the kernel for products that carry a *Body* representation, i.e. an
    # actual solid/surface to render. Curve-only products (e.g. IfcAlignmentSegment, whose
    # representation is an "Axis") have no body geometry, and the IfcOpenShell kernel can
    # spin effectively forever evaluating such curves (IfcCurveSegment runs a 9000-iter
    # root-find per sample point) — an uninterruptible C++ hang that no Python timeout can
    # break. The native reader already restricts itself to "Body" items, so this keeps the
    # fallback aligned with it.
    if not _has_body_representation(product):
        return None, None

    occ_body = _kernel_occ_shape(product)
    if occ_body is None:
        logger.warning(f"No geometry could be produced for product {product}")
    return None, occ_body


def _has_body_representation(product: ifcopenshell.entity_instance) -> bool:
    rep = getattr(product, "Representation", None)
    if rep is None:
        return False
    return any(r.RepresentationIdentifier == "Body" for r in rep.Representations)


def _kernel_occ_shape(product: ifcopenshell.entity_instance):
    """Build a world-placed OCC ``TopoDS`` for a product via the IfcOpenShell geometry
    kernel. Universal fallback for representation types adapy has no native reader for.
    Returns ``None`` (logged) if the kernel can't process the product."""
    import platform

    # The IfcOpenShell OCC kernel (create_shape) aborts the whole process (SIGABRT) on macOS
    # for some representations (e.g. mapped items) — an uncatchable native crash. Skip the
    # kernel fallback there: products with no native reader come back without geometry instead
    # of taking down the interpreter. Natively-read geometry never hits this path, so on macOS
    # only kernel-only representations degrade to no-geom. Override with ADA_IFC_MACOS_KERNEL=1.
    if platform.system() == "Darwin" and os.getenv("ADA_IFC_MACOS_KERNEL") not in ("1", "true", "True"):
        logger.warning(
            f"Skipping IfcOpenShell kernel geometry on macOS for {product} (known native crash; "
            "set ADA_IFC_MACOS_KERNEL=1 to force)"
        )
        return None

    import ifcopenshell.geom

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_PYTHON_OPENCASCADE, True)
    settings.set(settings.USE_WORLD_COORDS, True)
    try:
        return ifcopenshell.geom.create_shape(settings, product).geometry
    except Exception as e:  # IfcOpenShell raises RuntimeError for unprocessable geometry
        logger.warning(f"IfcOpenShell kernel could not build geometry for {product}: {e}")
        return None


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

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import ifcopenshell.geom
from ifcopenshell.util.placement import get_local_placement

from ada import Shape
from ada.cadit.ifc.read.geom.geom_reader import get_product_definitions
from ada.cadit.ifc.read.geom.placement import placement_from_ifc_4x4
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
    blob_rec = None
    if Config().ifc_import_shape_geom or force_geom:
        geom, occ_body, blob_rec = _read_shape_geometry(product, color, ifc_store)

    extra_opts = {}
    # Only apply the IFC local placement when we keep the native (parametric) geometry, which is
    # expressed in the product's local coordinates. The kernel fallback below bakes world
    # coordinates into the OCC body (``USE_WORLD_COORDS``), so re-applying the placement there would
    # double-transform it. ``get_local_placement`` returns the full world 4x4 regardless of whether
    # the ObjectPlacement is relative (PlacementRelTo chain) or ABSOLUTE — an absolute placement can
    # still carry a non-identity rotation (e.g. an IfcBeam that fell through to the shape importer,
    # with its extrusion axis rotated into the world frame), so it must be applied too. Gating on
    # PlacementRelTo dropped those rotations and rendered the product on the wrong world axis.
    if occ_body is None:
        obj_placement = product.ObjectPlacement
        if obj_placement is not None:
            extra_opts["placement"] = placement_from_ifc_4x4(get_local_placement(obj_placement))

    common = dict(
        guid=product.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
        opacity=color.opacity if color is not None else 1.0,
        **extra_opts,
    )

    if (geom is not None or blob_rec is not None) and Config().cad_lazy_shape_store:
        # Lazy shape store (default on): keep the geometry as one compact blob and
        # mint a ShapeProxy that hydrates on demand — large IFC imports stop holding
        # every product's ada.geom tree. Python-native geometry pickles losslessly
        # (bool_operations, half-space operands, parametric profiles); products the
        # Python readers can't resolve keep the adacpp IfcNgeomStream NGEOM buffer
        # as-arrived (zero-copy, tessellation fast-path capable). Kernel-fallback
        # products (occ_body) stay eager Shapes: their geometry is the transient
        # OCC body, and there is nothing heavy retained to avoid.
        from ada.api.shapes import ShapeProxy, ShapeStore

        store = getattr(ifc_store, "_lazy_shape_store", None)
        if store is None:
            store = ShapeStore(compress=Config().cad_shape_store_compress)
            ifc_store._lazy_shape_store = store
        if geom is not None:
            idx = store.add_geometry(geom)
        else:
            blob, meta = blob_rec
            # meta.transforms is the composed world placement (column-major 16-float,
            # like the STEP path); a single instance becomes the Shape placement so
            # downstream behaves exactly like the eager IFC path (local geometry +
            # Placement). Multi-instance products were filtered out at lookup time.
            if len(meta.transforms) == 1:
                import numpy as np

                mat = np.asarray(meta.transforms[0], dtype=float).reshape(4, 4, order="F")
                common["placement"] = placement_from_ifc_4x4(mat)
            idx = store.add_blob(blob, gid=product.GlobalId, color=color)
        return ShapeProxy(name, store, idx, **common)

    shape = Shape(name, geom=geom, **common)

    # Assign the kernel-built OCC body to the transient cache explicitly rather than via
    # the constructor: the constructor only routes ``geom`` to ``_occ_cache`` when the
    # *active* backend recognises it as a shape handle, which is false under adacpp (the
    # body is always a pythonocc TopoDS from IfcOpenShell). The tessellator/exporters read
    # ``_occ_cache`` and adopt a foreign OCC body across the kernel boundary as needed.
    if occ_body is not None:
        shape._occ_cache = occ_body

    return shape


def _read_shape_geometry(product: ifcopenshell.entity_instance, color, ifc_store: IfcStore = None):
    """Resolve a product's body geometry, preferring adapy's native (parametric) reader.

    Returns ``(geom, occ_body, blob_rec)``: a native :class:`~ada.geom.Geometry` when
    every body item is a type adapy reads natively; else ``blob_rec = (ngeom_buffer,
    meta)`` from adacpp's dep-free ``IfcNgeomStream`` when it resolved the product
    (B-reps and analytic solids, kernel-free and lazily storable); else ``occ_body``,
    a world-placed OCC ``TopoDS`` built by the IfcOpenShell geometry kernel. The kernel
    fallback is what lets ``ifc.import_shape_geom`` be on by default: any IFC geometry
    representation still imports, just as a faceted B-rep rather than a parametric one.
    ``(None, None, None)`` only when no geometry can be produced at all (logged)."""
    if product.Representation is None:
        return None, None, None

    try:
        geometries = get_product_definitions(product)
    except NotImplementedError as e:
        logger.debug(f"native IFC geom reader unsupported for {product.is_a()}; using kernel fallback ({e})")
        geometries = []

    if len(geometries) > 1:
        # A product with several Body items (e.g. multiple IfcMappedItem instances) needs ALL of
        # them — one Shape carries one geometry, so taking geometries[0] would silently drop the
        # rest. The OCC kernel builds every item into one compound, so keep the kernel fallback for
        # multi-item products (rare) rather than losing geometry.
        logger.debug(f"product {product.is_a()} has {len(geometries)} Body geometries; using kernel fallback")
        geometries = []

    if geometries:
        geometry = geometries[0]
        # A boolean result (e.g. IfcBooleanClippingResult) already comes back as a Geometry
        # carrying its bool_operations — adopt the product's guid/color rather than re-wrap.
        if isinstance(geometry, Geometry):
            geometry.id = product.GlobalId
            geometry.color = color
            return geometry, None, None
        return Geometry(product.GlobalId, geometry, color), None, None

    # Only fall back to the kernel for products that carry a *Body* representation, i.e. an
    # actual solid/surface to render. Curve-only products (e.g. IfcAlignmentSegment, whose
    # representation is an "Axis") have no body geometry, and the IfcOpenShell kernel can
    # spin effectively forever evaluating such curves (IfcCurveSegment runs a 9000-iter
    # root-find per sample point) — an uninterruptible C++ hang that no Python timeout can
    # break. The native reader already restricts itself to "Body" items, so this keeps the
    # fallback aligned with it.
    if not _has_body_representation(product):
        return None, None, None

    # Between the Python-native readers and the OCC kernel: adacpp's dep-free native IFC
    # resolver (advanced/faceted B-reps + analytic solids the Python readers don't cover).
    blob_rec = _native_geom_blob(product, ifc_store)
    if blob_rec is not None:
        return None, None, blob_rec

    occ_body = _kernel_occ_shape(product)
    if occ_body is None:
        logger.warning(f"No geometry could be produced for product {product}")
    return None, occ_body, None


def _native_geom_blob(product: ifcopenshell.entity_instance, ifc_store: IfcStore):
    """``(ngeom_buffer, meta)`` for a product from adacpp's ``IfcNgeomStream``, or
    ``None``. The whole file is scanned ONCE per store on first need (guid-keyed map;
    buffers retained zero-copy as they arrive). Multi-instance (mapped-item) products
    are excluded — the lazy Shape path models one placement per Shape."""
    if ifc_store is None or not Config().cad_lazy_shape_store:
        return None
    path = getattr(ifc_store, "ifc_file_path", None)
    if path is None:
        return None
    cache = getattr(ifc_store, "_native_geom_blobs", None)
    if cache is None:
        cache = {}
        try:
            import adacpp

            stream = adacpp.cad.IfcNgeomStream(str(path))
            for blob, meta in stream:
                if meta.guid and len(meta.transforms) <= 1:
                    cache[meta.guid] = (blob, meta)
        except (ImportError, AttributeError):
            pass  # no adacpp / build predates IfcNgeomStream
        except Exception as exc:  # noqa: BLE001 - a native scan failure must not break the import
            logger.warning("native IFC NGEOM scan failed (%s); using the kernel fallback", exc)
            cache = {}
        ifc_store._native_geom_blobs = cache
    return cache.get(product.GlobalId)


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
        extra_opts["placement"] = placement_from_ifc_4x4(get_local_placement(obj_placement))

    # A sphere-bodied product marked MassPoint reads back as MassPoint (mass from properties).
    if product.ObjectType == "MassPoint":
        from ada.api.mass import MassPoint

        from .reader_utils import get_ifc_property_sets

        mass = get_ifc_property_sets(product).get("Properties", {}).get("mass", 0.0)
        return MassPoint(
            name,
            center,
            float(mass),
            radius=float(sphere.Radius),
            **{k: v for k, v in extra_opts.items() if k == "placement"},
        )

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

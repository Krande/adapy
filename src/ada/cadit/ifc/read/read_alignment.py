"""Import IFC4x3 alignment-family products (IfcAlignment, IfcAlignmentSegment, IfcReferent, ...)
whose geometry is curve representations — 'Axis' (Curve3D), 'FootPrint' (Curve2D) or 'Segment'.

These carry no Body/solid, so the generic shape importer skips them and the IfcOpenShell geometry
kernel can hang on their IfcCurveSegments (a 9000-iter root-find per sample). Instead we read the
analytic ada.geom curve (IfcSegmentedReferenceCurve / IfcGradientCurve / IfcCompositeCurve /
IfcCurveSegment with line/arc/clothoid/cosine-spiral parents), evaluate it to a sampled 3D polyline
with the kernel-free alignment evaluator (validated to ~1e-6 vs the ifcopenshell oracle), and mint a
Shape carrying that PolyLine — which renders as GL_LINES on every backend (no OCC)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada import Shape
from ada.cadit.ifc.read.geom.curves import get_curve
from ada.cadit.ifc.read.read_color import get_product_color
from ada.cadit.ngeom._alignment_sweep import curve_to_polyline
from ada.config import logger
from ada.geom import Geometry
from ada.geom.curves import PolyLine
from ada.geom.points import Point

if TYPE_CHECKING:
    import ifcopenshell

    from ada.cadit.ifc.store import IfcStore

# Representation identifiers we can evaluate, most-preferred first: the 3D reference curve is the
# primary geometry; the 2D footprint and per-segment bodies are fallbacks.
_CURVE_REP_PRIORITY = ("Axis", "Segment", "FootPrint", "Curve3D", "Reference")


def is_alignment_curve_product(product: ifcopenshell.entity_instance) -> bool:
    """True if the product carries a curve (Axis/FootPrint/Segment) representation the alignment
    reader can evaluate — i.e. an alignment-family product with geometry but no Body."""
    rep = getattr(product, "Representation", None)
    if rep is None:
        return False
    return any(_curve_items(r) for r in rep.Representations)


def _dedupe_consecutive(pts, tol: float = 1e-7) -> list[Point]:
    """Drop consecutive coincident points (zero-length edges) from a sampled polyline — they arise
    at the boundaries between adjacent curve segments and would make wire-based exporters fail."""
    out: list[Point] = []
    for p in pts:
        q = Point(float(p[0]), float(p[1]), float(p[2]))
        if not out or float(np.linalg.norm(q - out[-1])) > tol:
            out.append(q)
    return out


def _curve_items(representation) -> list:
    """The representation's items if it is a curve representation we handle, else []."""
    ident = getattr(representation, "RepresentationIdentifier", None)
    if ident not in _CURVE_REP_PRIORITY:
        return []
    return list(representation.Items or [])


def import_ifc_alignment(product: ifcopenshell.entity_instance, name, ifc_store: IfcStore) -> Shape | None:
    """Evaluate the product's preferred curve representation to a Shape carrying a 3D polyline.

    Prefers the 3D 'Axis' reference curve (with cant, for IfcSegmentedReferenceCurve); falls back
    to a 'Segment' or 'FootPrint' representation. Returns ``None`` if no representation evaluates
    (logged), so the caller can fall through / skip."""
    rep = getattr(product, "Representation", None)
    if rep is None:
        return None

    # Pick the highest-priority curve representation present.
    by_ident = {}
    for r in rep.Representations:
        if _curve_items(r):
            by_ident.setdefault(r.RepresentationIdentifier, r)
    chosen = next((by_ident[i] for i in _CURVE_REP_PRIORITY if i in by_ident), None)
    if chosen is None:
        return None

    points: list[Point] = []
    for item in chosen.Items:
        try:
            curve = get_curve(item)
            pts = curve_to_polyline(curve)
        except NotImplementedError as exc:
            logger.debug(f"alignment curve {item.is_a()} on {name!r} not evaluable natively ({exc})")
            continue
        except Exception as exc:  # noqa: BLE001 - a single bad item must not drop the whole product
            logger.warning(f"alignment curve eval failed for {name!r} ({item.is_a()}): {exc}")
            continue
        points.extend(_dedupe_consecutive(pts))

    points = _dedupe_consecutive(points)
    if len(points) < 2:
        # A curve that collapses to a single point in its own parametric space (e.g. an isolated
        # constant vertical/cant segment) has no line to draw — skip it. Dropping degenerate
        # polylines also keeps them out of the wire-based STEP/IFC exporters (which raise on
        # zero-length edges).
        logger.info(f'alignment product "{name}" ({product.is_a()}) produced no evaluable curve geometry')
        return None

    color = get_product_color(product, ifc_store.f)
    geom = Geometry(product.GlobalId, PolyLine(points=points), color)
    return Shape(
        name,
        geom=geom,
        guid=product.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
        opacity=color.opacity if color is not None else 1.0,
    )

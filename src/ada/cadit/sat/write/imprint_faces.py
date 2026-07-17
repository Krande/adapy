"""Imprint beam axes onto plate faces before they are welded into the SAT body.

Genie imprints beams onto plate faces on *import* (a stiffener lying on a panel
splits the panel along its axis). Its own export is already imprinted — a plate
carrying stiffeners arrives as many sub-faces, and every beam carries a
``sat_reference`` naming the edge it lies on, so the importer reuses that edge
instead of re-imprinting. The un-imprinted curved-weld path emits one monolithic
face per plate and no beam edge refs, so Genie re-imprints on import, relinks a
face edge, and raises ``ACIS 21013 - attempt to relink other than vertex or
wire``.

This module pre-splits the faces the same way, for flat *and* curved plates, via
OCC General Fuse (``BOPAlgo_Builder``). It fuses **one plate at a time** against
only the beams whose bounding box meets it: the plate-to-plate sharing is
recovered downstream by the weld (position + curve), so the fuse only has to cut
each plate along its own beams. Per-plate keeps every fuse tiny and robust — a
single ill-conditioned plate falls back to its monolithic face instead of a
whole-model fuse erroring and dropping every imprint. It also reports, per beam,
the edges that beam became so the caller can name them and emit the matching
``sat_reference`` — both halves are needed; pre-split faces alone still relink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.geom import Point
    from ada.geom.surfaces import AdvancedFace


@dataclass
class FaceImprint:
    """Result of imprinting plate faces against beam axes.

    ``sub_faces`` is aligned with the input faces: entry ``i`` is the list of
    sub-faces plate ``i`` became, or ``None`` when the plate was not imprinted
    (no beam touched it, or the fuse/conversion failed) — author it monolithically.

    ``beam_edges`` is aligned with the input curves: entry ``j`` is the list of
    ``(start, end)`` endpoint pairs of the face-bounding edges beam ``j`` became.
    """

    sub_faces: "list[list[AdvancedFace] | None]"
    beam_edges: "list[list[tuple[Point, Point]]]" = field(default_factory=list)


def imprint_advanced_faces(
    advanced_faces: "list[AdvancedFace]",
    imprint_curves: "list[list[tuple[float, float, float]]]",
    tolerance: float = 1e-6,
) -> "FaceImprint | None":
    """Split each face in ``advanced_faces`` along the beams touching it and report both the
    sub-faces and the edges each beam became. ``None`` if no backend can imprint (caller keeps
    the un-imprinted behaviour).

    The OCC General-Fuse implementation lives in ``ada.occ.imprint_faces_occ`` and is reached
    through the CAD backend (``CadBackend.imprint_advanced_faces``) — this module imports no OCC.
    The imprint is an OCC operation, so we try the active backend first and, if it can't imprint
    (the adacpp backend has no native imprint binding yet), fall back to the OCC backend whenever
    pythonocc is installed — preserving the curved-plate imprint regardless of the default backend.
    """
    from ada.cad import active_backend, select_backend

    def _try(be) -> "tuple[list, list] | None":
        fn = getattr(be, "imprint_advanced_faces", None)
        if fn is None:
            return None
        try:
            return fn(advanced_faces, imprint_curves, tolerance)
        except NotImplementedError:
            return None
        except Exception as e:  # noqa: BLE001 - a backend imprint failure must not fail the export
            logger.warning(f"imprint_advanced_faces: backend imprint raised ({e}); plates left un-imprinted")
            return None

    be = active_backend()
    result = _try(be)
    if result is None and getattr(be, "name", "") != "pythonocc-core":
        # Active backend can't imprint (e.g. adacpp): use the OCC backend directly if available.
        try:
            occ_be = select_backend("occ")
        except Exception:  # noqa: BLE001 - no pythonocc in this environment
            occ_be = None
        if occ_be is not None:
            result = _try(occ_be)
    if result is None:
        logger.warning("imprint_advanced_faces: no backend could imprint; plates left un-imprinted")
        return None
    sub_faces, beam_edges = result
    return FaceImprint(sub_faces=sub_faces, beam_edges=beam_edges)

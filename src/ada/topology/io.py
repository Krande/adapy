"""Ingest pipelines: build a CellGraph from adapy models / IFC / section lofts.

These reuse adapy's own importers (``ada.from_ifc``) and CAD backend rather than
re-parsing or re-tessellating geometry. Because the imported geometry is B-rep
(not a triangle soup), the cells' faces are already clean — no coplanar-face
simplification step is needed, unlike a tessellation-based pipeline.

``from_section_loft`` (Phase 1 spike) derives a *lossless* cell decomposition of a
loft directly from its ORDERED SECTION PROFILES: each inter-station swept band
becomes one cell, so ``N`` stations yield ``N-1`` band cells per member. The union
of the bands reproduces the loft solid exactly (a partition). It stays
backend-neutral — every kernel op routes through ``ada.cad`` / ``ada.api.loft``
verbs — so no ``OCC.Core`` import lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

import numpy as np

from ada.topology.graph import CellGraph
from ada.topology.metadata import TopologyMetadata

if TYPE_CHECKING:
    from ada.cad import ShapeHandle
    from ada.geom.curves import PolyLoop


def from_part(part) -> CellGraph:
    """Build a CellGraph from the ``PrimBox`` objects in an assembly/part."""
    import ada

    return CellGraph.from_prim_boxes(list(part.get_all_physical_objects(by_type=ada.PrimBox)))


def from_assembly(assembly, ifc_types=("IfcSpace", "IfcRoom", "IfcZone")) -> CellGraph:
    """Build a CellGraph from the B-rep solids of an imported assembly.

    Each physical shape becomes a cell solid; abutting cells are merged so they
    share faces. ``ifc_types`` filters by the original IFC entity type (resolved
    via the assembly's ifc_store); pass ``None`` to accept every shape. Each
    object's property sets + IFC type are carried onto its ``TopologyMetadata``.
    """
    import ada

    store = getattr(assembly, "ifc_store", None)
    pairs: list[tuple] = []
    for obj in assembly.get_all_physical_objects(by_type=ada.Shape):
        ifc_type = None
        if store is not None and getattr(obj, "guid", None):
            try:
                ifc_type = store.get_by_guid(obj.guid).is_a()
            except Exception:
                ifc_type = None
        if ifc_types is not None and ifc_type not in ifc_types:
            continue
        try:
            solid = obj.solid_occ()
        except Exception:
            # No usable geometry on this shape (e.g. geometry import disabled).
            continue
        if solid is None:
            continue
        props: dict = {}
        for pset in (obj.metadata or {}).values():
            if isinstance(pset, dict):
                props.update(pset)
        if ifc_type:
            props["IFC_type"] = ifc_type
        pairs.append((solid, TopologyMetadata(name=obj.name, properties=props)))

    if not pairs:
        raise ValueError(
            "from_assembly: no matching shape solids found (check ifc_types, or that geometry was imported)"
        )
    return CellGraph.from_cell_solids(pairs, merge=True)


def from_ifc(ifc_file, ifc_types=("IfcSpace", "IfcRoom", "IfcZone")) -> CellGraph:
    """Build a CellGraph from an IFC file via adapy's own importer.

    No ifcopenshell re-parse or re-tessellation: ``ada.from_ifc`` yields B-rep
    shapes whose solids are the cells. By default only space-like entities are
    used (``ifc_types``); pass ``None`` to use every shape.
    """
    import ada
    from ada.config import Config

    # adapy only attaches solid geometry to imported shapes when this is on
    # (read at IFC-read time), so obj.solid_occ() works downstream.
    Config().ifc_import_shape_geom = True
    assembly = ada.from_ifc(ifc_file)
    return from_assembly(assembly, ifc_types=ifc_types)


# --------------------------------------------------------------------------------------
# Section-loft ingest (Phase 1 spike — lands the deferred ``from_section_loft`` hook)
# --------------------------------------------------------------------------------------


@dataclass
class LoftMember:
    """One lofted member fed to :func:`from_section_loft`.

    ``profiles`` are the ordered, closed section :class:`~ada.geom.curves.PolyLoop`\\ s
    (in the member's own frame). Consecutive profiles bound one swept band, so
    ``len(profiles)`` stations produce ``len(profiles) - 1`` band cells.

    ``placement`` is an optional 4x4 affine applied to the derived band solids after
    they are built in the member's local frame (mirrored/rotated/translated members,
    e.g. a floater's eight legs). ``None`` leaves the cells in world coordinates.
    """

    name: str
    profiles: "list[PolyLoop]"
    placement: "np.ndarray | None" = field(default=None)


def _station_centroids(profiles: "Sequence[PolyLoop]") -> "list[np.ndarray]":
    out = []
    for prof in profiles:
        pts = np.asarray([(float(p.x), float(p.y), float(p.z)) for p in prof.polygon], dtype=float)
        out.append(pts.mean(axis=0))
    return out


def _spine_param(point: "np.ndarray", station_centroids: "list[np.ndarray]") -> float:
    """Arc-length position of ``point`` projected onto the station-centroid polyline.

    Used only to *order* the band cells along the member's spine so each band gets the
    correct ``station_lo``/``station_hi``. Robust for a monotone spine (the usual
    stacked-section member); a member that folds back on itself would need a richer
    band-to-segment match (see the TODO in :func:`from_section_loft`).
    """
    best_dist = None
    best_param = 0.0
    acc = 0.0
    for a, b in zip(station_centroids[:-1], station_centroids[1:]):
        seg = b - a
        seg_len = float(np.linalg.norm(seg))
        if seg_len == 0.0:
            continue
        t = float(np.clip(np.dot(point - a, seg) / (seg_len * seg_len), 0.0, 1.0))
        proj = a + t * seg
        dist = float(np.linalg.norm(proj - point))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_param = acc + t * seg_len
        acc += seg_len
    return best_param


def _member_band_solids(
    member: LoftMember, ruled: bool, tolerance: float
) -> "list[tuple[ShapeHandle, TopologyMetadata]]":
    """Partition ONE lofted member into inter-station band solids + metadata.

    Mechanism (design "Derivation (lossless partition)"): loft the profiles to a swept
    solid, take the solid's own outer faces plus a planar divider face for each interior
    station, and hand the face soup to ``make_volumes_from_faces`` — which imprints the
    dividers into the sides and returns one solid per band. The bands are then ordered
    along the spine so their ``station_lo``/``station_hi`` metadata is correct.
    """
    from ada.api.loft import loft_profiles, planar_face_from_poly_loop
    from ada.cad import active_backend

    profiles = list(member.profiles)
    if len(profiles) < 2:
        raise ValueError(f"LoftMember '{member.name}' needs >= 2 section profiles, got {len(profiles)}")

    be = active_backend()
    solid = loft_profiles(profiles, ruled=ruled, is_solid=True)

    # Interior stations (exclude the two end caps) become divider faces; with only two
    # stations there is no interior divider and the whole solid is the single band.
    dividers = [planar_face_from_poly_loop(prof) for prof in profiles[1:-1]]
    face_soup = list(be.faces(solid)) + dividers
    band_solids = be.make_volumes_from_faces(face_soup, tolerance=tolerance)

    n_expected = len(profiles) - 1
    if len(band_solids) != n_expected:
        raise ValueError(
            f"LoftMember '{member.name}': expected {n_expected} band cells "
            f"({len(profiles)} stations), got {len(band_solids)} from make_volumes_from_faces"
        )

    # Order the bands along the member spine so station indexing is correct regardless of
    # the kernel's volume-enumeration order.
    station_centroids = _station_centroids(profiles)
    ordered = sorted(
        band_solids,
        key=lambda s: _spine_param(np.asarray(be.center_of_mass(s), dtype=float), station_centroids),
    )

    placement = member.placement
    pairs: list[tuple] = []
    for lo, band in enumerate(ordered):
        if placement is not None:
            band = be.transform(band, np.asarray(placement, dtype=float), True)
        meta = TopologyMetadata(
            name=f"{member.name}_bay{lo}",
            properties={"member": member.name, "station_lo": lo, "station_hi": lo + 1},
        )
        pairs.append((band, meta))
    return pairs


def from_section_loft(
    members: "Sequence[LoftMember]",
    ruled: bool = True,
    merge: bool = False,
    tolerance: float = 1e-6,
) -> CellGraph:
    """Build a :class:`CellGraph` of inter-station swept BANDS from lofted members.

    This is the ``ada.topology.io`` hook the design reserved for lofts. Each member's
    ordered section profiles are swept and partitioned into ``stations - 1`` band cells
    (see :func:`_member_band_solids`); the union of a member's bands reproduces its loft
    solid exactly, so the decomposition is lossless. Every band carries
    ``TopologyMetadata`` with ``member`` / ``station_lo`` / ``station_hi``.

    A single member is the common case; passing several members unions their bands into
    one graph (each member's ``placement`` is applied to its band solids first). With
    ``merge=False`` (default) the bands are kept as-is — a member's bands already share
    faces (they came out of ``make_volumes_from_faces``); pass ``merge=True`` only when
    distinct members physically abut and their shared interface should collapse to one
    face.

    TODO(phase-2): a member whose spine folds back on itself would defeat the monotone
    spine ordering in :func:`_spine_param`; and non-axis loft faces still fall into the
    ``stable_face_id`` overflow bucket (design risk #1), so loft cells are read-mostly
    until a loft-native face id lands.
    """
    if not members:
        raise ValueError("from_section_loft: no members given")

    pairs: list[tuple] = []
    for member in members:
        pairs.extend(_member_band_solids(member, ruled=ruled, tolerance=tolerance))

    return CellGraph.from_cell_solids(pairs, merge=merge)

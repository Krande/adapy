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

from ada.topology.graph import (
    CellGraph,
    _round_key,
    classify_loft_face,
    loft_face_id_str,
)
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

    # The band solids feed ``CellGraph.from_cell_solids``, whose GraphCell/GraphFace/
    # GraphEdge readers (ada.topology.graph) + extraction all read the solid handles via
    # ``active_backend()``. A shape can only be read by the backend that built it, so the
    # band-solid path stays on the active backend — like ``loft_member_to_part``. Band
    # cells are topology-only (a cell decomposition for selection); they carry no
    # Plate/PlateCurved distinction, so the curved-corner parity handling in
    # ``loft_member_to_part`` does not apply to them.
    be = active_backend()
    solid = loft_profiles(profiles, ruled=ruled, is_solid=True, backend=be)

    # Interior stations (exclude the two end caps) become divider faces; with only two
    # stations there is no interior divider and the whole solid is the single band.
    dividers = [planar_face_from_poly_loop(prof, backend=be) for prof in profiles[1:-1]]
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
    # Station profile points in the placed (world) frame — the same transform the
    # band solids get — so band-cell faces can be matched to a profile edge/cap by
    # vertex incidence (see assign_loft_face_ids). Not persisted (runtime only).
    placed_profiles = [_placed_profile_points(prof, placement) for prof in profiles]

    pairs: list[tuple] = []
    for lo, band in enumerate(ordered):
        if placement is not None:
            band = be.transform(band, np.asarray(placement, dtype=float), True)
        meta = TopologyMetadata(
            name=f"{member.name}_bay{lo}",
            properties={
                "member": member.name,
                "station_lo": lo,
                "station_hi": lo + 1,
                "profile_lo_pts": placed_profiles[lo],
                "profile_hi_pts": placed_profiles[lo + 1],
            },
        )
        pairs.append((band, meta))
    return pairs


def _placed_profile_points(prof: "PolyLoop", placement: "np.ndarray | None") -> "list[tuple[float, float, float]]":
    """The profile's polygon points as world-frame ``(x, y, z)`` tuples (``placement``
    applied when set — matching the transform the band solids receive)."""
    pts = [(float(p.x), float(p.y), float(p.z)) for p in prof.polygon]
    if placement is None:
        return pts
    mat = np.asarray(placement, dtype=float)
    out = []
    for x, y, z in pts:
        v = mat @ np.asarray([x, y, z, 1.0], dtype=float)
        out.append((float(v[0]), float(v[1]), float(v[2])))
    return out


def assign_loft_face_ids(cell_graph: CellGraph) -> None:
    """Stamp a loft-native ``loft_face_id`` onto every band-cell face (Phase 3b).

    For each cell carrying loft metadata (``member`` + the two station profiles), match
    each of its faces to a profile edge / end cap by vertex incidence
    (:func:`ada.topology.graph.classify_loft_face`) and record the canonical id. Box
    cells carry no such metadata and are left entirely untouched — ``stable_face_id`` is
    unaffected. Faces that fail to match keep ``loft_face_id = None``.
    """
    for cell in cell_graph.cells:
        md = cell.metadata
        member = md.get("member")
        lo_pts = md.get("profile_lo_pts")
        hi_pts = md.get("profile_hi_pts")
        bay = md.get("station_lo")
        if member is None or lo_pts is None or hi_pts is None or bay is None:
            continue
        profile_keys = [[_round_key(p) for p in lo_pts], [_round_key(p) for p in hi_pts]]
        for face in cell.faces:
            fkeys = {_round_key(p) for p in face.get_points()}
            cls = classify_loft_face(fkeys, profile_keys)
            if cls is None:
                continue
            kind, _bay_local, edge = cls
            face.loft_face_id = loft_face_id_str(member, bay, kind, edge)


def loft_member_to_part(
    name: str,
    profiles: "Sequence[PolyLoop]",
    thickness: float = 0.01,
    ruled: bool = True,
    reverse_winding: bool = True,
    exclude_faces: "Sequence[str] | None" = None,
):
    """Loft ``profiles`` to plates, naming each plate by its loft-native face id and
    dropping the plates whose face is excluded (Phase 3b).

    Face-id-aware sibling of :func:`ada.api.loft.loft_to_part`: each face of the
    member's swept solid is classified to ``(kind, bay, edge)`` against the same
    ordered station profiles the band cells use, so a plate's name equals the
    ``loft_face_id`` of the corresponding band-cell face (letting the frontend map a
    picked plate straight to a cell face). ``exclude_faces`` holds member-relative ids
    (e.g. ``"bay0:edge2"``, ``"bay0:cap_lo"``); the matching plates are omitted.
    """
    from ada.api.loft import loft_profiles
    from ada.api.plates.base_pl import Plate, PlateCurved
    from ada.api.spatial.part import Part
    from ada.cad import active_backend
    from ada.geom import Geometry

    # Run the WHOLE rendered-plate path (swept solid + every face op) on the active
    # backend, same as the band-solid path — a shape can only be read by the backend
    # that built it. Curved-corner parity needs the ``is_planar_face`` +
    # ``face_to_advanced_face`` verbs to keep the ruled corner-transition panels as
    # PlateCurved instead of flattening them (~0.18 wider); OCC and adacpp >=0.20 both
    # ship them natively. A backend without ``is_planar_face`` (pyodide/wasm) can't
    # classify the faces and falls back to flat plates below (the compile still
    # succeeds; rounded corners render very slightly wider).
    be = active_backend()
    exclude = set(exclude_faces or [])
    prefix_len = len(name) + 1  # strip "{name}:" to get the member-relative id
    shape = loft_profiles(profiles, ruled=ruled, is_solid=True, backend=be)
    profile_keys = [[_round_key((p.x, p.y, p.z)) for p in prof.polygon] for prof in profiles]

    plates = []
    # Iterate the swept solid's OCC faces directly (not just their poly loops):
    # a face's *surface* type decides whether it stays a flat ``Plate`` or must
    # keep its curvature as a ``PlateCurved``. ``BRepOffsetAPI_ThruSections`` with
    # ``ruled=True`` emits planar side panels between matching straight edges, but
    # B-spline ruled panels for the corner transitions between a sharp profile and
    # a rounded (CORNER_RADIUS) one. Flattening those bulges the member outward
    # (~0.18 for a col_d=10 / r=2.5 floater column), so we mirror the loft-tool's
    # ``loft_shape_to_plates``: planar → Plate, B-spline → PlateCurved.
    for i, face in enumerate(be.faces(shape)):
        wires = be.wires(face)
        if not wires:
            continue
        loop_pts = be.wire_points(wires[0])
        if not loop_pts:
            continue
        fkeys = {_round_key(p) for p in loop_pts}
        cls = classify_loft_face(fkeys, profile_keys)
        if cls is None:
            # Unclassified face: keep it (fail-safe) under a generic, non-excludable name.
            fid = f"{name}:face{i}"
            rel = None
        else:
            kind, bay, edge = cls
            fid = loft_face_id_str(name, bay, kind, edge)
            rel = fid[prefix_len:]
        if rel is not None and rel in exclude:
            continue

        plate = None
        # ``ThruSections`` stores even the flat side / taper / cap panels as
        # B-spline surfaces, so a surface-*type* check would wrongly curve them
        # (and change all-sharp box/jacket members). Probe the actual geometry:
        # only the genuinely-ruled corner-transition panels are non-planar.
        # A backend without the planarity probe (e.g. the pyodide/wasm kernel, which
        # ships neither verb) can't tell — treat every face as planar so the compile
        # still SUCCEEDS (flat plates; the curved corners render very slightly wider)
        # rather than erroring. Curved parity needs both ``is_planar_face`` and
        # ``face_to_advanced_face`` on the backend.
        try:
            face_is_curved = not be.is_planar_face(face)
        except NotImplementedError:
            face_is_curved = False
        if face_is_curved:
            # Ruled corner-transition panel — preserve the true surface. Go through
            # the ada.geom AdvancedFace so the PlateCurved is fully IFC-compatible
            # (same path the gxml importer and the loft-tool face_to_plate use).
            try:
                ada_face = be.face_to_advanced_face(face)
                if ada_face is not None:
                    plate = PlateCurved(fid, Geometry(id=fid, geometry=ada_face), thickness)
            except Exception as exc:  # noqa: BLE001 — defensive; fall back to a flat plate
                from ada.config import logger

                logger.debug("loft face %r curved-plate construction failed: %s", fid, exc)
        if plate is None:
            # Planar face (box/jacket sharp side walls, caps) — the flat plate is
            # exact. All-sharp members stay byte-identical to the pre-curved path.
            pts = list(loop_pts)
            if reverse_winding:
                pts.reverse()
            plate = Plate.from_3d_points(fid, pts, thickness)
        plates.append(plate)

    part = Part(name)
    part /= plates
    return part


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

    Each band-cell face additionally carries a loft-native ``loft_face_id`` (Phase 3b,
    :func:`assign_loft_face_ids`) so individual side panels / caps are addressable for
    per-face selection and face-exclude — the box ``stable_face_id`` path is untouched.

    TODO(phase-2): a member whose spine folds back on itself would defeat the monotone
    spine ordering in :func:`_spine_param`.
    TODO(phase-3b): rectangular openings authored on a loft face id are deferred — cutting
    a parametric hole in a possibly-ruled swept panel is real geometry work; the face-id
    + exclude ops land here, the opening does not.
    """
    if not members:
        raise ValueError("from_section_loft: no members given")

    pairs: list[tuple] = []
    for member in members:
        pairs.extend(_member_band_solids(member, ruled=ruled, tolerance=tolerance))

    cg = CellGraph.from_cell_solids(pairs, merge=merge)
    # Phase 3b: give each band-cell face a stable, loft-native id (member/bay/edge or
    # cap). Additive — box cells carry no loft metadata and are untouched.
    assign_loft_face_ids(cg)
    return cg

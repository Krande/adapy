from __future__ import annotations

import pathlib
from collections import Counter
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING

from ada.base.types import GeomRepr
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.sat_entities import SATEntity
from ada.cadit.sat.write.utils import IDGenerator
from ada.cadit.sat.write.write_plate import plate_to_sat_entities
from ada.config import logger

if TYPE_CHECKING:
    from ada import Assembly, Part, Plate
    from ada.api.plates import PlateCurved

HEADER_STR = """2000 0 1 0
18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Tue Jan 17 20:39:08 2023
1000 9.9999999999999995e-07 1e-10
"""


def part_to_sat_writer(part: Part | Assembly, imprint: bool = True) -> SatWriter:
    """Build the ACIS SAT body for every :class:`~ada.Plate` under ``part``.

    Every plate face lives in ONE body / lump / shell, matching what Genie
    itself writes (verified against ``files/sat_files/flat_plate*_sesam_*.sat``).
    A shell records a single face pointer, so the remaining faces are reached by
    walking ``next_face`` — hence the chaining pass at the end.

    With ``imprint`` (the default) the plates are mutually split and welded by
    the CAD backend first, so touching plates share vertices and edges and a
    plate crossed by its neighbours resolves to several faces — the form Genie
    writes, and the one it otherwise has to reconstruct on import. Set it False
    for the unfused one-face-per-plate body (no CAD backend needed).

    A :class:`~ada.api.plates.PlateCurved` becomes a spline face (or a plane
    face with curved edges) and is always unfused: the imprint splits *planar*
    outlines and has nothing to say about a B-spline patch.
    """
    from ada import Plate
    from ada.api.plates import PlateCurved

    # A Part read from a Genie concept XML carries the source body as a neutral
    # connectivity store. Exporting through it reproduces the source topology
    # exactly (1 lump, every shared edge present) — so beams resolve to real
    # named edges and Genie need not re-imprint. Falls through to the weld when
    # no store is attached (adapy-authored geometry, or import without the store).
    store = getattr(part, "_topology_store", None)
    if store is not None:
        from ada.cadit.sat.write.from_brep_part import part_store_to_sat_writer

        return part_store_to_sat_writer(part, store)

    sw = SatWriter(part)

    # Only plates become faces — Genie imports beams from the concept XML alone
    # and its own SAT carries no beam bodies. Beams still take part in the
    # imprint though (see _add_imprinted_plates).
    plates = list(part.get_all_physical_objects(by_type=Plate))
    curved = list(part.get_all_physical_objects(by_type=PlateCurved))
    if not plates and not curved:
        # No faces means no body: a body/lump/shell with nothing in it is not a
        # meaningful ACIS record, and `is_empty` lets the caller drop the whole
        # <geometry> element (a beams-only model simply has no SAT).
        return sw

    _, _, shell = sw.init_body(plates, curved)

    if curved:
        # A body has one topology, and the imprint can only build a planar one.
        # Where curved faces are present the flat plates join their weld
        # instead, so the two share vertices and edges where they meet — built
        # apart they leave a coincident copy of every shared corner, which ACIS
        # rejects as "duplicate vertex". They forgo the imprint's splitting to
        # get it; on a hull export that costs nothing (the imprint returns the
        # 17 flat plates as 17 faces, having split none of them).
        _add_curved_plates(sw, curved, plates)
    elif plates:
        if imprint:
            _add_imprinted_plates(sw, plates)
        else:
            _add_plates_unfused(sw, plates)

    _assign_faces_to_shells(sw, shell)

    sw.renumber()
    return sw


def _face_components(faces: list[se.Face]) -> list[list[se.Face]]:
    """Group faces into the connected sets they form.

    Joined by a shared *vertex*, not merely a shared edge: Genie's own exports
    put two plates meeting at a single corner in one lump
    (``flat_plate_x2_sesam_10x10_shared_vertex.sat``) and two that touch nowhere
    in two (``..._offset_no_shared.sat``). A vertex is the weaker claim and the
    one that matches.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Keyed on where a vertex IS, not which object it is: the unfused path mints
    # a vertex per face, so two plates meeting at a corner hold distinct objects
    # at one position and would read as disconnected.
    faces_at_vertex: dict[tuple, list[se.Face]] = {}
    for face in faces:
        find(id(face))
        loop = face.loop
        while loop is not None:
            coedge = loop.coedge
            seen = set()
            while coedge is not None and id(coedge) not in seen:
                seen.add(id(coedge))
                edge = coedge.edge
                if edge is not None:
                    for vertex in (edge.vertex_start, edge.vertex_end):
                        if vertex is not None:
                            key = tuple(round(float(c), 7) for c in vertex.point.point)
                            faces_at_vertex.setdefault(key, []).append(face)
                coedge = coedge.next_coedge
            loop = loop.next_loop

    for sharing in faces_at_vertex.values():
        for other in sharing[1:]:
            union(id(sharing[0]), id(other))

    grouped: dict[int, list[se.Face]] = {}
    for face in faces:
        grouped.setdefault(find(id(face)), []).append(face)
    # ordered by the first face of each, so the layout is stable between runs
    order = {id(f): i for i, f in enumerate(faces)}
    return sorted(grouped.values(), key=lambda g: order[id(g[0])])


def _assign_faces_to_shells(sw: SatWriter, shell: se.Shell) -> None:
    """One lump and shell per connected set of faces, as Genie writes them.

    A shell is a connected sheet, and ACIS says so — a shell holding faces that
    cannot be reached from one another fails verification with "entities in
    shell are not connected". A body carries the disjoint pieces as separate
    lumps instead, chained off the body: a hull export splits its 5470 faces
    into two lumps of 2910 and 2560.

    Within a shell the faces chain through ``next_face``, since a shell records
    only the first.
    """
    faces = sw.get_entities_by_type(se.Face)
    if not faces:
        return

    components = _face_components(faces)
    lumps: list[se.Lump] = []
    for i, group in enumerate(components):
        if i == 0:
            lump, group_shell = sw.lump, shell
        else:
            lump = se.Lump(sw.id_generator.next_id(), None, sw.body, list(sw.bbox))
            group_shell = se.Shell(sw.id_generator.next_id(), None, lump, list(sw.bbox))
            lump.shell = group_shell
            sw.add_entity(lump)
            sw.add_entity(group_shell)
        group_shell.face = group[0]
        for face in group:
            face.shell = group_shell
            face.next_face = None
        for cur, nxt in zip(group, group[1:]):
            cur.next_face = nxt
        lumps.append(lump)

    for cur, nxt in zip(lumps, lumps[1:]):
        cur.next_lump = nxt
    sw.body.lump = lumps[0]

    if len(lumps) > 1:
        logger.info(f"sat-write: {len(faces)} faces form {len(lumps)} disconnected lumps")


def _add_plates_unfused(sw: SatWriter, plates: list[Plate]) -> None:
    """One independent face per plate — no shared topology."""
    for face_id, pl in enumerate(plates, start=1):
        face_name = f"FACE{face_id:08d}"
        sw.face_map[pl.guid] = [face_name]
        for entity in plate_to_sat_entities(pl, face_name, GeomRepr.SHELL, sw):
            sw.add_entity(entity)


def _add_curved_plates(sw: SatWriter, curved: list[PlateCurved], plates: list[Plate] = ()) -> None:
    """One face per plate, curved and flat alike, welded into one topology.

    Neighbouring faces share their vertices and edges rather than minting
    coincident copies — ACIS rejects those as "duplicate vertex" — and the
    coedges on each shared edge are joined into a partner ring.

    The curved plates go first: their edges carry the parameter range the file
    authored, and a flat plate's straight edge has none to contribute, so
    whichever builds an edge first decides its range. The other way round a
    shared edge would be written 0..length and every curved face on it would
    disagree.

    A plate whose geometry this writer cannot author is skipped and logged
    rather than dropped silently — it leaves no entry in ``face_map``, so the
    gxml writer falls back to its boundary polygon for that plate alone.
    """
    from ada.cadit.sat.write.write_curved_plate import (
        TopologyWeld,
        UnsupportedCurvedFace,
        advanced_face_to_sat_entities,
        curved_plate_to_sat_entities,
        flat_plate_to_advanced_face,
        link_partner_rings,
        name_curved_beam_edges,
        name_imprinted_beam_edges,
        name_straight_beam_edges,
    )

    if not curved:
        return

    from ada.api.plates import PlateCurved
    from ada.cadit.sat.write.imprint_faces import imprint_advanced_faces

    weld = TopologyWeld(sw.id_generator)
    face_id = len(sw.get_entities_by_type(se.Face)) + 1
    skipped = Counter()

    # Build one AdvancedFace per plate. Curved plates carry the surface the file
    # authored; flat plates become a plane face. Curved go first so a shared edge
    # takes the curved face's parameter range (a straight edge has none).
    ordered = list(curved) + list(plates)
    afaces: list = []
    for pl in ordered:
        af = None
        try:
            af = pl.geom.geometry if isinstance(pl, PlateCurved) else flat_plate_to_advanced_face(pl)
        except UnsupportedCurvedFace as ex:
            skipped[str(ex)] += 1
        except Exception as ex:  # noqa: BLE001 - a bad face must not sink the whole write
            skipped[f"advanced-face build: {ex}"] += 1
        afaces.append(af)

    # Imprint the beam axes onto the faces: a stiffener lying on a plate splits it
    # along its axis, exactly as Genie's own export is split. Without this Genie
    # re-imprints on import and relinks a monolithic face edge (ACIS 21013). The
    # fuse is given only the faces that built; results are remapped back by plate.
    beams, beam_axes = _beam_axes(sw.part)
    # Imprint every plate whose face built. imprint_advanced_faces skips a
    # BRepCheck-invalid face rather than risk the General Fuse segfaulting on it,
    # so curved plates take part where they can and fall back to monolithic where
    # they can't.
    build_idx = [idx for idx in range(len(ordered)) if afaces[idx] is not None]
    build_faces = [afaces[idx] for idx in build_idx]
    imprint = imprint_advanced_faces(build_faces, beam_axes) if (beam_axes and build_faces) else None
    imp_map: dict[int, "list | None"] = {}
    if imprint is not None:
        for k, idx in enumerate(build_idx):
            imp_map[idx] = imprint.sub_faces[k]

    def author(pl, face_geom) -> None:
        nonlocal face_id
        face_name = f"FACE{face_id:08d}"
        try:
            entities = advanced_face_to_sat_entities(face_geom, face_name, sw, weld)
        except UnsupportedCurvedFace as ex:
            skipped[str(ex)] += 1
            return
        for entity in entities:
            sw.add_entity(entity)
        sw.face_map.setdefault(pl.guid, []).append(face_name)
        face_id += 1

    n_imprinted = 0
    for idx, pl in enumerate(ordered):
        subs = imp_map.get(idx)
        # Prefer the imprinted faces (they carry the beam-axis edges Genie needs
        # to imprint against without relinking); fall back to the original face
        # when the fuse could not produce a usable set for this plate.
        if subs:
            if len(subs) > 1:
                n_imprinted += 1
            for sub in subs:
                author(pl, sub)
        elif afaces[idx] is not None:
            author(pl, afaces[idx])

    link_partner_rings(weld)
    # Name the edges each beam was imprinted into and point the beam's
    # <sat_reference> at them, so Genie reuses the edge instead of re-imprinting
    # the beam on import (which relinks a face edge and raises 21013). Falls back
    # to naming just the curved-beam arc edges when the imprint did not run.
    if imprint is not None:
        name_imprinted_beam_edges(sw, weld, beams, imprint.beam_edges)
    # Arc beams (BeamRevolve) are not in the straight-beam imprint set; name the
    # curved-plate edge each of those lies on, as before.
    name_curved_beam_edges(sw, weld)
    # Most stiffeners lie on edges the weld already holds: Genie pre-splits its
    # panels along them and the reader hands those sub-faces back, so the edge
    # exists once the faces are welded — no imprint needed. Name each remaining
    # straight beam onto the existing welded edge(s) its axis coincides with.
    name_straight_beam_edges(sw, weld)
    for entity in weld.entities:
        sw.add_entity(entity)

    logger.info(
        f"sat-write: {face_id - 1} faces ({len(curved)} curved, {len(plates)} flat) share "
        f"{weld.n_vertices} vertices and {weld.n_edges} edges"
    )
    if weld.range_conflicts:
        # Two faces on one edge disagreeing about its parameter range means the
        # weld joined edges that are not the same edge.
        logger.warning(
            f"sat-write: {weld.range_conflicts} shared edges disagree on their parameter range; "
            "the welded topology may be wrong there"
        )
    if skipped:
        total = sum(skipped.values())
        detail = "; ".join(f"{reason} ({n})" for reason, n in skipped.most_common(3))
        logger.warning(f"sat-write: {total} of {len(curved)} curved plates are not authored as SAT faces: {detail}")


def _add_imprinted_plates(sw: SatWriter, plates) -> None:
    """Imprint the plates against each other and the beams, then emit the topology."""
    from ada.cad import select_backend
    from ada.cadit.sat.write.from_imprint import imprint_to_sat_entities
    from ada.cadit.sat.write.write_plate import outline_ccw_about

    # Global coordinates (a plate inside a placed Part would otherwise be
    # imprinted at the wrong position), oriented counter-clockwise about the
    # plate's own normal: the backend derives each face's plane from the polygon
    # it is given, so feeding CurvePoly2d's raw (possibly clockwise) order would
    # flip every face normal away from the plate's declared one.
    outlines = [outline_ccw_about(*pl.outline_global()) for pl in plates]
    beams, curves = _beam_axes(sw.part)

    result = select_backend().imprint_planar_faces(outlines, imprint_curves=curves)

    entities, faces, edges = imprint_to_sat_entities(result, sw)
    for entity in entities:
        sw.add_entity(entity)

    for i, face in enumerate(faces, start=1):
        face.name.name = f"FACE{i:08d}"

    for pl, src in zip(plates, result.sources):
        sw.face_map[pl.guid] = [faces[i].name.name for i in src]
        if not src:
            logger.warning(f"sat-write: plate {pl.name!r} produced no face in the imprint and is not referenced")

    _name_beam_edges(sw, beams, edges, result.curve_sources)

    logger.info(
        f"sat-write: imprinted {len(plates)} plates against {len(curves)} beam axes -> "
        f"{len(faces)} faces, {sum(len(v) for v in sw.edge_map.values())} named edges"
    )


def _name_beam_edges(sw: SatWriter, beams, edges, curve_sources) -> None:
    """Name each beam's imprinted axis edges and record the beam -> EDGE map.

    Without this a beam's ``<sat_reference/>`` is empty and Genie rebuilds that
    beam's ACIS wire on import — on a large frame that dominates load time, far
    more than the plates do. Genie's own exports name every edge a beam resolves
    to and reference each one from the beam's ``<wire>``.
    """
    edge_name_of: dict[int, str] = {}
    unresolved = 0
    for beam, src in zip(beams, curve_sources or []):
        names = []
        for edge_idx in src:
            edge = edges[edge_idx]
            if edge.coedge is None:
                continue  # pruned: bounds no face, so it is not in the body
            name = edge_name_of.get(edge_idx)
            if name is None:
                name = f"EDGE{sw.edge_name_id:08d}"
                sw.edge_name_id += 1
                edge_name_of[edge_idx] = name
                attrib = se.StringAttribName(sw.id_generator.next_id(), name, edge)
                edge.attrib_name = attrib
                sw.add_entity(attrib)
            names.append(name)
        if names:
            sw.edge_map[beam.guid] = names
        else:
            unresolved += 1
    if unresolved:
        # A beam whose axis lies on no plate leaves no edge in the body (Genie
        # emits those as standalone `wire` bodies, which we do not author yet).
        logger.info(f"sat-write: {unresolved} beam(s) have no plate under their axis; left without a SAT edge")


@dataclass
class SatWriter:
    part: Part | Assembly
    entities: dict = field(default_factory=dict)

    header: str = HEADER_STR
    bbox: list[float] = field(default_factory=list)
    id_generator: IDGenerator = field(default_factory=IDGenerator)
    # plate guid -> the FACE names that plate resolves to. A plate maps to
    # several faces once the imprint pass splits it, so this is a list even
    # though the un-imprinted writer only ever puts one name in it.
    face_map: dict[str, list[str]] = field(default_factory=dict)
    # beam guid -> the EDGE names its axis resolves to (imprinted path only).
    # Empty for a beam whose axis lies on no plate.
    edge_map: dict[str, list[str]] = field(default_factory=dict)

    body: se.Body = None
    lump: se.Lump = None
    shell: se.Shell = None

    edge_name_id: int = 1

    def init_body(self, plates: list[Plate], curved: list[PlateCurved]) -> tuple[se.Body, se.Lump, se.Shell]:
        """Create the single body/lump/shell that owns every face."""
        self.bbox = _plates_bbox(plates, curved)
        body = se.Body(self.id_generator.next_id(), None, list(self.bbox))
        lump = se.Lump(self.id_generator.next_id(), None, body, list(self.bbox))
        shell = se.Shell(self.id_generator.next_id(), None, lump, list(self.bbox))
        body.lump = lump
        lump.shell = shell
        self.body, self.lump, self.shell = body, lump, shell
        for entity in (body, lump, shell):
            self.add_entity(entity)
        return body, lump, shell

    @property
    def is_empty(self) -> bool:
        """No faces to embed — the caller should omit ``<geometry>`` entirely."""
        return not self.entities

    def add_entity(self, entity: SATEntity) -> None:
        self.entities[entity.id] = entity

    def renumber(self) -> None:
        """Re-index entities so body/lump/shell/face lead, then everything else.

        Record ids are positional, so only their relative order matters to ACIS;
        leading with the topology roots mirrors how Genie lays its files out and
        keeps diffs against the reference files readable.
        """
        first = list(
            chain(
                self.get_entities_by_type(se.Body),
                self.get_entities_by_type(se.Lump),
                self.get_entities_by_type(se.Shell),
                self.get_entities_by_type(se.Face),
            )
        )
        leading = {id(e) for e in first}
        rest = [e for e in self.entities.values() if id(e) not in leading]
        for new_id, entity in enumerate(chain(first, rest)):
            entity.id = new_id
        self.entities = {e.id: e for e in sorted(self.entities.values(), key=lambda x: x.id)}

    def write(self, file_path: str | pathlib.Path) -> None:
        with open(file_path, "w") as f:
            f.write(self.to_str())

    def to_str(self):
        sorted_values = sorted(self.entities.values(), key=lambda x: x.id)
        return self.header + "\n".join(entity.to_string() for entity in sorted_values) + "\nEnd-of-ACIS-data"

    def get_entities_by_type(self, by_type) -> list[SATEntity]:
        return list(filter(lambda x: type(x) is by_type, self.entities.values()))


def _beam_axes(part) -> tuple[list, list[list[tuple[float, float, float]]]]:
    """Every beam and its axis, as a 2-point polyline, to imprint onto the plates.

    Returned index-aligned so the imprint's ``curve_sources`` maps straight back
    to the beam that produced each edge.

    A stiffener lying on a plate splits it along its axis; a member merely
    crossing one drops a vertex on the boundary. This is where most of Genie's
    face count comes from — its own export splits a 3.9 m panel carrying
    stiffeners at 0.65 m spacing into 6 faces, one per bay.

    Positioned exactly as :func:`~ada.cadit.gxml.write.write_beams.add_segments`
    writes the beam's guide into the XML — full transform when the placement
    rotates, translation only otherwise — so the imprinted edge lands on the
    concept beam the ``<wire>`` will reference.
    """
    from ada import Beam

    beams, axes = [], []
    for bm in part.get_all_physical_objects(by_type=Beam):
        p1, p2 = bm.axis_global()
        a = tuple(float(c) for c in p1)
        b = tuple(float(c) for c in p2)
        if a != b:
            beams.append(bm)
            axes.append([a, b])
    return beams, axes


def _plates_bbox(plates: list[Plate], curved: list[PlateCurved]) -> list[float]:
    """[xmin, ymin, zmin, xmax, ymax, zmax] over every plate outline.

    Genie sets the body/lump/shell box to the union of the plate extents; an
    empty plate set degenerates to a zero box rather than failing. A curved
    plate has no ``poly``, so it contributes its boundary nodes instead — the
    same loop-edge endpoints the face is built from.
    """
    import numpy as np

    from ada.cadit.sat.utils import make_ints_if_possible

    groups = [np.asarray(pl.poly.points3d, dtype=float) for pl in plates]
    groups += [np.asarray([n.p for n in pl.nodes], dtype=float) for pl in curved if pl.nodes]
    if not groups:
        return [0.0] * 6
    pts = np.concatenate(groups)
    return make_ints_if_possible([*np.min(pts, axis=0), *np.max(pts, axis=0)])

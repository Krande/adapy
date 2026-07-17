"""Import producer: a Genie/ACIS SAT body → a shared :class:`BRepStore`.

This is the *ground truth* leg of the connectivity store. It walks the SAT
topology hierarchy (body → lump → shell → face → loop → coedge → edge → vertex →
point) and preserves the source's sharing by **record identity**: a vertex/edge is
built once, keyed on its SAT record index, so the two coedges that reference the
same ``$edge`` resolve to the same :class:`BEdge`. Nothing is welded by position —
the sharing is read straight off the ``$``-references Genie authored.

Curve/surface geometry is converted to ngeom via the existing SAT read helpers, so
the store stays neutral (no SAT text retained). Faces whose surface the reader
cannot parse (a handful of splines) are skipped and counted rather than aborting
the whole body.

Record field offsets (SAT v4.0, verified against a Genie export):
  body   [6]=first lump
  lump   [6]=next lump   [7]=first shell
  shell  [6]=next shell  [8]=first face
  face   [6]=next face   [7]=first loop   [10]=surface   [11]=sense
  loop   [6]=next loop   [7]=first coedge
  coedge [6]=next coedge [9]=edge   [10]=sense   [11]=loop
  edge   [6]=start vtx   [7]=t_start [8]=end vtx [9]=t_end  [11]=curve  [12]=sense
  vertex [7]=point
  point  [6:9]=x y z
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada import Point
from ada.cadit.sat.exceptions import ACISDegenerateEdge
from ada.cadit.sat.read.advanced_face import get_face_same_sense, get_face_surface
from ada.cadit.sat.read.curves import create_line_from_sat, get_ellipse_curve
from ada.config import logger
from ada.geom.brep import BRepStore, LoopKind

if TYPE_CHECKING:
    from ada.cadit.sat.read.sat_entities import AcisRecord
    from ada.cadit.sat.store import SatStore


def _floats_after_T(toks: list[str], start: int, n: int) -> list[float] | None:
    """The ``n`` floats following the ``T`` at/after ``toks[start]``, or None."""
    if start >= len(toks) or toks[start] != "T":
        return None
    vals = toks[start + 1 : start + 1 + n]
    if len(vals) != n:
        return None
    try:
        return [float(x) for x in vals]
    except (TypeError, ValueError):
        return None


def _face_boxes(face_rec) -> tuple[list[float] | None, list[float] | None]:
    """(3D bbox 6 floats, UV param box 4 floats) from a face record's tail:
    ``... out T <xmin ymin zmin xmax ymax zmax> [T <umin umax vmin vmax> | F]``."""
    toks = face_rec.chunks
    try:
        i = toks.index("out")
    except ValueError:
        return None, None
    bbox = _floats_after_T(toks, i + 1, 6)
    if bbox is None:
        return None, None
    param = _floats_after_T(toks, i + 1 + 1 + 6, 4)  # T <6> then T <4>
    return bbox, param


def _loop_bbox(loop_rec) -> list[float] | None:
    """The loop's 3D bbox (the 6 floats after its ``T``)."""
    toks = loop_rec.chunks
    try:
        i = toks.index("T")
    except ValueError:
        return None
    return _floats_after_T(toks, i, 6)


def _walk_chain(store: SatStore, first_ref: str, next_idx: int, max_n: int = 2_000_000):
    """Yield records along a ``chunks[next_idx]`` linked list until ``$-1``."""
    rec = store.get(first_ref)
    seen = 0
    while rec is not None:
        yield rec
        seen += 1
        if seen > max_n:
            raise ValueError(f"chain exceeded {max_n} — cycle?")
        nxt = rec.chunks[next_idx] if len(rec.chunks) > next_idx else "$-1"
        rec = store.get(nxt)


def _curve_from_edge(sat_store: SatStore, edge_rec: AcisRecord):
    """The edge's underlying curve as an ngeom primitive (direction-neutral).

    The store keeps the edge in its record direction (start→end); coedge sense
    carries orientation, so unlike the OCC path we do NOT swap by coedge here.
    """
    curve_chunk = edge_rec.chunks[11] if len(edge_rec.chunks) > 11 else "$-1"
    if not curve_chunk or curve_chunk == "$-1":
        raise ACISDegenerateEdge(f"edge {edge_rec.chunks[0]} names no curve")
    curve_rec = sat_store.get(curve_chunk)
    if curve_rec is None:
        raise ACISDegenerateEdge(f"edge {edge_rec.chunks[0]} curve not in file")
    if curve_rec.type == "straight-curve":
        return create_line_from_sat(curve_rec)
    if curve_rec.type == "ellipse-curve":
        return get_ellipse_curve(curve_rec)
    if curve_rec.type == "intcurve-curve":
        from ada.cadit.sat.read.bsplinecurves import (
            create_bspline_curve_from_sat,
            create_surface_curve_from_sat,
        )

        # a surfintcur is a curve-on-surface: keep it whole (3D spline + its 2D
        # pcurves) as a SurfaceCurve, so reference-form coedge pcurves resolve.
        try:
            sc = create_surface_curve_from_sat(curve_rec)
        except Exception:  # noqa: BLE001 — fall back to the plain 3D read
            sc = None
        if sc is not None:
            return sc
        curve = create_bspline_curve_from_sat(curve_rec)
        if curve is None:
            raise ACISDegenerateEdge(f"edge {edge_rec.chunks[0]} intcurve unparsable")
        return curve
    raise ACISDegenerateEdge(f"unsupported curve type {curve_rec.type}")


def _read_pcurve(sat_store: SatStore, coedge_rec: AcisRecord):
    """The coedge's UV pcurve as an ngeom ``Pcurve2dBSpline`` (authored sense
    preserved), or None. Captured raw — no OCC-oriented reversal — so it can be
    re-emitted verbatim; a spline face needs it to be ACIS-valid.

    Two SAT forms exist. Inline: ``pcurve ... 0 <sense> { exppc ... }`` with the
    2D data in the record. Reference: ``pcurve ... ±n $intcurve 0 0`` — the data
    is pcurve ``|n|`` of the referenced intcurve's ``surfintcur`` (a curve-on-
    surface), negative when it runs against the 3D curve.
    """
    chunks = coedge_rec.chunks
    if len(chunks) <= 12:
        return None
    ref = chunks[12]
    if not ref or not ref.startswith("$") or ref == "$-1":
        return None
    rec = sat_store.get(ref)
    if rec is None or getattr(rec, "type", None) != "pcurve":
        return None

    try:
        idx = int(rec.chunks[6])
    except (ValueError, IndexError):
        idx = 0
    if idx != 0:
        # reference form: pull pcurve |idx| out of the surfintcur it points at
        from dataclasses import replace

        from ada.cadit.sat.read.bsplinecurves import create_surface_curve_from_sat

        curve_rec = sat_store.get(rec.chunks[7])
        if curve_rec is None:
            return None
        try:
            sc = create_surface_curve_from_sat(curve_rec)
        except Exception:  # noqa: BLE001
            return None
        if sc is None or abs(idx) > len(sc.associated_pcurves):
            return None
        pc = sc.associated_pcurves[abs(idx) - 1]
        if pc is None:
            return None
        return replace(pc, same_sense=idx > 0)

    from ada.cadit.sat.read.bsplinecurves import create_pcurve_2d_from_sat_record

    try:
        pc = create_pcurve_2d_from_sat_record(rec)
    except Exception:  # noqa: BLE001 — a malformed pcurve must not fail the coedge
        return None
    if pc is None:
        return None
    sense = "forward"
    if len(rec.chunks) > 7 and rec.chunks[7] in ("forward", "reversed"):
        sense = rec.chunks[7]
    pc.same_sense = sense == "forward"
    return pc


def sat_store_to_brep(sat_store: SatStore) -> BRepStore:
    """Build a :class:`BRepStore` from a populated :class:`SatStore`."""
    store = BRepStore()
    vmap: dict[int, object] = {}  # sat vertex index -> BVertex
    emap: dict[int, object] = {}  # sat edge index -> BEdge
    skipped_faces = 0
    skipped_coedges = 0

    def get_vertex(vrec: AcisRecord):
        v = vmap.get(vrec.index)
        if v is None:
            prec = sat_store.get(vrec.chunks[7])
            pt = Point(*[float(x) for x in prec.chunks[6:9]])
            try:
                name = vrec.get_name() or None
            except Exception:  # noqa: BLE001
                name = None
            v = store.add_vertex(pt, name=name, source_id=str(vrec.index))
            vmap[vrec.index] = v
        return v

    def get_edge(erec: AcisRecord):
        e = emap.get(erec.index)
        if e is not None:
            return e
        curve = _curve_from_edge(sat_store, erec)  # may raise ACISDegenerateEdge
        v0 = get_vertex(sat_store.get(erec.chunks[6]))
        v1 = get_vertex(sat_store.get(erec.chunks[8]))
        t0 = t1 = None
        try:
            t0 = float(erec.chunks[7])
            t1 = float(erec.chunks[9])
        except (ValueError, IndexError, TypeError):
            pass
        try:
            name = erec.get_name() or None
        except Exception:  # noqa: BLE001 — a malformed name attr must not fail the edge
            name = None
        e = store.add_edge(curve, v0, v1, t0, t1, name=name, source_id=str(erec.index))
        emap[erec.index] = e
        return e

    def coedge_edge(coedge_rec: AcisRecord):
        """The shared BEdge for a coedge, or None (recording why on the store)."""
        nonlocal skipped_coedges
        erec = sat_store.get(coedge_rec.chunks[9])
        try:
            return get_edge(erec)
        except ACISDegenerateEdge as ex:
            skipped_coedges += 1
            # Not silent: every un-built edge is recorded with its reason, so the
            # store can be audited for completeness against the source.
            store.mark_unresolved("edge", str(erec.index), str(ex))
            logger.debug("unresolved edge %s: %s", erec.chunks[0], ex)
            return None

    def build_loop(loop_rec: AcisRecord, kind: LoopKind):
        bloop = store.add_loop(kind, bbox=_loop_bbox(loop_rec), source_id=str(loop_rec.index))
        for coedge_rec in _walk_chain_ring(sat_store, loop_rec.chunks[7]):
            edge = coedge_edge(coedge_rec)
            if edge is None:
                continue
            sense = coedge_rec.chunks[10] == "forward"
            pcurve = _read_pcurve(sat_store, coedge_rec)
            store.add_coedge(edge, sense, bloop, pcurve=pcurve, source_id=str(coedge_rec.index))
        return bloop

    def build_wire(wire_rec: AcisRecord, shell, coedge_recs):
        """A wire's edges (guide axes / construction geometry) — kept for a
        complete mirror of the source body. Its coedges are collected by owner
        reference rather than by walking a ``next`` ring: a wire is not always a
        single ring, so the ring walk misses disconnected segments."""
        coedges = []
        for coedge_rec in coedge_recs:
            edge = coedge_edge(coedge_rec)
            if edge is None:
                continue
            sense = coedge_rec.chunks[10] == "forward"
            coedges.append(store.add_coedge(edge, sense, None, source_id=str(coedge_rec.index)))
        # (wire coedges carry no pcurve — they bound no surface)
        w = store.add_wire(coedges, source_id=str(wire_rec.index))
        w.shell = shell
        return w

    def build_face(face_rec: AcisRecord):
        nonlocal skipped_faces
        try:
            surface = get_face_surface(face_rec)
        except Exception as ex:  # noqa: BLE001 — a bad surface must not abort the body
            skipped_faces += 1
            store.mark_unresolved("face", str(face_rec.index), str(ex))
            logger.debug("unresolved face %s: %s", face_rec.chunks[0], ex)
            return None
        sense = get_face_same_sense(face_rec)
        loops = list(_walk_chain(sat_store, face_rec.chunks[7], next_idx=6))
        if not loops:
            skipped_faces += 1
            return None
        outer = build_loop(loops[0], LoopKind.OUTER)
        inner = [build_loop(lp, LoopKind.INNER) for lp in loops[1:]]
        try:
            name = face_rec.get_name() or None
        except Exception:  # noqa: BLE001
            name = None
        bbox, param_box = _face_boxes(face_rec)
        return store.add_face(
            surface, sense, outer=outer, inner=inner, name=name, bbox=bbox, param_box=param_box,
            source_id=str(face_rec.index),
        )

    # coedges owned by each wire, bucketed by owner ref (coedge.chunks[11] -> wire)
    wire_coedges: dict[int, list] = {}
    for r in sat_store.iter():
        if r.type != "coedge":
            continue
        owner_ref = r.chunks[11] if len(r.chunks) > 11 else "$-1"
        owner = sat_store.get(owner_ref)
        if owner is not None and owner.type == "wire":
            wire_coedges.setdefault(owner.index, []).append(r)

    bodies = sat_store_bodies(sat_store)
    for body in bodies:
        for lump_rec in _walk_chain(sat_store, body.chunks[6], next_idx=6):
            shells = []
            for shell_rec in _walk_chain(sat_store, lump_rec.chunks[7], next_idx=6):
                faces = []
                for face_rec in _walk_chain(sat_store, shell_rec.chunks[8], next_idx=6):
                    bf = build_face(face_rec)
                    if bf is not None:
                        faces.append(bf)
                bshell = store.add_shell(faces, source_id=str(shell_rec.index))
                shells.append(bshell)
                # a shell may carry a wire (chunks[9]); chain of wires via [6]
                wire_ref = shell_rec.chunks[9] if len(shell_rec.chunks) > 9 else "$-1"
                for wire_rec in _walk_chain(sat_store, wire_ref, next_idx=6):
                    build_wire(wire_rec, bshell, wire_coedges.get(wire_rec.index, []))
            store.add_lump(shells, source_id=str(lump_rec.index))

    # Completeness sweep: every SAT edge must be built or recorded as unresolved —
    # never silently absent. Any edge the topology walk did not reach (a wire the
    # owner bucket missed, an orphan) is built standalone off its shared vertices.
    for r in sat_store.iter():
        if r.type == "edge" and r.index not in emap:
            try:
                get_edge(r)
            except ACISDegenerateEdge as ex:
                store.mark_unresolved("edge", str(r.index), str(ex))

    if skipped_faces or skipped_coedges:
        logger.info(f"sat->brep: {skipped_faces} unresolved faces, {skipped_coedges} unresolved coedges")
    return store


def _walk_chain_ring(sat_store: SatStore, first_ref: str, max_n: int = 200_000):
    """Yield coedges along a loop ring or wire chain.

    Follows the ``next`` pointer (``chunks[6]``) and stops when it returns to the
    start (a closed loop), hits ``$-1`` (an open wire), or would revisit any
    already-seen coedge (a malformed chain) — a visited set rather than a bare
    return-to-start test, because a wire is not guaranteed to be a single ring.
    """
    start = sat_store.get(first_ref)
    if start is None:
        return
    visited: set[int] = set()
    cur = start
    while cur is not None and cur.index not in visited:
        yield cur
        visited.add(cur.index)
        if len(visited) > max_n:
            raise ValueError("coedge chain exceeded bound — cycle?")
        cur = sat_store.get(cur.chunks[6])


def sat_store_bodies(sat_store: SatStore) -> list[AcisRecord]:
    return [r for r in sat_store.iter() if r.type == "body"]


def genie_xml_to_brep(xml_path) -> BRepStore:
    """Convenience: build a store straight from a Genie concept XML."""
    from ada.cadit.gxml.store import GxmlStore

    gs = GxmlStore(xml_path)
    if len(gs.sat_factory.sat_store.sat_records) == 0:
        gs.sat_factory.load_sat_data_from_file()
    return sat_store_to_brep(gs.sat_factory.sat_store)

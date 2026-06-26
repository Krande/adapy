"""A kernel-free, lazily-streaming STEP (ISO-10303-21 / Part-21) reader.

This is the read-side counterpart to :mod:`ada.cadit.step.write.ap242_stream`.
It parses the analytic B-rep vocabulary that the streaming emitter produces
(``PLANE`` / ``CYLINDRICAL_SURFACE`` faces bound by ``LINE`` / ``CIRCLE`` edge
loops) and yields one adapy :class:`~ada.geom.Geometry` per ``MANIFOLD_SOLID_BREP``
*as it is encountered*. The caller can feed each ``Geometry`` straight to
``active_backend().build(geom)`` for tessellation and drop it again — so peak
memory tracks a single solid rather than the whole model.

Why not OpenCascade's ``STEPControl_Reader``? It materialises every root shape
into one in-memory compound (plus the reader's transfer maps) before anything
can be tessellated — that is the source of the OOM when re-reading a large
(e.g. 700 MB+) emitted STEP file. Why not a third-party C/C++ lazy reader
(STEPcode's ``cllazyfile``)? The two hard problems it solves — a general
EXPRESS-schema-bound parser and a lazy instance offset-index — are unnecessary
here: we only need a tiny fixed entity vocabulary, and the emitter writes each
solid's entity closure contiguously and bottom-up (definitions precede
references within the solid's block). That locality lets a *single forward
pass* with a per-solid entity pool that is cleared at each solid boundary do
the job in pure Python, with no kernel and no global index.

``local_pool=True`` (the default) relies on that locality. For arbitrary STEP
where entities are shared across solids (global point tables, forward
references), pass ``local_pool=False`` to keep the full entity pool for the
duration of the read.

Coverage is intentionally scoped to the emitter's vocabulary; unsupported
surface/curve types raise so the caller can fall back to the OCC reader.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ada.config import logger

# ---------------------------------------------------------------------------
# Stage 0 memory-attribution probe (env-gated; zero cost unless enabled).
#
# Set ``ADA_STEP_STREAM_MEM_PROBE=1`` to print a per-stage RSS breakdown of the
# streaming reader's parent process: how much resident memory is the file mmap
# (reclaimable, file-backed), the spilled id/offset index tmpfiles, anonymous
# Python heap, plus a sizing of the ``colour_map`` / ``tmap`` dicts. This is the
# measurement that decides where the ~2.1 GB parent peak actually lives before
# we optimise (see dap plan: cut peak parent RSS of STEP->GLB).
# ---------------------------------------------------------------------------
import os as _os


def _mem_probe_enabled() -> bool:
    return _os.environ.get("ADA_STEP_STREAM_MEM_PROBE", "") not in ("", "0", "false", "no")


def _deep_size(obj, _seen=None) -> int:
    """Best-effort deep byte size of the nested dict/list/tuple/ndarray structures
    the reader holds (``tmap``, ``colour_map``). Counts numpy buffers by ``nbytes``
    and walks containers once (id-deduped). Not exact, but good enough to rank."""
    import sys

    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return 0
    _seen.add(oid)
    total = sys.getsizeof(obj, 0)
    try:
        import numpy as _np

        if isinstance(obj, _np.ndarray):
            return int(obj.nbytes) + sys.getsizeof(obj, 0)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(obj, dict):
        for k, v in obj.items():
            total += _deep_size(k, _seen) + _deep_size(v, _seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for it in obj:
            total += _deep_size(it, _seen)
    return total


def _smaps_attribution(step_path=None, idx_paths=()):
    """Return ``(file_mmap_kb, index_kb, anon_kb, rollup_rss_kb)`` by parsing
    ``/proc/self/smaps`` (per-mapping ``Rss``) and ``/proc/self/smaps_rollup``
    (process total). Linux-only; returns zeros where unavailable."""
    file_mmap = index = anon = 0
    step_real = None
    try:
        if step_path is not None:
            step_real = _os.path.realpath(str(step_path))
    except Exception:  # noqa: BLE001
        step_real = str(step_path) if step_path is not None else None
    idx_set = {str(p) for p in (idx_paths or ())}
    _hdr = re.compile(r"^[0-9a-fA-F]+-[0-9a-fA-F]+ ")
    try:
        with open("/proc/self/smaps", "r") as fh:
            cur = None  # current mapping's pathname
            for line in fh:
                if _hdr.match(line):
                    # mapping header: "addr-addr perms off dev inode  pathname"
                    parts = line.split()
                    cur = parts[5] if len(parts) >= 6 else ""
                elif line.startswith("Rss:"):
                    kb = int(line.split()[1])
                    if not kb:
                        continue
                    name = cur or ""
                    if step_real is not None and (name == step_real or name.endswith(".stp") or name.endswith(".step")):
                        file_mmap += kb
                    elif name in idx_set or name.endswith(".ada_idx_ids") or name.endswith(".ada_idx_offs"):
                        index += kb
                    elif name == "" or name.startswith("[heap]") or name.startswith("[stack]") or name == "[anon]":
                        anon += kb
    except OSError:
        pass
    rollup = 0
    try:
        with open("/proc/self/smaps_rollup", "r") as fh:
            for line in fh:
                if line.startswith("Rss:"):
                    rollup = int(line.split()[1])
                    break
    except OSError:
        pass
    return file_mmap, index, anon, rollup


def _mem_probe(label: str, *, step_path=None, idx_paths=(), sized=None) -> None:
    """Print a one-line RSS attribution for ``label`` when the probe env is set."""
    if not _mem_probe_enabled():
        return
    file_mmap, index, anon, rollup = _smaps_attribution(step_path, idx_paths)
    extra = ""
    if sized:
        sizes = {name: _deep_size(obj) for name, obj in sized.items() if obj is not None}
        extra = "  " + "  ".join(f"{n}={v / 1e6:.1f}MB" for n, v in sizes.items())
    logger.warning(
        "[MEMPROBE] %-26s rss=%6.0fMB  file_mmap=%6.0fMB  index=%5.0fMB  anon=%6.0fMB%s",
        label,
        rollup / 1024.0,
        file_mmap / 1024.0,
        index / 1024.0,
        anon / 1024.0,
        extra,
    )
from ada.geom import Geometry
from ada.geom.booleans import BooleanResult, BoolOpEnum
from ada.geom.curves import (
    BSplineCurveFormEnum,
    BSplineCurveWithKnots,
    Circle,
    CompositeCurve,
    CompositeCurveSegment,
    EdgeCurve,
    EdgeLoop,
    Ellipse,
    GeometricCurveSet,
    Hyperbola,
    KnotType,
    Line,
    OffsetCurve3D,
    OrientedEdge,
    Parabola,
    PCurve,
    PointOnCurve,
    PolyLine,
    PolyLoop,
    RationalBSplineCurveWithKnots,
    TrimmedCurve,
)
from ada.geom.direction import Direction
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.points import Point
from ada.geom.solids import (
    Box,
    Cone,
    Cylinder,
    ExtrudedAreaSolid,
    FacetedBrep,
    RevolvedAreaSolid,
    Sphere,
    Torus,
)
from ada.geom.surfaces import (
    AdvancedFace,
    BSplineSurfaceForm,
    BSplineSurfaceWithKnots,
    ClosedShell,
    ConicalSurface,
    CurveBoundedPlane,
    CylindricalSurface,
    FaceBound,
    OffsetSurface,
    OpenShell,
    Plane,
    PointOnSurface,
    RationalBSplineSurfaceWithKnots,
    RectangularCompositeSurface,
    RectangularTrimmedSurface,
    ShellBasedSurfaceModel,
    SphericalSurface,
    SurfaceOfLinearExtrusion,
    SurfaceOfRevolution,
    ToroidalSurface,
    TriangulatedFaceSet,
)

__all__ = ["stream_read_step", "StepStreamUnsupported"]


class StepStreamUnsupported(NotImplementedError):
    """Raised when the file uses an entity outside the streaming reader's scope.

    Signals the caller to fall back to the full OCC ``STEPControl_Reader``."""


# --------------------------------------------------------------------------- #
# Part-21 tokenizing
# --------------------------------------------------------------------------- #
class _Ref:
    """A reference to another instance (``#42``)."""

    __slots__ = ("id",)

    def __init__(self, i: int):
        self.id = i

    def __repr__(self):
        return f"#{self.id}"


class _Enum:
    """An enumeration / logical value (``.T.``, ``.UNSPECIFIED.``)."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f".{self.name}."


_STAR = object()  # '*' — derived/redundant value
_DOLLAR = object()  # '$' — unset optional value

# STEP presentation-style colour resolution: a STYLED_ITEM points at a geometric
# item (the solid root) and a style tree that bottoms out in COLOUR_RGB or a named
# DRAUGHTING_PRE_DEFINED_COLOUR. We walk the style references generically rather than
# hard-coding the SURFACE_STYLE_USAGE→…→FILL_AREA_STYLE_COLOUR chain (writers vary).
_PREDEFINED_COLOURS = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "magenta": (1.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
}


def _iter_refs(args):
    """Yield referenced instance ids from a (possibly nested) parsed arg list."""
    for v in args:
        if isinstance(v, _Ref):
            yield v.id
        elif isinstance(v, (list, tuple)):
            yield from _iter_refs(v)


def _find_colour(pool_get, ref_id: int, depth: int = 0, seen: set | None = None):
    """Walk an entity's style reference tree collecting the first colour (``COLOUR_RGB`` or a
    pre-defined colour name) and the first ``SURFACE_STYLE_TRANSPARENT`` transparency. Returns
    ``(r, g, b, a)`` in 0..1 (a = 1 − transparency, default 1.0 = opaque), or ``None`` when no
    colour is found. BFS so a colour and its sibling transparency are both reached (mirrors
    step2glb's styles.rs find_color)."""
    from collections import deque

    queue: deque[tuple[int, int]] = deque([(ref_id, 0)])
    seen = set()
    rgb = None
    transparency = None
    while queue:
        rid, d = queue.popleft()
        if d > 12 or rid in seen:
            continue
        seen.add(rid)
        rec = pool_get(rid)
        if rec is None:
            continue
        t = rec.type
        if t == "COLOUR_RGB" and rgb is None:
            a = rec.args
            try:
                rgb = (float(a[1]), float(a[2]), float(a[3]))
            except Exception:  # noqa: BLE001
                rgb = None
        elif t in ("DRAUGHTING_PRE_DEFINED_COLOUR", "PRE_DEFINED_COLOUR") and rgb is None:
            name = rec.args[0] if rec.args and isinstance(rec.args[0], str) else ""
            rgb = _PREDEFINED_COLOURS.get(name.strip().lower())
        elif t == "SURFACE_STYLE_TRANSPARENT" and transparency is None:
            for v in rec.args:
                try:
                    transparency = float(v)
                    break
                except (TypeError, ValueError):
                    continue
        else:
            for cid in _iter_refs(rec.args):
                queue.append((cid, d + 1))
        if rgb is not None and transparency is not None:
            break
    if rgb is None:
        return None
    # ISO 10303-46: transparency 0 = opaque, 1 = fully transparent.
    alpha = 1.0 if transparency is None else max(0.0, min(1.0, 1.0 - transparency))
    return (rgb[0], rgb[1], rgb[2], alpha)


def _build_colour_map(pool_get, styled_ids: list[int]) -> dict[int, tuple]:
    """Map ``geometric_item_id -> (r, g, b)`` from every STYLED_ITEM. Only the style
    references are searched (not the item itself) so we don't walk the huge geometry
    tree looking for a colour."""
    cmap: dict[int, tuple] = {}
    for sid in styled_ids:
        rec = pool_get(sid)
        if rec is None or rec.type != "STYLED_ITEM" or len(rec.args) < 3:
            continue
        item = rec.args[2]
        if not isinstance(item, _Ref) or item.id in cmap:
            continue
        styles = rec.args[1]
        for rid in _iter_refs(styles if isinstance(styles, (list, tuple)) else [styles]):
            col = _find_colour(pool_get, rid)
            if col is not None:
                cmap[item.id] = col
                break
    return cmap


def _as_color(rgb):
    if rgb is None:
        return None
    from ada.visit.colors import Color

    return Color(*rgb)


# --------------------------------------------------------------------------- #
# STEP assembly-instance transforms (kernel-free).
#
# A MANIFOLD_SOLID_BREP authored in a sub-assembly's *local* frame is positioned
# in the world by a chain of placement relationships:
#
#   solid  --(item of)-->  ADVANCED_BREP_SHAPE_REPRESENTATION (geom rep)
#   geom rep  <--SHAPE_REPRESENTATION_RELATIONSHIP-->  placement SHAPE_REPRESENTATION
#   placement rep == rep_1 of a CONTEXT_DEPENDENT_SHAPE_REPRESENTATION's complex
#       rep_rel = ( REPRESENTATION_RELATIONSHIP($,$,rep_1,rep_2)
#                   REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#IDT)
#                   SHAPE_REPRESENTATION_RELATIONSHIP() )
#   IDT = ITEM_DEFINED_TRANSFORMATION($,$,item_1,item_2)   item_1 in rep_1 (child),
#       item_2 in rep_2 (parent), both AXIS2_PLACEMENT_3D.
#   T_edge = inv(M(item_1)) @ M(item_2)   maps child(rep_1) coords -> parent(rep_2).
#   recurse rep_2 (it is rep_1 of its own CDSR edge) up to a root rep; accumulate
#   T_world = T_parent @ T_edge (root-most leftmost). A rep that is rep_1 of N edges
#   yields N world matrices -> N placed instances of the solid.
#
# The algorithm is validated against OpenCascade's STEPControl_Reader to 1e-9.
# A flat/baked file (e.g. adapy's own emitter) has no standalone SRR and identity
# IDTs, so place_rep falls back to the geom rep, there are no edges, and every solid
# yields a single identity (None) transform — a no-op for existing flat reads.
# --------------------------------------------------------------------------- #
def _axis2_args(pool_get, rid: int):
    """Return (location, axis|None, ref|None) raw 3-tuples from an AXIS2_PLACEMENT_3D id."""
    import numpy as np

    rec = pool_get(rid)
    if rec is None or rec.type != "AXIS2_PLACEMENT_3D":
        return None

    def _coords(arg):
        if not isinstance(arg, _Ref):
            return None
        sub = pool_get(arg.id)
        if sub is None or not sub.args:
            return None
        c = sub.args[1] if len(sub.args) > 1 else None  # ('', (x,y,z))
        if not isinstance(c, (list, tuple)):
            return None
        try:
            return np.asarray([float(x) for x in c], dtype=float)
        except (TypeError, ValueError):
            return None

    a = rec.args
    loc = _coords(a[1]) if len(a) > 1 else None
    if loc is None:
        return None
    axis = _coords(a[2]) if len(a) > 2 else None
    ref = _coords(a[3]) if len(a) > 3 else None
    return loc, axis, ref


def _axis2_to_matrix(loc, axis, ref):
    """Build a 4x4 placement matrix from an AXIS2_PLACEMENT_3D (STEP defaults for
    unset axis/ref). Validated vs OCC to 1e-9."""
    import numpy as np

    def _norm(v):
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-12 else v

    z = _norm(axis if axis is not None else np.asarray([0.0, 0.0, 1.0]))
    x = ref if ref is not None else np.asarray([1.0, 0.0, 0.0])
    x = _norm(x - float(np.dot(x, z)) * z)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0] = x
    m[:3, 1] = y
    m[:3, 2] = z
    m[:3, 3] = loc
    return m


def _placement_matrix(pool_get, rid: int):
    """4x4 matrix for an AXIS2_PLACEMENT_3D id, or identity if unresolvable."""
    import numpy as np

    parts = _axis2_args(pool_get, rid)
    if parts is None:
        return np.eye(4)
    return _axis2_to_matrix(*parts)


def _cdsr_rep_rel(pool_get, cdsr_rec):
    """For a CONTEXT_DEPENDENT_SHAPE_REPRESENTATION, return its complex rep_rel record
    (the one carrying REPRESENTATION_RELATIONSHIP + ..._WITH_TRANSFORMATION), or None."""
    if not cdsr_rec.args or not isinstance(cdsr_rec.args[0], _Ref):
        return None
    rec = pool_get(cdsr_rec.args[0].id)
    if rec is None or rec.type != _COMPLEX or not isinstance(rec.args, dict):
        return None
    return rec


def _rep_rel_edge(pool_get, rep_rel_rec):
    """Extract (rep_1_id, rep_2_id, idt_id) from a complex rep_rel record, or None."""
    subs = rep_rel_rec.args
    rr = subs.get("REPRESENTATION_RELATIONSHIP")
    rrt = subs.get("REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION")
    if not rr or not rrt:
        return None
    # REPRESENTATION_RELATIONSHIP(name, desc, rep_1, rep_2) — complex sub-args are
    # 0-indexed (no leading '' name slot is stripped here; name/desc are present).
    refs = [v for v in rr if isinstance(v, _Ref)]
    if len(refs) < 2:
        return None
    rep_1, rep_2 = refs[0].id, refs[1].id
    idt = rrt[0] if rrt and isinstance(rrt[0], _Ref) else None
    if idt is None:
        return None
    return rep_1, rep_2, idt.id


def _build_product_name_map(pool_get, sdr_ids):
    """rep id -> product name, via SHAPE_DEFINITION_REPRESENTATION -> PRODUCT_DEFINITION_SHAPE
    -> PRODUCT_DEFINITION -> FORMATION -> PRODUCT. Used to label assembly-tree group nodes;
    any unresolvable link just leaves the rep unnamed (caller falls back to ``asm_<rep>``)."""
    name_of_rep: dict[int, str] = {}
    for sid in sdr_ids:
        rec = pool_get(sid)
        if rec is None or len(rec.args) < 2:
            continue
        defn, rep = rec.args[0], rec.args[1]
        if not (isinstance(defn, _Ref) and isinstance(rep, _Ref)) or rep.id in name_of_rep:
            continue
        name = None
        try:
            pds = pool_get(defn.id)  # PRODUCT_DEFINITION_SHAPE(name, desc, #pd)
            pd = pool_get(pds.args[2].id) if pds and isinstance(pds.args[2], _Ref) else None
            pdf = pool_get(pd.args[2].id) if pd and isinstance(pd.args[2], _Ref) else None
            prod = pool_get(pdf.args[2].id) if pdf and isinstance(pdf.args[2], _Ref) else None
            if prod is not None:
                for cand in (prod.args[1], prod.args[0]):
                    if isinstance(cand, str) and cand.strip():
                        name = cand.strip()
                        break
        except Exception:  # noqa: BLE001 - naming is best-effort metadata
            name = None
        if name:
            name_of_rep[rep.id] = name
    return name_of_rep


def _build_transform_map(pool_get, root_ids, cdsr_ids, srr_ids, absr_ids, sdr_ids=(), global_scale=1.0):
    """Map each root solid id -> (matrices, paths): one world 4x4 matrix per placed
    instance, plus the matching assembly path — a root-first tuple of
    ``(rep_id, product_name)`` levels — so the scene graph can group instances the way
    the STEP product tree does.

    ``global_scale`` is the file's global length scale to metres (what the GLB writer
    multiplies positions by). When a representation declares its OWN length unit that
    differs (mixed mm/cm/metre files), its geometry and placement translations are
    brought into the global unit frame here — the per-instance matrix's rotation block
    is multiplied by the solid's ``rep_scale/global_scale`` (uniform scale commutes
    with rotation, so this scales the local geometry), and each placement translation
    is scaled by the factor of the rep it was authored in. Without this, parts
    authored in a non-global unit are mis-sized (e.g. metre-context fasteners in an
    mm file shrink 1000x and tessellate to near-zero-area slivers).

    Resilient: any unresolved entity simply leaves a solid with the identity ([None]
    handled by the caller); never raises.
    """
    import numpy as np

    _factor_cache: dict[int, float] = {}

    def _rep_factor(rep_id) -> float:
        if rep_id is None:
            return 1.0
        f = _factor_cache.get(rep_id)
        if f is None:
            s = _representation_length_scale(pool_get, rep_id)
            f = (s / global_scale) if (s is not None and abs(global_scale) > 1e-300) else 1.0
            _factor_cache[rep_id] = f
        return f

    # geom_rep id -> solid id (from each ABSR's item list)
    geomrep_of_solid: dict[int, int] = {}
    root_set = set(root_ids)
    for aid in absr_ids:
        rec = pool_get(aid)
        if rec is None or len(rec.args) < 2:
            continue
        items = rec.args[1]
        if not isinstance(items, (list, tuple)):
            continue
        for it in items:
            if isinstance(it, _Ref) and it.id in root_set:
                geomrep_of_solid[it.id] = aid

    # geom_rep id -> placement rep id (the non-ABSR side of a standalone SRR).
    place_rep_of_geom: dict[int, int] = {}
    absr_set = set(absr_ids)
    for sid in srr_ids:
        rec = pool_get(sid)
        if rec is None or len(rec.args) < 4:
            continue
        ra, rb = rec.args[2], rec.args[3]
        if not (isinstance(ra, _Ref) and isinstance(rb, _Ref)):
            continue
        # The geom rep is whichever side is the ABSR; the other side is the placement rep.
        if ra.id in absr_set:
            place_rep_of_geom.setdefault(ra.id, rb.id)
        elif rb.id in absr_set:
            place_rep_of_geom.setdefault(rb.id, ra.id)

    # rep_1 id -> list of (rep_2 id, item_1 id, item_2 id) edges (from CDSR/rep_rel/IDT).
    edges: dict[int, list] = {}
    for cid in cdsr_ids:
        cdsr = pool_get(cid)
        if cdsr is None:
            continue
        rr_rec = _cdsr_rep_rel(pool_get, cdsr)
        if rr_rec is None:
            continue
        edge = _rep_rel_edge(pool_get, rr_rec)
        if edge is None:
            continue
        rep_1, rep_2, idt_id = edge
        idt = pool_get(idt_id)
        if idt is None or idt.type != "ITEM_DEFINED_TRANSFORMATION" or len(idt.args) < 4:
            continue
        i1, i2 = idt.args[2], idt.args[3]
        if not (isinstance(i1, _Ref) and isinstance(i2, _Ref)):
            continue
        edges.setdefault(rep_1, []).append((rep_2, i1.id, i2.id))

    name_of_rep = _build_product_name_map(pool_get, sdr_ids)

    def _path_level(rep_id: int) -> tuple:
        return (rep_id, name_of_rep.get(rep_id) or f"asm_{rep_id}")

    def _world_matrices(rep_id: int, _seen: frozenset) -> list:
        """All ``(matrix, path)`` pairs reaching ``rep_id`` (a rep that is rep_1 of
        edges); path is root-first. Each edge T_edge maps this rep's coords -> its
        parent rep; recurse to root."""
        out_edges = edges.get(rep_id)
        if not out_edges:
            return [(np.eye(4), (_path_level(rep_id),))]  # root rep: its coords ARE world
        if rep_id in _seen:
            return [(np.eye(4), (_path_level(rep_id),))]  # cycle guard
        seen2 = _seen | {rep_id}
        mats: list = []
        for rep_2, i1, i2 in out_edges:
            m_child = _placement_matrix(pool_get, i1)
            m_parent = _placement_matrix(pool_get, i2)
            # Placement points are authored in their own rep's unit; bring each
            # translation into the global unit frame before composing so a
            # mixed-unit child/parent pair maps consistently.
            m_child[:3, 3] *= _rep_factor(rep_id)
            m_parent[:3, 3] *= _rep_factor(rep_2)
            try:
                t_edge = np.linalg.inv(m_child) @ m_parent
            except np.linalg.LinAlgError:
                t_edge = np.eye(4)
            for t_parent, parent_path in _world_matrices(rep_2, seen2):
                mats.append((t_parent @ t_edge, parent_path + (_path_level(rep_id),)))
        return mats

    tmap: dict[int, tuple] = {}
    for sid in root_ids:
        geom_rep = geomrep_of_solid.get(sid)
        # Flat/baked file: no ABSR item linkage at all -> identity, yield once.
        if geom_rep is None:
            continue
        # The placement rep is the SRR's non-ABSR side; when there's no standalone SRR
        # (box_comp-style: the ABSR itself is rep_1 of the CDSR edge) fall back to the
        # geom rep so its outgoing edges are found directly.
        place_rep = place_rep_of_geom.get(geom_rep, geom_rep)
        try:
            pairs = _world_matrices(place_rep, frozenset())
        except Exception:  # noqa: BLE001 - never crash a read over a placement chain
            pairs = [(np.eye(4), None)]
        mats = [m for m, _p in pairs]
        # Bake the solid's own length-unit factor into each instance's rotation block
        # (uniform scale commutes with rotation), scaling its local geometry into the
        # global unit frame. An identity-placement solid in a non-global unit thus
        # gains a pure-scale transform, so mixed-unit parts come out the right size
        # rather than collapsing to near-zero-area slivers.
        factor = _rep_factor(geom_rep)
        if abs(factor - 1.0) > 1e-12:
            for m in mats:
                m[:3, :3] *= factor
        # Drop pure-identity lists to a no-op (single instance, transform=None).
        nontrivial = [m for m in mats if not np.allclose(m, np.eye(4), atol=1e-12)]
        if not nontrivial and len(mats) <= 1:
            continue
        tmap[sid] = (mats, [p for _m, p in pairs])
    # solid id -> owning product name. The PRODUCT may be linked (via its SDR) to
    # either the solid's geom rep (ABSR; flat files) or, when the solid is placed
    # through a SHAPE_REPRESENTATION_RELATIONSHIP, to the placement rep — so try the
    # placement rep first, then the ABSR. Independent of any transform chain, so
    # both flat and deeply-placed solids get the real part name.
    name_of_solid = {}
    for sid in root_ids:
        gr = geomrep_of_solid.get(sid)
        if gr is None:
            continue
        place = place_rep_of_geom.get(gr, gr)
        nm = name_of_rep.get(place) or name_of_rep.get(gr)
        if nm:
            name_of_solid[sid] = nm
    return tmap, name_of_solid


# SI prefix -> factor relative to the unprefixed unit (we only resolve METRE).
_SI_PREFIX_SCALE = {
    "KILO": 1e3,
    "DECI": 1e-1,
    "CENTI": 1e-2,
    "MILLI": 1e-3,
    "MICRO": 1e-6,
    "NANO": 1e-9,
}
# CONVERSION_BASED_UNIT names -> metres. The exact factor lives in a referenced
# MEASURE_WITH_UNIT record; resolving it cross-statement isn't worth it when the
# unit *name* already pins the factor exactly. Some writers (e.g. Abaqus) express
# even plain millimetres this way rather than via an SI prefix.
_CONV_UNIT_SCALE = {
    "MILLIMETRE": 1e-3,
    "MILLIMETER": 1e-3,
    "MM": 1e-3,
    "CENTIMETRE": 1e-2,
    "CENTIMETER": 1e-2,
    "CM": 1e-2,
    "METRE": 1.0,
    "METER": 1.0,
    "M": 1.0,
    "INCH": 0.0254,
    "INCHES": 0.0254,
    "IN": 0.0254,
    "FOOT": 0.3048,
    "FEET": 0.3048,
    "FT": 0.3048,
    "YARD": 0.9144,
    "MILE": 1609.344,
}

# Both arg forms occur in the wild: SI_UNIT(.MILLI.,.METRE.) and SI_UNIT(.METRE.).
_SI_LEN_RE = re.compile(r"SI_UNIT\(\s*(?:(\.\w+\.|\$)\s*,\s*)?\.METRE\.\s*\)")
_CONV_NAME_RE = re.compile(r"CONVERSION_BASED_UNIT\(\s*'([^']*)'")


# Chunk size for the ``os.pread`` scan that locates the LENGTH_UNIT record. Module-level
# so tests can shrink it to exercise needle-straddles-chunk-boundary stitching.
_UNIT_SCAN_CHUNK = 1 << 20  # 1 MiB


def detect_step_length_unit_scale(filepath) -> float:
    """Factor converting the file's declared length unit to METRES, read from the
    first ``LENGTH_UNIT`` record in the data section (e.g. the ubiquitous
    ``( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )`` -> 0.001).

    glTF mandates metres, so the streaming GLB path multiplies positions by this;
    the OCC reader does the same conversion internally via ``xstep.cascade.unit``.
    Returns 1.0 when the file is already in metres or the unit is undetectable
    (logged at warning in the latter case).

    Scanned with chunked ``os.pread`` rather than a whole-file mmap: ``LENGTH_UNIT``
    routinely sits at the very END of the DATA section (e.g. ~99.7% into a 778 MB crane
    assembly), so an ``mmap.find`` would fault the entire file into RSS — a ~700 MB+
    transient spike right in the middle of the streaming reader's setup. pread keeps the
    pages in the reclaimable OS page cache, off this process's VmRSS."""
    import os

    needle = b"LENGTH_UNIT"
    overlap = len(needle) - 1
    chunk = _UNIT_SCAN_CHUNK
    try:
        fd = os.open(str(filepath), os.O_RDONLY)
    except OSError:
        return 1.0
    try:
        size = os.fstat(fd).st_size
        pos = 0
        tail = b""  # trailing bytes of the previous chunk, so a needle straddling a
        hit = -1  # chunk boundary is still found
        while pos < size:
            buf = os.pread(fd, chunk, pos)
            if not buf:
                break
            base = pos - len(tail)  # absolute file offset of window[0]
            window = tail + buf
            j = window.find(needle)
            if j >= 0:
                hit = base + j
                break
            tail = window[-overlap:] if len(window) >= overlap else window
            pos += len(buf)
        if hit < 0:
            return 1.0
        # Statement start = just past the prior ';'. Read a bounded window ending at the
        # match, then the full statement forward via the shared pread helper.
        bstart = max(0, hit - (1 << 16))
        pre = os.pread(fd, hit - bstart, bstart)
        k = pre.rfind(b";")
        start = bstart + k + 1 if k >= 0 else bstart
        stmt = _read_statement_pread(fd, start, size)
    except (OSError, ValueError):
        return 1.0
    finally:
        os.close(fd)
    m = _SI_LEN_RE.search(stmt)
    if m is not None:
        prefix = m.group(1)
        if prefix is None or prefix == "$":
            return 1.0
        return _SI_PREFIX_SCALE.get(prefix.strip("."), 1.0)
    m = _CONV_NAME_RE.search(stmt)
    if m is not None:
        scale = _CONV_UNIT_SCALE.get(m.group(1).strip().upper())
        if scale is not None:
            return scale
    logger.warning("detect_step_length_unit_scale: unrecognised LENGTH_UNIT record %r — assuming metres", stmt[:120])
    return 1.0


def _si_unit_length_scale(args) -> float | None:
    """Length scale to metres of an ``SI_UNIT(prefix, .METRE.)`` arg list, or None."""
    if not args or len(args) < 2:
        return None
    if _enum_name(args[-1]).strip(".").upper() != "METRE":
        return None
    prefix = args[0]
    if not isinstance(prefix, _Enum):  # ``$`` (no prefix) -> base metre
        return 1.0
    return _SI_PREFIX_SCALE.get(prefix.name.strip("."), 1.0)


def _unit_length_scale(pool_get, unit_id: int) -> float | None:
    """Scale to metres of a unit entity, or None if it isn't a length unit. Handles
    a plain ``SI_UNIT`` and the common complex
    ``( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )`` / inch-foot
    ``CONVERSION_BASED_UNIT`` forms."""
    rec = pool_get(unit_id)
    if rec is None:
        return None
    if rec.type == _COMPLEX and isinstance(rec.args, dict):
        si = rec.args.get("SI_UNIT")
        if si is not None:
            s = _si_unit_length_scale(si)
            if s is not None:
                return s
        if rec.args.get("LENGTH_UNIT") is not None:
            cbu = rec.args.get("CONVERSION_BASED_UNIT")
            if cbu and isinstance(cbu[0], str):
                return _CONV_UNIT_SCALE.get(cbu[0].strip().upper())
        return None
    if rec.type == "SI_UNIT":
        return _si_unit_length_scale(rec.args)
    return None


def _representation_length_scale(pool_get, rep_id: int) -> float | None:
    """Length-unit scale (to metres) of a SHAPE_REPRESENTATION's own context, via its
    ``GLOBAL_UNIT_ASSIGNED_CONTEXT`` unit list. None when the rep carries no length
    unit. Some CAD systems mix mm/cm/metre contexts in one file, so geometry must be
    scaled per representation rather than by a single global unit."""
    rec = pool_get(rep_id)
    if rec is None or not isinstance(rec.args, list):
        return None
    ctx_id = None  # the context is the last _Ref arg (after the items list)
    for v in reversed(rec.args):
        if isinstance(v, _Ref):
            ctx_id = v.id
            break
    if ctx_id is None:
        return None
    ctx = pool_get(ctx_id)
    if ctx is None:
        return None
    units = None
    if ctx.type == _COMPLEX and isinstance(ctx.args, dict):
        gua = ctx.args.get("GLOBAL_UNIT_ASSIGNED_CONTEXT")
        if gua and isinstance(gua[0], (list, tuple)):
            units = gua[0]
    elif ctx.type == "GLOBAL_UNIT_ASSIGNED_CONTEXT" and ctx.args and isinstance(ctx.args[0], (list, tuple)):
        units = ctx.args[0]
    if not units:
        return None
    for u in units:
        if isinstance(u, _Ref):
            s = _unit_length_scale(pool_get, u.id)
            if s is not None:
                return s
    return None


_HEADER_RE = re.compile(r"^\s*#(\d+)\s*=\s*([A-Z0-9_]+)\s*\(", re.S)
_COMPLEX_RE = re.compile(r"^\s*#(\d+)\s*=\s*\(", re.S)  # #id=(NAME(..)NAME(..)..) complex record
_COMPLEX = "__COMPLEX__"


def _iter_statements(fh, chunk_size: int = 1 << 20) -> Iterator[str]:
    """Yield raw Part-21 statements (text between ``;`` separators), streaming.

    ``;`` inside a quoted string does not terminate a statement. STEP strings
    are single-quoted with ``''`` as an escaped quote; the simple in/out toggle
    handles both because there is never a ``;`` between the two quote chars of
    an escaped pair.
    """
    pending = ""
    in_str = False
    while True:
        chunk = fh.read(chunk_size)
        if not chunk:
            break
        start = 0
        for j, c in enumerate(chunk):
            if c == "'":
                in_str = not in_str
            elif c == ";" and not in_str:
                yield pending + chunk[start:j]
                pending = ""
                start = j + 1
        pending += chunk[start:]
    if pending.strip():
        yield pending


def _parse_seq(s: str, i: int, end_char: str) -> tuple[list, int]:
    """Parse a comma-separated value sequence starting at ``i`` until ``end_char``."""
    vals: list = []
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        c = s[i]
        if c == end_char:
            return vals, i + 1
        if c == ",":
            i += 1
            continue
        val, i = _parse_value(s, i)
        vals.append(val)
    return vals, i


def _parse_value(s: str, i: int) -> tuple[object, int]:
    c = s[i]
    if c == "(":
        return _parse_seq(s, i + 1, ")")
    if c == "'":
        j = i + 1
        out = []
        while j < len(s):
            if s[j] == "'":
                if j + 1 < len(s) and s[j + 1] == "'":  # escaped quote
                    out.append("'")
                    j += 2
                    continue
                break
            out.append(s[j])
            j += 1
        return "".join(out), j + 1
    if c == "#":
        j = i + 1
        while j < len(s) and s[j].isdigit():
            j += 1
        return _Ref(int(s[i + 1 : j])), j
    if c == ".":
        j = i + 1
        while j < len(s) and s[j] != ".":
            j += 1
        return _Enum(s[i + 1 : j]), j + 1
    if c == "*":
        return _STAR, i + 1
    if c == "$":
        return _DOLLAR, i + 1
    # bare token: number or keyword
    j = i
    while j < len(s) and s[j] not in ",()":
        j += 1
    tok = s[i:j].strip()
    return _parse_scalar(tok), j


def _parse_scalar(tok: str):
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


# --------------------------------------------------------------------------- #
# Entity resolution: parsed tokens -> adapy geom objects
# --------------------------------------------------------------------------- #
@dataclass
class _Rec:
    type: str
    args: list


class _Resolver:
    """Resolves instance ids into adapy geom objects against an entity pool,
    memoizing within a single solid so shared points/edges are built once."""

    def __init__(self, pool: dict[int, _Rec]):
        self._pool = pool
        self._cache: dict[int, object] = {}

    def reset_cache(self):
        self._cache = {}

    def deref(self, val):
        """Resolve a value that may be a reference into a built object."""
        if isinstance(val, _Ref):
            return self.resolve(val.id)
        return val

    def resolve(self, ref_id: int):
        cached = self._cache.get(ref_id, _STAR)
        if cached is not _STAR:
            return cached
        rec = self._pool.get(ref_id)
        if rec is None:
            raise KeyError(f"unresolved reference #{ref_id}")
        obj = self._build(rec)
        self._cache[ref_id] = obj
        return obj

    def _build(self, rec: _Rec):
        if rec.type == _COMPLEX:
            return _build_complex(self, rec.args)  # rec.args is a {SUBTYPE: subargs} dict
        builder = _BUILDERS.get(rec.type)
        if builder is None:
            raise StepStreamUnsupported(f"entity type {rec.type} not supported by the streaming reader")
        return builder(self, rec.args)


def _enum_true(v) -> bool:
    return isinstance(v, _Enum) and v.name == "T"


def _b_cartesian_point(r: _Resolver, a: list) -> Point:
    coords = a[1]  # ('', (x, y, z))
    return Point(*[float(x) for x in coords])


def _b_direction(r: _Resolver, a: list) -> Direction:
    coords = a[1]
    return Direction(*[float(x) for x in coords])


def _b_vector(r: _Resolver, a: list) -> Direction:
    # VECTOR('', #orientation, magnitude) -> the (unit) direction; magnitude is
    # irrelevant for the consumers here (Line.dir is a Direction).
    return r.deref(a[1])


def _b_vertex_point(r: _Resolver, a: list) -> Point:
    return r.deref(a[1])  # VERTEX_POINT('', #point)


def _b_axis2_placement_3d(r: _Resolver, a: list) -> Axis2Placement3D:
    # AXIS2_PLACEMENT_3D('', #location, #axis, #ref_direction)
    location = r.deref(a[1])
    kwargs = {"location": location}
    if len(a) > 2 and isinstance(a[2], _Ref):
        kwargs["axis"] = r.deref(a[2])
    if len(a) > 3 and isinstance(a[3], _Ref):
        kwargs["ref_direction"] = r.deref(a[3])
    return Axis2Placement3D(**kwargs)


def _b_line(r: _Resolver, a: list) -> Line:
    return Line(pnt=r.deref(a[1]), dir=r.deref(a[2]))


def _b_circle(r: _Resolver, a: list) -> Circle:
    return Circle(position=r.deref(a[1]), radius=float(a[2]))


def _b_ellipse(r: _Resolver, a: list) -> Ellipse:
    # ELLIPSE('', #position, semi_axis_1, semi_axis_2)
    return Ellipse(position=r.deref(a[1]), semi_axis1=float(a[2]), semi_axis2=float(a[3]))


def _b_parabola(r: _Resolver, a: list) -> Parabola:
    # PARABOLA('', #position, focal_dist)
    return Parabola(position=r.deref(a[1]), focal_dist=float(a[2]))


def _b_hyperbola(r: _Resolver, a: list) -> Hyperbola:
    # HYPERBOLA('', #position, semi_axis, semi_imag_axis)
    return Hyperbola(position=r.deref(a[1]), semi_axis=float(a[2]), semi_imag_axis=float(a[3]))


def _b_polyline(r: _Resolver, a: list) -> PolyLine:
    # POLYLINE('', (#points))
    return PolyLine(points=[r.deref(p) for p in a[1]])


def _trim_value(r: _Resolver, item):
    """One trimming-select of a TRIMMED_CURVE: either a CARTESIAN_POINT ref (-> Point)
    or a PARAMETER_VALUE (-> float). The parser hands typed reals through as floats."""
    if isinstance(item, _Ref):
        return r.deref(item)
    try:
        return float(item)
    except (TypeError, ValueError):
        return r.deref(item)


def _b_trimmed_curve(r: _Resolver, a: list) -> TrimmedCurve:
    # TRIMMED_CURVE('', #basis_curve, (trim_1), (trim_2), sense_agreement, master_repr)
    t1 = a[2][0] if a[2] else 0.0
    t2 = a[3][0] if a[3] else 1.0
    return TrimmedCurve(
        basis_curve=r.deref(a[1]),
        trim1=_trim_value(r, t1),
        trim2=_trim_value(r, t2),
        sense_agreement=_enum_true(a[4]),
        master_representation=_enum_name(a[5]) if len(a) > 5 else "PARAMETER",
    )


def _b_composite_curve_segment(r: _Resolver, a: list) -> CompositeCurveSegment:
    # COMPOSITE_CURVE_SEGMENT(transition, same_sense, #parent_curve)
    return CompositeCurveSegment(parent_curve=r.deref(a[2]), same_sense=_enum_true(a[1]), transition=_enum_name(a[0]))


def _b_composite_curve(r: _Resolver, a: list) -> CompositeCurve:
    # COMPOSITE_CURVE('', (#segments), self_intersect)
    return CompositeCurve(segments=[r.deref(s) for s in a[1]])


def _b_pcurve(r: _Resolver, a: list) -> PCurve:
    # PCURVE('', #basis_surface, #reference_to_curve) — the 2D curve lives in a
    # DEFINITIONAL_REPRESENTATION; keep the basis surface and the (best-effort) ref so
    # the p-curve imports natively. Tessellation uses the 3D edge curve, not this.
    try:
        ref = r.deref(a[2])
    except Exception:  # noqa: BLE001 - representation wrapper not modelled; keep surface
        ref = None
    return PCurve(basis_surface=r.deref(a[1]), reference_curve=ref)


def _b_surface_curve(r: _Resolver, a: list):
    # SURFACE_CURVE / SEAM_CURVE('', #curve_3d, (#associated_geometry...), master)
    # The first arg is the 3D curve (LINE/CIRCLE/ELLIPSE/B-spline) — the edge geometry
    # we need; the associated p-curves only trim it on the face and aren't required to
    # build/tessellate via the backend. Unwrapping keeps OCCT-written STEP (any flavor)
    # readable without falling back to the kernel reader.
    return r.deref(a[1])


def _b_edge_curve(r: _Resolver, a: list) -> EdgeCurve:
    # EDGE_CURVE('', #start_vertex, #end_vertex, #edge_geometry, same_sense)
    start = r.deref(a[1])
    end = r.deref(a[2])
    geometry = r.deref(a[3])
    return EdgeCurve(start=start, end=end, edge_geometry=geometry, same_sense=_enum_true(a[4]))


def _b_oriented_edge(r: _Resolver, a: list) -> OrientedEdge:
    # ORIENTED_EDGE('', *, *, #edge_element, orientation)
    edge_element = r.deref(a[3])
    orientation = _enum_true(a[4])
    start, end = edge_element.start, edge_element.end
    if not orientation:
        start, end = end, start
    return OrientedEdge(start=start, end=end, edge_element=edge_element, orientation=orientation)


def _b_edge_loop(r: _Resolver, a: list) -> EdgeLoop:
    return EdgeLoop(edge_list=[r.deref(x) for x in a[1]])


class _DegenerateLoop:
    """A VERTEX_LOOP — a single-vertex 'loop' at a pole/apex of a closed surface
    (sphere/cone). Not a real boundary wire; dropped from a face's bounds."""

    __slots__ = ()


_DEGENERATE_LOOP = _DegenerateLoop()


def _b_vertex_loop(r: _Resolver, a: list) -> _DegenerateLoop:
    return _DEGENERATE_LOOP


def _b_face_bound(r: _Resolver, a: list) -> FaceBound:
    # FACE_BOUND / FACE_OUTER_BOUND('', #bound, orientation)
    return FaceBound(bound=r.deref(a[1]), orientation=_enum_true(a[2]))


def _b_plane(r: _Resolver, a: list) -> Plane:
    return Plane(position=r.deref(a[1]))


def _b_cylindrical_surface(r: _Resolver, a: list) -> CylindricalSurface:
    return CylindricalSurface(position=r.deref(a[1]), radius=float(a[2]))


def _b_conical_surface(r: _Resolver, a: list) -> ConicalSurface:
    # CONICAL_SURFACE('', #position, radius, semi_angle)
    return ConicalSurface(position=r.deref(a[1]), radius=float(a[2]), semi_angle=float(a[3]))


def _b_spherical_surface(r: _Resolver, a: list) -> SphericalSurface:
    # SPHERICAL_SURFACE('', #position, radius). A complete sphere face is closed in
    # both u and v, so OCCT bounds it with a single degenerate VERTEX_LOOP (no edges) —
    # which the reader filters out, leaving an empty-bounds AdvancedFace. The OCC face
    # builder makes the natural full sphere from the surface in that case.
    return SphericalSurface(position=r.deref(a[1]), radius=float(a[2]))


def _b_toroidal_surface(r: _Resolver, a: list) -> ToroidalSurface:
    # TOROIDAL_SURFACE('', #position, major_radius, minor_radius)
    return ToroidalSurface(position=r.deref(a[1]), major_radius=float(a[2]), minor_radius=float(a[3]))


def _b_axis1_placement(r: _Resolver, a: list) -> Axis1Placement:
    # AXIS1_PLACEMENT('', #location, #axis)
    axis = r.deref(a[2]) if len(a) > 2 and isinstance(a[2], _Ref) else Direction(0.0, 0.0, 1.0)
    return Axis1Placement(location=r.deref(a[1]), axis=axis)


def _b_surface_of_revolution(r: _Resolver, a: list) -> SurfaceOfRevolution:
    # SURFACE_OF_REVOLUTION('', #swept_curve, #axis_position(AXIS1_PLACEMENT))
    return SurfaceOfRevolution(swept_curve=r.deref(a[1]), axis_position=r.deref(a[2]))


def _b_surface_of_linear_extrusion(r: _Resolver, a: list) -> SurfaceOfLinearExtrusion:
    # SURFACE_OF_LINEAR_EXTRUSION('', #swept_curve, #extrusion_axis(VECTOR))
    vec_rec = r._pool.get(a[2].id) if isinstance(a[2], _Ref) else None
    direction = r.deref(a[2])  # _b_vector returns the (unit) Direction
    depth = 1.0
    if vec_rec is not None and vec_rec.type == "VECTOR":
        try:
            depth = float(vec_rec.args[2])  # VECTOR('', #orientation, magnitude)
        except (IndexError, TypeError, ValueError):
            depth = 1.0
    return SurfaceOfLinearExtrusion(
        swept_curve=r.deref(a[1]), position=None, extrusion_direction=direction, depth=depth
    )


def _b_rectangular_trimmed_surface(r: _Resolver, a: list) -> RectangularTrimmedSurface:
    # RECTANGULAR_TRIMMED_SURFACE('', #basis, u1, u2, v1, v2, usense, vsense)
    return RectangularTrimmedSurface(
        basis_surface=r.deref(a[1]),
        u1=float(a[2]),
        u2=float(a[3]),
        v1=float(a[4]),
        v2=float(a[5]),
        usense=_enum_true(a[6]),
        vsense=_enum_true(a[7]),
    )


def _b_curve_bounded_surface(r: _Resolver, a: list):
    # CURVE_BOUNDED_SURFACE('', #basis_surface, (#boundaries), implicit_outer). Modelled
    # as CurveBoundedPlane when the basis is a Plane (the common case); otherwise keep
    # the basis surface so the region still imports + tessellates from its bounds.
    basis = r.deref(a[1])
    bounds = [r.deref(b) for b in a[2]] if len(a) > 2 and a[2] else []
    if isinstance(basis, Plane) and bounds:
        return CurveBoundedPlane(basis_surface=basis, outer_boundary=bounds[0], inner_boundaries=bounds[1:])
    return basis


def _b_offset_surface(r: _Resolver, a: list) -> OffsetSurface:
    # OFFSET_SURFACE('', #basis_surface, distance, self_intersect)
    return OffsetSurface(basis_surface=r.deref(a[1]), distance=float(a[2]), self_intersect=_enum_true(a[3]))


# -- placements / point-on / replicas / offsets ----------------------------- #
def _b_axis2_placement_2d(r: _Resolver, a: list) -> Axis2Placement3D:
    # AXIS2_PLACEMENT_2D('', #location, #ref_direction) — promote to a 3D placement.
    kwargs = {"location": r.deref(a[1])}
    if len(a) > 2 and isinstance(a[2], _Ref):
        kwargs["ref_direction"] = r.deref(a[2])
    return Axis2Placement3D(**kwargs)


def _b_point_on_curve(r: _Resolver, a: list) -> PointOnCurve:
    # POINT_ON_CURVE('', #basis_curve, parameter)
    return PointOnCurve(basis_curve=r.deref(a[1]), parameter=float(a[2]))


def _b_point_on_surface(r: _Resolver, a: list) -> PointOnSurface:
    # POINT_ON_SURFACE('', #basis_surface, u, v)
    return PointOnSurface(basis_surface=r.deref(a[1]), u=float(a[2]), v=float(a[3]))


def _b_offset_curve_3d(r: _Resolver, a: list) -> OffsetCurve3D:
    # OFFSET_CURVE_3D('', #basis_curve, distance, self_intersect, #ref_direction)
    ref = r.deref(a[4]) if len(a) > 4 and isinstance(a[4], _Ref) else None
    return OffsetCurve3D(
        basis_curve=r.deref(a[1]), distance=float(a[2]), self_intersect=_enum_true(a[3]), ref_direction=ref
    )


def _b_replica(r: _Resolver, a: list):
    # CURVE_REPLICA / SURFACE_REPLICA('', #parent, #transformation): import the parent
    # geom natively (the rigid transform is recorded upstream, not applied here).
    return r.deref(a[1])


def _b_surface_patch(r: _Resolver, a: list):
    # SURFACE_PATCH('', #parent_surface, u_transition, v_transition, u_sense, v_sense)
    return r.deref(a[1])


def _b_rectangular_composite_surface(r: _Resolver, a: list) -> RectangularCompositeSurface:
    # RECTANGULAR_COMPOSITE_SURFACE('', ((#surface_patch, ...), ...))
    grid = []
    for row in a[1]:
        for patch in row:
            p = r.deref(patch)
            grid.append(getattr(p, "parent_surface", p))
    return RectangularCompositeSurface(segments=grid)


# -- solids / models -------------------------------------------------------- #
def _as_axis2(p):
    """Coerce an Axis1Placement (location + axis, no ref dir) into Axis2Placement3D so
    CSG-primitive geom (which expects a full placement) imports + builds."""
    if isinstance(p, Axis1Placement):
        return Axis2Placement3D(location=p.location, axis=p.axis)
    return p


def _b_faceted_brep(r: _Resolver, a: list) -> FacetedBrep:
    # FACETED_BREP('', #outer_closed_shell)  (planar-faced manifold solid)
    return FacetedBrep(outer=r.deref(a[1]))


def _b_block(r: _Resolver, a: list) -> Box:
    # BLOCK('', #position, x, y, z)
    return Box(position=_as_axis2(r.deref(a[1])), x_length=float(a[2]), y_length=float(a[3]), z_length=float(a[4]))


def _b_right_circular_cylinder(r: _Resolver, a: list) -> Cylinder:
    # RIGHT_CIRCULAR_CYLINDER('', #position, height, radius)
    return Cylinder(position=_as_axis2(r.deref(a[1])), radius=float(a[3]), height=float(a[2]))


def _b_right_circular_cone(r: _Resolver, a: list) -> Cone:
    # RIGHT_CIRCULAR_CONE('', #position, height, radius, semi_angle)
    return Cone(position=_as_axis2(r.deref(a[1])), bottom_radius=float(a[3]), height=float(a[2]))


def _b_sphere(r: _Resolver, a: list) -> Sphere:
    # SPHERE('', radius, #centre)
    return Sphere(center=r.deref(a[2]), radius=float(a[1]))


def _b_torus(r: _Resolver, a: list) -> Torus:
    # TORUS('', #position, major_radius, minor_radius)
    return Torus(position=r.deref(a[1]), major_radius=float(a[2]), minor_radius=float(a[3]))


def _b_extruded_area_solid(r: _Resolver, a: list) -> ExtrudedAreaSolid:
    # EXTRUDED_AREA_SOLID('', #swept_area, #position, #extruded_direction, depth)
    return ExtrudedAreaSolid(
        swept_area=r.deref(a[1]), position=r.deref(a[2]), extruded_direction=r.deref(a[3]), depth=float(a[4])
    )


def _b_revolved_area_solid(r: _Resolver, a: list) -> RevolvedAreaSolid:
    # REVOLVED_AREA_SOLID('', #swept_area, #axis(AXIS1_PLACEMENT), angle)
    return RevolvedAreaSolid(swept_area=r.deref(a[1]), position=None, axis=r.deref(a[2]), angle=float(a[3]))


def _b_boolean_result(r: _Resolver, a: list) -> BooleanResult:
    # BOOLEAN_RESULT(operator, #first_operand, #second_operand)
    return BooleanResult(
        operator=BoolOpEnum.from_str(_enum_name(a[0])), first_operand=r.deref(a[1]), second_operand=r.deref(a[2])
    )


def _b_csg_solid(r: _Resolver, a: list):
    # CSG_SOLID('', #tree_root_expression) — unwrap to its boolean tree / primitive.
    return r.deref(a[1])


def _b_geometric_set(r: _Resolver, a: list) -> GeometricCurveSet:
    # GEOMETRIC_SET / GEOMETRIC_CURVE_SET('', (#elements)) — loose points/curves/surfaces.
    return GeometricCurveSet(elements=[r.deref(x) for x in a[1]])


# -- AP242 tessellated geometry --------------------------------------------- #
def _b_coordinates_list(r: _Resolver, a: list):
    # COORDINATES_LIST('', npoints, ((x,y,z), ...)) — an inline point table.
    pts = a[2] if len(a) > 2 and isinstance(a[2], (list, tuple)) else a[-1]
    return [Point(*[float(c) for c in p]) for p in pts]


def _b_triangulated_face_set(r: _Resolver, a: list) -> TriangulatedFaceSet:
    # *_TRIANGULATED_FACE_SET / TRIANGULATED_SURFACE_SET: coordinates first; the rest
    # carry triangle index triples (ints, kept 1-based to match the IFC convention) and
    # optional per-vertex normals (reals). Argument order varies by exporter, so classify.
    coords = []
    first = r.deref(a[1]) if isinstance(a[1], _Ref) else None
    if isinstance(first, list) and first and isinstance(first[0], Point):
        coords = first
    normals: list = []
    tris: list = []
    for arg in a[2:]:
        if isinstance(arg, (list, tuple)) and arg and isinstance(arg[0], (list, tuple)) and len(arg[0]) == 3:
            if all(isinstance(x, int) for x in arg[0]):
                tris = arg
            elif all(isinstance(x, (int, float)) for x in arg[0]):
                normals = [Direction(*[float(c) for c in n]) for n in arg]
    indices = [int(i) for tri in tris for i in tri]  # flattened, 1-based
    return TriangulatedFaceSet(coordinates=coords, normals=normals, indices=indices)


def _b_tessellated_shell(r: _Resolver, a: list):
    # TESSELLATED_SHELL / TESSELLATED_SOLID('', (#items), ...) — wraps tessellated face
    # set(s). Return the single item; merge coords+indices (offsetting) for several.
    items = [r.deref(x) for x in a[1]] if isinstance(a[1], (list, tuple)) else [r.deref(a[1])]
    items = [it for it in items if isinstance(it, TriangulatedFaceSet)]
    if len(items) == 1:
        return items[0]
    coords: list = []
    indices: list = []
    normals: list = []
    for it in items:
        off = len(coords)
        coords.extend(it.coordinates)
        normals.extend(it.normals)
        indices.extend(i + off for i in it.indices)  # indices are 1-based; offset by prior count
    return TriangulatedFaceSet(coordinates=coords, normals=normals, indices=indices)


def _b_manifold_surface_shape_rep(r: _Resolver, a: list):
    # MANIFOLD_SURFACE_SHAPE_REPRESENTATION('', (#items), #context): a shape rep whose
    # items are shells / surface models. Return the single item directly, else wrap the
    # shells' faces in one OpenShell so the representation imports + renders.
    items = [r.deref(x) for x in a[1]]
    if len(items) == 1:
        return items[0]
    faces = []
    for it in items:
        faces.extend(getattr(it, "cfs_faces", None) or getattr(it, "sbsm_boundary", None) or [])
    return OpenShell(cfs_faces=faces) if faces else (items[0] if items else None)


def _b_advanced_face(r: _Resolver, a: list) -> AdvancedFace:
    # ADVANCED_FACE('', (#bounds), #face_surface, same_sense)
    # Drop degenerate vertex-loop bounds (pole/apex of a closed surface) — they
    # carry no boundary wire; the surface trims to its real edge-loop bounds.
    bounds = [fb for fb in (r.deref(x) for x in a[1]) if not isinstance(fb.bound, _DegenerateLoop)]
    return AdvancedFace(bounds=bounds, face_surface=r.deref(a[2]), same_sense=_enum_true(a[3]))


def _b_closed_shell(r: _Resolver, a: list) -> ClosedShell:
    return ClosedShell(cfs_faces=[r.deref(x) for x in a[1]])


def _b_open_shell(r: _Resolver, a: list) -> OpenShell:
    # OPEN_SHELL('', (#faces)) — a pure (thickness-less) surface shell.
    return OpenShell(cfs_faces=[r.deref(x) for x in a[1]])


def _b_poly_loop(r: _Resolver, a: list) -> PolyLoop:
    # POLY_LOOP('', (#points)) — a faceted-brep polygon boundary.
    return PolyLoop(polygon=[r.deref(p) for p in a[1]])


def _b_connected_face_set(r: _Resolver, a: list) -> OpenShell:
    # CONNECTED_FACE_SET('', (#faces)) — the supertype of CLOSED/OPEN_SHELL. Import as an
    # OpenShell so it both maps to a native shell type and tessellates from its faces.
    return OpenShell(cfs_faces=[r.deref(x) for x in a[1]])


def _b_subface(r: _Resolver, a: list):
    # SUBFACE('', (#bounds), #parent_face): a region of a parent face. The sub-region has
    # no own surface — reuse the parent's geometry with the sub-bounds so it imports +
    # builds (rare; over-covers only if the sub-bounds are ignored).
    parent = r.deref(a[2])
    bounds = [fb for fb in (r.deref(x) for x in a[1]) if not isinstance(fb.bound, _DegenerateLoop)]
    fs = getattr(parent, "face_surface", None)
    if fs is not None:
        return AdvancedFace(bounds=bounds or parent.bounds, face_surface=fs, same_sense=True)
    return parent


def _b_subedge(r: _Resolver, a: list):
    # SUBEDGE('', #start, #end, #parent_edge): a segment of a parent edge. Reuse the
    # parent's 3D curve with the sub start/end vertices.
    parent = r.deref(a[3])
    geom = getattr(parent, "edge_geometry", parent)
    return EdgeCurve(start=r.deref(a[1]), end=r.deref(a[2]), edge_geometry=geom, same_sense=True)


def _b_shell_based_surface_model(r: _Resolver, a: list) -> ShellBasedSurfaceModel:
    # SHELL_BASED_SURFACE_MODEL('', (#shells)) — how a surface (no-thickness) shape
    # is wrapped, e.g. a curved B-spline plate exported as an open shell.
    return ShellBasedSurfaceModel(sbsm_boundary=[r.deref(x) for x in a[1]])


# -- B-splines -------------------------------------------------------------- #
def _enum_name(v) -> str:
    return v.name if isinstance(v, _Enum) else str(v)


def _make_bspline_curve(r, degree, cp_refs, curve_form, closed, si, mults, knots, knot_spec, weights=None):
    cps = [r.deref(ref) for ref in cp_refs]
    common = dict(
        degree=int(degree),
        control_points_list=cps,
        curve_form=BSplineCurveFormEnum(_enum_name(curve_form)),
        closed_curve=_enum_true(closed),
        self_intersect=_enum_true(si),
        knot_multiplicities=[int(x) for x in mults],
        knots=[float(x) for x in knots],
        knot_spec=KnotType.from_str(_enum_name(knot_spec)),
    )
    if weights is not None:
        return RationalBSplineCurveWithKnots(**common, weights_data=[float(w) for w in weights])
    return BSplineCurveWithKnots(**common)


def _make_bspline_surface(
    r,
    u_deg,
    v_deg,
    cp_grid,
    surf_form,
    u_closed,
    v_closed,
    si,
    u_mults,
    v_mults,
    u_knots,
    v_knots,
    knot_spec,
    weights=None,
):
    cps = [[r.deref(ref) for ref in row] for row in cp_grid]
    common = dict(
        u_degree=int(u_deg),
        v_degree=int(v_deg),
        control_points_list=cps,
        surface_form=BSplineSurfaceForm.from_str(_enum_name(surf_form)),
        u_closed=_enum_true(u_closed),
        v_closed=_enum_true(v_closed),
        self_intersect=_enum_true(si),
        u_multiplicities=[int(x) for x in u_mults],
        v_multiplicities=[int(x) for x in v_mults],
        u_knots=[float(x) for x in u_knots],
        v_knots=[float(x) for x in v_knots],
        knot_spec=KnotType.from_str(_enum_name(knot_spec)),
    )
    if weights is not None:
        # Rational B-spline surface: carry the weight grid into the native geom. The OCC
        # backend builds a rational Geom_BSplineSurface from it and trims the face from
        # the 3D boundary wire (OCC computes the p-curves), so these solids tessellate
        # natively instead of being skipped. (Earlier this raised on the theory that
        # 3D->UV reprojection collapses the face; in practice OCC's MakeFace handles the
        # rational surface from the 3D wire — verified building all faces of the skipped
        # solids. Authored 2D p-curves, when a file supplies them, still take the
        # SURFACE_CURVE/pcurve path and override this.)
        return RationalBSplineSurfaceWithKnots(**common, weights_data=[[float(w) for w in row] for row in weights])
    return BSplineSurfaceWithKnots(**common)


def _b_bspline_curve_with_knots(r: _Resolver, a: list):
    # B_SPLINE_CURVE_WITH_KNOTS('', degree, (cps), form, .closed., .si., (mults), (knots), spec)
    return _make_bspline_curve(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8])


def _b_bspline_surface_with_knots(r: _Resolver, a: list):
    # B_SPLINE_SURFACE_WITH_KNOTS('', u_deg, v_deg, (cp grid), form, .uc., .vc., .si.,
    #                             (u_mults), (v_mults), (u_knots), (v_knots), spec)
    return _make_bspline_surface(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12])


def _implicit_bspline_knots(knot_type: str, degree: int, n_cps: int):
    """Knots + multiplicities for a B-spline subtype that omits explicit knots
    (BEZIER / UNIFORM / QUASI_UNIFORM), derived from degree + control-point count per
    ISO 10303-42. Lets these map onto the existing (Rational)BSplineCurve/Surface geom."""
    d = int(degree)
    n = int(n_cps)
    if knot_type == "PIECEWISE_BEZIER_KNOTS":
        segs = max(1, (n - 1) // d) if d else 1  # piecewise-Bezier segment count
        knots = [float(i) for i in range(segs + 1)]
        mults = [d + 1] + [d] * (segs - 1) + [d + 1]
    elif knot_type == "QUASI_UNIFORM_KNOTS":
        n_interior = max(0, n - d - 1)  # clamped ends, uniform mult-1 interior
        knots = [float(i) for i in range(n_interior + 2)]
        mults = [d + 1] + [1] * n_interior + [d + 1]
    else:  # UNIFORM_KNOTS — open uniform, every knot mult 1
        n_knots = n + d + 1
        knots = [float(i) for i in range(n_knots)]
        mults = [1] * n_knots
    return knots, mults


def _b_bezier_curve(r: _Resolver, a: list):
    # BEZIER_CURVE / UNIFORM_CURVE / QUASI_UNIFORM_CURVE inherit B_SPLINE_CURVE's args
    # ('', degree, (cps), form, closed, si) and imply their knots from degree+#cps.
    k, m = _implicit_bspline_knots("PIECEWISE_BEZIER_KNOTS", a[1], len(a[2]))
    return _make_bspline_curve(r, a[1], a[2], a[3], a[4], a[5], m, k, "PIECEWISE_BEZIER_KNOTS")


def _b_uniform_curve(r: _Resolver, a: list):
    k, m = _implicit_bspline_knots("UNIFORM_KNOTS", a[1], len(a[2]))
    return _make_bspline_curve(r, a[1], a[2], a[3], a[4], a[5], m, k, "UNIFORM_KNOTS")


def _b_quasi_uniform_curve(r: _Resolver, a: list):
    k, m = _implicit_bspline_knots("QUASI_UNIFORM_KNOTS", a[1], len(a[2]))
    return _make_bspline_curve(r, a[1], a[2], a[3], a[4], a[5], m, k, "QUASI_UNIFORM_KNOTS")


def _b_bezier_surface(r: _Resolver, a: list):
    # BEZIER/UNIFORM/QUASI_UNIFORM_SURFACE inherit B_SPLINE_SURFACE's args
    # ('', u_deg, v_deg, (cp grid), form, u_closed, v_closed, si); knots implied per dir.
    n_u = len(a[3])
    n_v = len(a[3][0]) if a[3] else 0
    uk, um = _implicit_bspline_knots("PIECEWISE_BEZIER_KNOTS", a[1], n_u)
    vk, vm = _implicit_bspline_knots("PIECEWISE_BEZIER_KNOTS", a[2], n_v)
    return _make_bspline_surface(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], um, vm, uk, vk, "PIECEWISE_BEZIER_KNOTS")


def _b_uniform_surface(r: _Resolver, a: list):
    n_u = len(a[3])
    n_v = len(a[3][0]) if a[3] else 0
    uk, um = _implicit_bspline_knots("UNIFORM_KNOTS", a[1], n_u)
    vk, vm = _implicit_bspline_knots("UNIFORM_KNOTS", a[2], n_v)
    return _make_bspline_surface(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], um, vm, uk, vk, "UNIFORM_KNOTS")


def _b_quasi_uniform_surface(r: _Resolver, a: list):
    n_u = len(a[3])
    n_v = len(a[3][0]) if a[3] else 0
    uk, um = _implicit_bspline_knots("QUASI_UNIFORM_KNOTS", a[1], n_u)
    vk, vm = _implicit_bspline_knots("QUASI_UNIFORM_KNOTS", a[2], n_v)
    return _make_bspline_surface(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], um, vm, uk, vk, "QUASI_UNIFORM_KNOTS")


def _build_complex(r: _Resolver, subs: dict):
    """Build a rational/non-rational B-spline from a complex record's sub-entities.
    Note: complex sub-entity args have NO leading '' name, so they are 0-indexed."""
    if "B_SPLINE_SURFACE" in subs and "B_SPLINE_SURFACE_WITH_KNOTS" in subs:
        s = subs["B_SPLINE_SURFACE"]  # [u_deg, v_deg, cp_grid, form, u_closed, v_closed, si]
        k = subs["B_SPLINE_SURFACE_WITH_KNOTS"]  # [u_mults, v_mults, u_knots, v_knots, spec]
        rat = subs.get("RATIONAL_B_SPLINE_SURFACE")  # [weight_grid] or None
        weights = rat[0] if rat else None
        return _make_bspline_surface(r, s[0], s[1], s[2], s[3], s[4], s[5], s[6], k[0], k[1], k[2], k[3], k[4], weights)
    if "B_SPLINE_CURVE" in subs and "B_SPLINE_CURVE_WITH_KNOTS" in subs:
        c = subs["B_SPLINE_CURVE"]  # [degree, cps, form, closed, si]
        k = subs["B_SPLINE_CURVE_WITH_KNOTS"]  # [mults, knots, spec]
        rat = subs.get("RATIONAL_B_SPLINE_CURVE")  # [weights] or None
        weights = rat[0] if rat else None
        return _make_bspline_curve(r, c[0], c[1], c[2], c[3], c[4], k[0], k[1], k[2], weights)
    raise StepStreamUnsupported(f"complex entity {sorted(subs)} not supported by the streaming reader")


_BUILDERS = {
    "CARTESIAN_POINT": _b_cartesian_point,
    "DIRECTION": _b_direction,
    "VECTOR": _b_vector,
    "VERTEX_POINT": _b_vertex_point,
    "AXIS2_PLACEMENT_3D": _b_axis2_placement_3d,
    "AXIS2_PLACEMENT_2D": _b_axis2_placement_2d,
    "POINT_ON_CURVE": _b_point_on_curve,
    "POINT_ON_SURFACE": _b_point_on_surface,
    "OFFSET_CURVE_3D": _b_offset_curve_3d,
    "INTERSECTION_CURVE": _b_surface_curve,
    "CURVE_REPLICA": _b_replica,
    "SURFACE_REPLICA": _b_replica,
    "SURFACE_PATCH": _b_surface_patch,
    "RECTANGULAR_COMPOSITE_SURFACE": _b_rectangular_composite_surface,
    "LINE": _b_line,
    "CIRCLE": _b_circle,
    "ELLIPSE": _b_ellipse,
    "PARABOLA": _b_parabola,
    "HYPERBOLA": _b_hyperbola,
    "POLYLINE": _b_polyline,
    "TRIMMED_CURVE": _b_trimmed_curve,
    "COMPOSITE_CURVE": _b_composite_curve,
    "COMPOSITE_CURVE_SEGMENT": _b_composite_curve_segment,
    "PCURVE": _b_pcurve,
    "B_SPLINE_CURVE_WITH_KNOTS": _b_bspline_curve_with_knots,
    "B_SPLINE_SURFACE_WITH_KNOTS": _b_bspline_surface_with_knots,
    "BEZIER_CURVE": _b_bezier_curve,
    "UNIFORM_CURVE": _b_uniform_curve,
    "QUASI_UNIFORM_CURVE": _b_quasi_uniform_curve,
    "BEZIER_SURFACE": _b_bezier_surface,
    "UNIFORM_SURFACE": _b_uniform_surface,
    "QUASI_UNIFORM_SURFACE": _b_quasi_uniform_surface,
    "SURFACE_CURVE": _b_surface_curve,
    "SEAM_CURVE": _b_surface_curve,
    "EDGE_CURVE": _b_edge_curve,
    "ORIENTED_EDGE": _b_oriented_edge,
    "EDGE_LOOP": _b_edge_loop,
    "VERTEX_LOOP": _b_vertex_loop,
    "FACE_BOUND": _b_face_bound,
    "FACE_OUTER_BOUND": _b_face_bound,
    "AXIS1_PLACEMENT": _b_axis1_placement,
    "PLANE": _b_plane,
    "CYLINDRICAL_SURFACE": _b_cylindrical_surface,
    "CONICAL_SURFACE": _b_conical_surface,
    "SPHERICAL_SURFACE": _b_spherical_surface,
    "TOROIDAL_SURFACE": _b_toroidal_surface,
    "SURFACE_OF_REVOLUTION": _b_surface_of_revolution,
    "SURFACE_OF_LINEAR_EXTRUSION": _b_surface_of_linear_extrusion,
    "RECTANGULAR_TRIMMED_SURFACE": _b_rectangular_trimmed_surface,
    "CURVE_BOUNDED_SURFACE": _b_curve_bounded_surface,
    "OFFSET_SURFACE": _b_offset_surface,
    "ADVANCED_FACE": _b_advanced_face,
    "FACE_SURFACE": _b_advanced_face,
    "SUBFACE": _b_subface,
    "SUBEDGE": _b_subedge,
    "POLY_LOOP": _b_poly_loop,
    "CONNECTED_FACE_SET": _b_connected_face_set,
    "CLOSED_SHELL": _b_closed_shell,
    "OPEN_SHELL": _b_open_shell,
    # ORIENTED_CLOSED_SHELL('', *, #base_closed_shell, orientation): a CLOSED_SHELL reused
    # with an orientation flag (e.g. the void shells of a BREP_WITH_VOIDS). The supertype's
    # cfs_faces field (arg 1) is DERIVED in the oriented subtype -> emitted as ``*`` (the
    # _STAR sentinel); the real base shell is arg 2, the orientation arg 3. Resolve arg 2 —
    # dereffing arg 1 yields the bare ``*`` sentinel (0 faces), which silently dropped every
    # void shell. The orientation only flips face normals, which tessellation treats as
    # double-sided. No geometry left behind.
    "ORIENTED_CLOSED_SHELL": lambda r, a: r.deref(a[2]),
    "SHELL_BASED_SURFACE_MODEL": _b_shell_based_surface_model,
    # solids / models
    "FACETED_BREP": _b_faceted_brep,
    "BLOCK": _b_block,
    "RIGHT_CIRCULAR_CYLINDER": _b_right_circular_cylinder,
    "RIGHT_CIRCULAR_CONE": _b_right_circular_cone,
    "SPHERE": _b_sphere,
    "TORUS": _b_torus,
    "EXTRUDED_AREA_SOLID": _b_extruded_area_solid,
    "REVOLVED_AREA_SOLID": _b_revolved_area_solid,
    "BOOLEAN_RESULT": _b_boolean_result,
    "CSG_SOLID": _b_csg_solid,
    "GEOMETRIC_CURVE_SET": _b_geometric_set,
    "GEOMETRIC_SET": _b_geometric_set,
    "MANIFOLD_SURFACE_SHAPE_REPRESENTATION": _b_manifold_surface_shape_rep,
    # AP242 tessellated geometry
    "COORDINATES_LIST": _b_coordinates_list,
    "TRIANGULATED_FACE_SET": _b_triangulated_face_set,
    "TRIANGULATED_SURFACE_SET": _b_triangulated_face_set,
    "COMPLEX_TRIANGULATED_FACE_SET": _b_triangulated_face_set,
    "TESSELLATED_SHELL": _b_tessellated_shell,
    "TESSELLATED_SOLID": _b_tessellated_shell,
}


# Top-level renderable geometry roots — one yielded Geometry per record. A solid
def _b_brep_with_voids(r: _Resolver, a: list) -> ClosedShell:
    """BREP_WITH_VOIDS('name', outer_shell, (void_shells)) — a solid with internal cavities.

    The void shells (arg 2, each an ORIENTED_CLOSED_SHELL -> CLOSED_SHELL) are the cavity
    boundaries. They are invisible from outside, but step2glb (the parity reference)
    tessellates every ADVANCED_FACE including the voids, so we render them too: drop them and
    the face count falls short of the file's (e.g. 2450 faces across 38 voided solids in one
    assembly). Each void face keeps its own ``same_sense`` (cavity normals point inward, as in
    the file) — we do not reorient, matching step2glb's straight per-face tessellation.
    """
    outer = r.deref(a[1])
    voids = a[2] if len(a) > 2 else None
    outer_faces = getattr(outer, "cfs_faces", None)
    if not voids or outer_faces is None:
        return outer
    merged = list(outer_faces)
    for v in voids:
        shell = r.deref(v)
        v_faces = getattr(shell, "cfs_faces", None)
        if v_faces:
            merged.extend(v_faces)
    return ClosedShell(cfs_faces=merged)


# (MANIFOLD_SOLID_BREP -> its ClosedShell), a solid with internal cavities
# (BREP_WITH_VOIDS -> outer shell + void shells, all faces, for step2glb parity), and a pure
# surface shell (SHELL_BASED_SURFACE_MODEL -> ShellBasedSurfaceModel). Shells nested inside
# these are reached by reference, never yielded on their own, so no double-count. Without the
# BREP_WITH_VOIDS entry these solids were silently dropped (38 of them in one CAD assembly).
_ROOT_BUILDERS = {
    "MANIFOLD_SOLID_BREP": lambda r, a: r.deref(a[1]),
    "BREP_WITH_VOIDS": _b_brep_with_voids,
    "SHELL_BASED_SURFACE_MODEL": _b_shell_based_surface_model,
    # additional renderable roots (CSG primitives, swept + faceted solids, boolean trees)
    "FACETED_BREP": _b_faceted_brep,
    "BLOCK": _b_block,
    "RIGHT_CIRCULAR_CYLINDER": _b_right_circular_cylinder,
    "RIGHT_CIRCULAR_CONE": _b_right_circular_cone,
    "SPHERE": _b_sphere,
    "TORUS": _b_torus,
    "EXTRUDED_AREA_SOLID": _b_extruded_area_solid,
    "REVOLVED_AREA_SOLID": _b_revolved_area_solid,
    "CSG_SOLID": _b_csg_solid,
    "TRIANGULATED_FACE_SET": _b_triangulated_face_set,
    "TRIANGULATED_SURFACE_SET": _b_triangulated_face_set,
    "COMPLEX_TRIANGULATED_FACE_SET": _b_triangulated_face_set,
    "TESSELLATED_SHELL": _b_tessellated_shell,
    "TESSELLATED_SOLID": _b_tessellated_shell,
}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def stream_read_step(
    filepath: str | Path, *, local_pool: bool = True, tolerant: bool = False, on_total=None
) -> Iterator[Geometry]:
    """Lazily stream a STEP file, yielding one :class:`Geometry` per solid.

    Each yielded ``Geometry`` wraps a :class:`~ada.geom.surfaces.ClosedShell`
    built from the solid's ``MANIFOLD_SOLID_BREP``, ready to hand to
    ``active_backend().build(geom)`` for tessellation.

    Parameters
    ----------
    filepath:
        Path to the ``.step`` / ``.stp`` file.
    local_pool:
        When ``True`` (default) the entity pool is cleared at every solid
        boundary — constant memory, valid only for files whose solids are
        written as self-contained, bottom-up contiguous blocks (definitions
        precede references), which is what the adapy streaming emitter produces.

        When ``False`` the reader does a two-pass deferred resolution (load the
        whole entity table, then resolve each solid). This holds the full pool
        but correctly handles **forward references** — a ``MANIFOLD_SOLID_BREP``
        written before its shell/faces/points, which is how OpenCASCADE and most
        other writers emit STEP. Use ``False`` for arbitrary STEP.
    tolerant:
        When ``True`` a solid using an unsupported surface/curve (e.g. a spherical
        or rational-B-spline face) is *skipped* and the reader keeps going, instead
        of raising ``StepStreamUnsupported``. Lets a large mixed CAD file read its
        supported solids kernel-free rather than dropping the whole file to OCC (and
        OOM-ing); a one-line summary of what was skipped is logged at the end.
    on_total:
        Optional callback ``on_total(n_roots)`` fired once (two-pass paths only) after
        the index scan, before any solid is yielded — lets a caller show conversion
        progress against the total solid count.
    """
    filepath = Path(filepath)
    skipped: Counter = Counter()

    if not local_pool:
        yield from _read_two_pass(filepath, tolerant=tolerant, skipped=skipped, on_total=on_total)
        _log_skips(filepath, skipped)
        return

    pool: dict[int, _Rec] = {}
    resolver = _Resolver(pool)
    n_solids = 0

    with filepath.open("r", encoding="utf-8", errors="replace") as fh:
        for stmt in _iter_statements(fh):
            parsed = _parse_statement(stmt)
            if parsed is None:
                # header keywords (ISO-10303-21, HEADER, DATA, ENDSEC, ...) and
                # complex/instance records starting with '(' — not geometry roots.
                continue
            inst_id, etype, args = parsed

            root = _ROOT_BUILDERS.get(etype)
            if root is not None:
                name = _solid_name(args, n_solids)
                geom = _try_resolve_root(resolver, name, root, args, tolerant=tolerant, skipped=skipped)
                if geom is not None:
                    n_solids += 1
                    yield Geometry(id=name, geometry=geom)
                pool.clear()  # per-root clear: constant memory, bottom-up only
                resolver.reset_cache()
                continue

            pool[inst_id] = _Rec(etype, args)
    _log_skips(filepath, skipped)


def _parse_statement(stmt: str):
    """Parse one Part-21 statement into (instance_id, type, args), or None for
    header keywords. A *complex* record ``#id=(NAME(..)NAME(..)..)`` (how STEP
    encodes rational B-splines) returns type ``_COMPLEX`` with args a dict
    ``{NAME: subargs}``."""
    m = _HEADER_RE.match(stmt)
    if m is not None:
        args, _ = _parse_seq(stmt, m.end(), ")")  # m.end() is just past the '('
        return int(m.group(1)), m.group(2), args
    cm = _COMPLEX_RE.match(stmt)
    if cm is None:
        return None
    return int(cm.group(1)), _COMPLEX, _parse_complex(stmt, cm.end())  # cm.end() just past the outer '('


def _parse_complex(s: str, i: int) -> dict:
    """Parse a complex record body ``NAME(args)NAME(args)...)`` into {NAME: args}."""
    subs: dict[str, list] = {}
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n or s[i] == ")":
            break
        j = i
        while j < n and (s[j].isalnum() or s[j] == "_"):
            j += 1
        name = s[i:j]
        args, i = _parse_seq(s, j + 1, ")")  # s[j] == '('
        subs[name] = args
    return subs


def _solid_name(args: list, n_solids: int, product: str | None = None) -> str:
    # The owning STEP PRODUCT name always wins (this is the meaningful part tag
    # and what step2glb uses, so the two engines' parts line up by name); fall
    # back to the solid's own MANIFOLD_SOLID_BREP name, then a generic ordinal.
    own = args[0] if args and isinstance(args[0], str) and args[0] else None
    return product or own or f"solid_{n_solids + 1}"


def _yield_instances(name: str, geom, color, tmap_entry):
    """Yield ONE Geometry for this (single) solid, carrying its list of world-placement
    matrices plus the matching assembly paths. ``tmap_entry`` is ``(mats, paths)`` (one
    4x4 + one root-first path per placed instance) or None for the single, no-transform
    case. The downstream tessellator meshes the local shell ONCE and applies each matrix
    to that mesh, so a part instanced N times meshes once. A lone identity matrix
    collapses to ``transforms=None`` so flat files and single-instance solids are
    byte-for-byte unchanged."""
    import numpy as np

    mats, paths = tmap_entry if tmap_entry else (None, None)
    if mats and len(mats) == 1 and np.allclose(mats[0], np.eye(4), atol=1e-12):
        mats, paths = None, None
    yield Geometry(id=name, geometry=geom, color=color, transforms=(mats or None), instance_paths=(paths or None))


def _short_reason(ex: Exception) -> str:
    """A compact, groupable label for a skipped-solid summary."""
    s = str(ex)
    m = re.match(r"complex entity (\[[^\]]*\])", s)
    if m:
        return f"complex {m.group(1)}"
    # leading ALL-CAPS entity token, e.g. "SPHERICAL_SURFACE not yet ..." or
    # "entity type B_SPLINE_SURFACE ..."
    m = re.match(r"(?:entity type )?([A-Z][A-Z_0-9]{2,})", s)
    if m:
        return m.group(1)
    return s.split(" (")[0].split(";")[0][:40]


def _try_resolve_root(resolver: "_Resolver", name: str, root_builder, args: list, *, tolerant, skipped):
    """Build one root geometry (solid shell / surface model); return None on a bad
    root. A StepStreamUnsupported root re-raises (so the caller can fall back to OCC)
    unless ``tolerant`` — then it is tallied in ``skipped`` and dropped so the rest of
    the file still reads kernel-free."""
    try:
        return root_builder(resolver, args)
    except StepStreamUnsupported as ex:
        if not tolerant:
            raise
        skipped[_short_reason(ex)] += 1
        return None
    except Exception as ex:  # noqa: BLE001 - report and skip a bad root
        logger.warning(f"stream_read_step: skipping {name!r}: {ex}")
        skipped["error"] += 1
        return None


def _log_skips(filepath: Path, skipped) -> None:
    if skipped:
        total = sum(skipped.values())
        logger.info("stream_read_step: %s: skipped %d unsupported solid(s) — %s", filepath.name, total, dict(skipped))


# Files above this size resolve against an mmap + offset-index pool (parse each
# entity on demand) instead of materialising every entity as a parsed _Rec — the
# dict pool is ~7x the file size in Python objects (5+ GB on a 750 MB CAD assembly),
# which OOMs a worker pod. Small files keep the simpler/faster dict pool.
_LAZY_POOL_THRESHOLD = 64 * 1024 * 1024
_WS = frozenset(b" \t\r\n")


def _read_two_pass(
    filepath: Path, *, tolerant: bool = False, skipped=None, low_memory: bool | None = None, on_total=None
):
    """General STEP (forward references): resolve each root against the full entity
    table. Large files use a constant-memory mmap + offset-index pool so a worker pod
    stays within budget; small files use a plain parsed-entity dict."""
    if skipped is None:
        skipped = Counter()
    if low_memory is None:
        try:
            low_memory = filepath.stat().st_size > _LAZY_POOL_THRESHOLD
        except OSError:
            low_memory = False
    gen = _read_two_pass_lazy if low_memory else _read_two_pass_dict
    yield from gen(filepath, tolerant=tolerant, skipped=skipped, on_total=on_total)


def _read_two_pass_dict(filepath: Path, *, tolerant: bool, skipped, on_total=None):
    pool: dict[int, _Rec] = {}
    root_ids: list[int] = []
    styled_ids: list[int] = []
    cdsr_ids: list[int] = []
    srr_ids: list[int] = []
    absr_ids: list[int] = []
    sdr_ids: list[int] = []
    with filepath.open("r", encoding="utf-8", errors="replace") as fh:
        for stmt in _iter_statements(fh):
            parsed = _parse_statement(stmt)
            if parsed is None:
                continue
            inst_id, etype, args = parsed
            pool[inst_id] = _Rec(etype, args)
            if etype in _ROOT_BUILDERS:
                root_ids.append(inst_id)
            elif etype == "STYLED_ITEM":
                styled_ids.append(inst_id)
            elif etype == "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION":
                cdsr_ids.append(inst_id)
            elif etype == "SHAPE_REPRESENTATION_RELATIONSHIP":
                srr_ids.append(inst_id)
            elif etype == "ADVANCED_BREP_SHAPE_REPRESENTATION":
                absr_ids.append(inst_id)
            elif etype == "SHAPE_DEFINITION_REPRESENTATION":
                sdr_ids.append(inst_id)

    colour_map = _build_colour_map(pool.get, styled_ids)
    tmap, prod_names = _build_transform_map(
        pool.get, root_ids, cdsr_ids, srr_ids, absr_ids, sdr_ids, global_scale=detect_step_length_unit_scale(filepath)
    )
    if on_total is not None:
        on_total(len(root_ids))
    resolver = _Resolver(pool)
    n_solids = 0
    for rid in root_ids:
        rec = pool[rid]
        name = _solid_name(rec.args, n_solids, prod_names.get(rid))
        resolver.reset_cache()
        geom = _try_resolve_root(resolver, name, _ROOT_BUILDERS[rec.type], rec.args, tolerant=tolerant, skipped=skipped)
        if geom is None:
            continue
        n_solids += 1
        color = _as_color(colour_map.get(rid))
        yield from _yield_instances(name, geom, color, tmap.get(rid))


def _stmt_end(mm, start: int, n: int) -> int:
    """Byte index of the statement-terminating ';' at/after ``start`` (a ';' inside a
    single-quoted string doesn't terminate). Returns ``n`` if there is none."""
    end = mm.find(b";", start)
    if end < 0:
        return n
    while mm[start:end].count(b"'") & 1:  # the ';' fell inside an open string
        nxt = mm.find(b";", end + 1)
        if nxt < 0:
            return n
        end = nxt
    return end


def _read_statement_at(mm, start: int, n: int) -> str:
    return mm[start : _stmt_end(mm, start, n)].decode("utf-8", "replace")


# Initial ``pread`` window for one entity statement. Most STEP entities are well under
# this; the rare larger one (e.g. a B-spline surface with many poles) grows by doubling.
_PREAD_CHUNK = 8192


def _stmt_end_bytes(buf: bytes) -> int:
    """Index of the terminating ';' in the chunk ``buf`` (a ';' inside a single-quoted
    string doesn't count). ``buf`` starts AT the entity offset, mirroring ``_stmt_end``'s
    ``mm[start:end]`` quote-parity check. Returns -1 when the terminator can't yet be
    decided — no ';' at all, OR the scan ran out of bytes inside an open quote — so the
    caller grows the window and retries."""
    end = buf.find(b";")
    if end < 0:
        return -1
    while buf.count(b"'", 0, end) & 1:  # the ';' fell inside an open string
        nxt = buf.find(b";", end + 1)
        if nxt < 0:
            return -1  # open quote runs past the chunk -> need more bytes
        end = nxt
    return end


def _read_statement_pread(fd: int, offset: int, file_size: int) -> str:
    """Read the entity statement starting at ``offset`` via ``os.pread``, growing the
    window until the terminating ';' is found or EOF. Unlike the mmap slice, the file
    pages this touches stay in the reclaimable OS page cache and never enter the process
    address space / VmRSS — which is what the worker memory caps measure."""
    import os

    chunk = _PREAD_CHUNK
    while True:
        buf = os.pread(fd, chunk, offset)
        end = _stmt_end_bytes(buf)
        if end >= 0:
            return buf[:end].decode("utf-8", "replace")
        # No terminator yet. If the read came up short, we are at EOF: return the rest
        # (matches ``_stmt_end`` returning ``n`` for an unterminated final statement).
        if len(buf) < chunk or offset + len(buf) >= file_size:
            return buf.decode("utf-8", "replace")
        chunk *= 2


def _is_kw_byte(b: int) -> bool:
    return (0x41 <= b <= 0x5A) or (0x61 <= b <= 0x7A) or (0x30 <= b <= 0x39) or b == 0x5F


# Free-behind tuning for the linear offset scan: every ``_SCAN_FREE_STEP`` bytes,
# ``MADV_DONTNEED`` the pages already passed (keeping a ``_SCAN_FREE_MARGIN`` look-behind
# so the statement currently being read is never dropped). Bounds the scan's file
# residency to ~these few MB instead of the whole (700 MB+) file — the last big chunk of
# the "parent at ~2.1 GB before tessellation" peak. Mirrors sin_reader's scan pattern.
_SCAN_FREE_STEP = 32 << 20
_SCAN_FREE_MARGIN = 1 << 20


def _scan_offset_index(mm):
    """One linear pass: record (id -> byte offset) for every ``#id=…`` entity plus the
    ids of the geometry roots. Uses array.array (raw int64 — no per-int Python object
    blow-up), so the index of an 11 M-entity file is ~170 MB, not gigabytes.

    This pass only *records* offsets — it never resolves a reference (entities may point
    in any direction; that is handled later by random-access ``os.pread`` over the full
    index). Because the byte walk itself is strictly left-to-right, pages already scanned
    are dropped from RSS via ``MADV_DONTNEED`` as we go, so the whole file never stays
    resident."""
    import array
    import mmap as _mmap

    page = _mmap.PAGESIZE
    freed = 0

    def _free_behind(upto: int) -> None:
        nonlocal freed
        target = ((upto - _SCAN_FREE_MARGIN) // page) * page
        if target > freed:
            try:
                mm.madvise(_mmap.MADV_DONTNEED, freed, target - freed)
            except (AttributeError, OSError, ValueError):
                pass
            freed = target

    ids = array.array("q")
    offs = array.array("q")
    roots: list[int] = []
    styled: list[int] = []  # STYLED_ITEM ids — resolved to per-solid colours later
    cdsr: list[int] = []  # CONTEXT_DEPENDENT_SHAPE_REPRESENTATION ids — assembly transforms
    srr: list[int] = []  # standalone SHAPE_REPRESENTATION_RELATIONSHIP ids
    absr: list[int] = []  # ADVANCED_BREP_SHAPE_REPRESENTATION ids (solid -> geom rep)
    sdr: list[int] = []  # SHAPE_DEFINITION_REPRESENTATION ids (rep -> product, for tree names)
    n = len(mm)
    pos = 0
    while pos < n:
        end = _stmt_end(mm, pos, n)
        if end >= n:
            break
        s = pos
        while s < end and mm[s] in _WS:
            s += 1
        if s < end and mm[s] == 0x23:  # '#'
            k = s + 1
            while k < end and 0x30 <= mm[k] <= 0x39:
                k += 1
            if k > s + 1:
                rid = int(mm[s + 1 : k])
                ids.append(rid)
                offs.append(s)
                # locate the type keyword for root/styled detection: skip ws, '=', ws
                # (OCC writes "#33 = MANIFOLD_SOLID_BREP(...)" with spaces around '=').
                eq = k
                while eq < end and mm[eq] in _WS:
                    eq += 1
                if eq < end and mm[eq] == 0x3D:  # '='
                    m = eq + 1
                    while m < end and mm[m] in _WS:
                        m += 1
                    p = m
                    while p < end and _is_kw_byte(mm[p]):
                        p += 1
                    if p > m:
                        kw = mm[m:p].decode("ascii", "replace")
                        if kw in _ROOT_BUILDERS:
                            roots.append(rid)
                        elif kw == "STYLED_ITEM":
                            styled.append(rid)
                        elif kw == "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION":
                            cdsr.append(rid)
                        elif kw == "SHAPE_REPRESENTATION_RELATIONSHIP":
                            srr.append(rid)
                        elif kw == "ADVANCED_BREP_SHAPE_REPRESENTATION":
                            absr.append(rid)
                        elif kw == "SHAPE_DEFINITION_REPRESENTATION":
                            sdr.append(rid)
        pos = end + 1
        if pos - freed >= _SCAN_FREE_STEP:
            _free_behind(pos)
    return ids, offs, roots, styled, cdsr, srr, absr, sdr


class _OffsetPool:
    """Drop-in for the entity dict: ``get(id)`` looks up the entity's byte offset (binary
    search over the spilled, sorted id array) and parses the statement on demand. The
    _Resolver caches the BUILT object per solid, so each entity is parsed about once.

    Two backends: ``fd`` mode reads each statement with ``os.pread`` so the 700 MB+ file
    never enters the process address space (pages live in the reclaimable OS page cache,
    off VmRSS — the default for the streaming reader); ``mm`` mode slices an open mmap
    (kept for callers that already hold one)."""

    def __init__(self, ids_sorted, offs_sorted, *, fd=None, file_size=0, mm=None, owns_fd=False):
        self._ids = ids_sorted
        self._offs = offs_sorted
        self._fd = fd
        self._owns_fd = owns_fd
        self._file_size = file_size
        self._mm = mm
        self._n = len(mm) if mm is not None else file_size

    def close(self) -> None:
        """Close the pread fd when this pool opened it (``owns_fd``). The memmapped index
        arrays drop with the pool; an mmap passed in by the caller is the caller's to close."""
        import os

        if self._owns_fd and self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = None

    def get(self, rid):
        import numpy as np

        i = int(np.searchsorted(self._ids, rid))
        if i >= self._ids.shape[0] or self._ids[i] != rid:
            return None
        off = int(self._offs[i])
        if self._fd is not None:
            stmt = _read_statement_pread(self._fd, off, self._file_size)
        else:
            stmt = _read_statement_at(self._mm, off, self._n)
        parsed = _parse_statement(stmt)
        if parsed is None:
            return None
        return _Rec(parsed[1], parsed[2])


class StreamIndex:
    """The one-time, **picklable** result of indexing a large STEP file: the spilled
    id→offset index (two memmappable tempfiles) + the per-solid colour / world-transform /
    product-name maps + the ordered list of root ids.

    ``open_pool()`` (re)binds an :class:`_OffsetPool` in ANY process — the parent for the
    serial generator, or each worker for the parallel build — by reopening the STEP file
    and memmapping the index tempfiles (shared OS page cache, so N workers don't multiply
    RSS). ``build_one_solid(idx, pool, resolver, rid, seq)`` is then stateless across
    solids, so workers can build solids by id in any order with no per-solid cross-process
    geometry transfer (only a root id crosses the boundary).

    Pickling carries the maps (one-time, ~tens of MB) to a spawn worker; only the creating
    process OWNS the tempfiles — an unpickled copy never unlinks them (``_owns=False``)."""

    __slots__ = (
        "step_path",
        "idx_ids_path",
        "idx_offs_path",
        "file_size",
        "roots",
        "colour_map",
        "tmap",
        "prod_names",
        "tolerant",
        "_owns",
    )

    def __init__(self, step_path, idx_ids_path, idx_offs_path, file_size, roots, colour_map, tmap, prod_names, tolerant):
        self.step_path = str(step_path)
        self.idx_ids_path = idx_ids_path
        self.idx_offs_path = idx_offs_path
        self.file_size = file_size
        self.roots = roots
        self.colour_map = colour_map
        self.tmap = tmap
        self.prod_names = prod_names
        self.tolerant = tolerant
        self._owns = True  # the creating process unlinks the tempfiles; pickled copies don't

    def __getstate__(self):
        return {k: getattr(self, k) for k in self.__slots__ if k != "_owns"}

    def __setstate__(self, state):
        for k, v in state.items():
            setattr(self, k, v)
        self._owns = False  # a worker's copy must NOT unlink the parent's tempfiles

    def open_pool(self):
        """Reopen the STEP fd (pread) + memmap the spilled index → ``(pool, resolver)``.
        Call once per process; close the returned pool when done (``pool.close()``)."""
        import os

        import numpy as np

        fd = os.open(self.step_path, os.O_RDONLY)
        if self.idx_ids_path is not None:
            ids_mm = np.memmap(self.idx_ids_path, dtype=np.int64, mode="r")
            offs_mm = np.memmap(self.idx_offs_path, dtype=np.int64, mode="r")
        else:  # empty file
            ids_mm = np.empty(0, dtype=np.int64)
            offs_mm = np.empty(0, dtype=np.int64)
        pool = _OffsetPool(ids_mm, offs_mm, fd=fd, file_size=self.file_size, owns_fd=True)
        return pool, _Resolver(pool)

    def close(self):
        """Unlink the spilled index tempfiles (creating process only)."""
        import os

        if not self._owns:
            return
        for p in (self.idx_ids_path, self.idx_offs_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        self.idx_ids_path = self.idx_offs_path = None


def prepare_stream_index(filepath, *, tolerant: bool, on_total=None) -> StreamIndex:
    """Do the one-time, serial setup for a large STEP file and return a picklable
    :class:`StreamIndex`: scan the offset index, spill it to disk, and build the
    colour / world-transform / product-name maps. This is the only work the conversion
    PARENT must do serially; the per-solid parse+build is then parallelised by handing the
    StreamIndex to workers (each calls :meth:`StreamIndex.open_pool` + ``build_one_solid``).

    The scan keeps the file off VmRSS (munmap right after the linear pass + free-behind);
    entity reads go through ``os.pread``. The index tempfiles are NOT unlinked here —
    ownership transfers to ``StreamIndex.close()`` after every consumer (parent + workers)
    has finished, so a spawned worker can still memmap them."""
    import mmap
    import os
    import tempfile

    import numpy as np

    filepath = Path(filepath)
    fh = open(filepath, "rb")  # noqa: SIM115 - closed in finally once the maps are built
    fd = fh.fileno()
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
    file_size = mm.size()
    # The scan is a single forward pass: hint the kernel to read ahead + drop pages behind.
    try:
        mm.madvise(mmap.MADV_SEQUENTIAL)
    except (AttributeError, OSError):
        pass
    p_i = p_o = None
    try:
        ids_arr, offs_arr, roots, styled, cdsr, srr, absr, sdr = _scan_offset_index(mm)
        _mem_probe("after scan (mmap live)", step_path=filepath)
        # The scan is the only mmap consumer; drop the file mapping immediately (~700 MB+
        # on a large assembly) so argsort/spill + every later read run off VmRSS via pread.
        mm.close()
        mm = None
        _mem_probe("after scan munmap", step_path=filepath)
        ids_np = np.frombuffer(ids_arr, dtype=np.int64)
        order = np.argsort(ids_np, kind="stable")
        if ids_np.size:
            fd_i, p_i = tempfile.mkstemp(suffix=".ada_idx_ids")
            fd_o, p_o = tempfile.mkstemp(suffix=".ada_idx_offs")
            os.close(fd_i)
            os.close(fd_o)
            # Reorder + spill one array at a time, freeing each before the next (~2x, not
            # ~5x, the index transient).
            ids_np[order].tofile(p_i)
            del ids_np, ids_arr
            offs_np = np.frombuffer(offs_arr, dtype=np.int64)
            offs_np[order].tofile(p_o)
            del offs_np, offs_arr, order
            ids_mm = np.memmap(p_i, dtype=np.int64, mode="r")
            offs_mm = np.memmap(p_o, dtype=np.int64, mode="r")
        else:  # empty file
            del ids_np, ids_arr, offs_arr, order
            ids_mm = np.empty(0, dtype=np.int64)
            offs_mm = np.empty(0, dtype=np.int64)
        idx_paths = [p for p in (p_i, p_o) if p]
        pool = _OffsetPool(ids_mm, offs_mm, fd=fd, file_size=file_size)
        _mem_probe("after index spill+pread pool", step_path=filepath, idx_paths=idx_paths)
        colour_map = _build_colour_map(pool.get, styled)
        _mem_probe("after colour_map", step_path=filepath, idx_paths=idx_paths, sized={"colour_map": colour_map})
        tmap, prod_names = _build_transform_map(
            pool.get, roots, cdsr, srr, absr, sdr, global_scale=detect_step_length_unit_scale(filepath)
        )
        _mem_probe(
            "after transform_map",
            step_path=filepath,
            idx_paths=idx_paths,
            sized={"tmap": tmap, "colour_map": colour_map},
        )
        if on_total is not None:
            on_total(len(roots))
        # Drop the prepare-local memmaps + pool; consumers rebind fresh ones via open_pool().
        del ids_mm, offs_mm, pool
    finally:
        if mm is not None:  # early error before the post-scan munmap
            mm.close()
        fh.close()  # the prepare fd; consumers reopen the file in open_pool()
    return StreamIndex(filepath, p_i, p_o, file_size, roots, colour_map, tmap, prod_names, tolerant)


def build_one_solid(idx: StreamIndex, pool, resolver, rid: int, seq: int, *, skipped):
    """Build the single ``ada.geom`` :class:`Geometry` for root ``rid`` (position ``seq`` in
    ``idx.roots``), carrying its colour + per-instance world transforms — exactly what the
    serial reader yields for that solid. Returns the Geometry or None (unresolved / dropped).

    Stateless across solids (the only per-solid state is ``resolver``'s cache, reset here),
    so a worker can call it for any ``rid`` in any order. ``seq`` is the deterministic
    ordinal used only for the generic ``solid_N`` fallback name (named solids ignore it)."""
    rec = pool.get(rid)
    if rec is None:
        return None
    name = _solid_name(rec.args, seq, idx.prod_names.get(rid))
    resolver.reset_cache()
    geom = _try_resolve_root(resolver, name, _ROOT_BUILDERS[rec.type], rec.args, tolerant=idx.tolerant, skipped=skipped)
    if geom is None:
        return None
    color = _as_color(idx.colour_map.get(rid))
    # _yield_instances yields exactly one Geometry per solid (its instance transforms attached).
    return next(iter(_yield_instances(name, geom, color, idx.tmap.get(rid))), None)


def root_face_count(pool, rid: int) -> int:
    """Cheap complexity proxy for a root solid: the number of faces in its shell(s), read
    straight from the shell entity — ~1-2 ``pool.get`` (preads), NO full geometry build.

    A solid's tessellation cost grows with its face count (and B-spline grid sizes, not
    counted here — face count alone is a good first-order proxy for the many-faced dense
    parts that dominate the long tail). Used by the optional LPT scheduler to dispatch the
    heaviest solids first. Handles the common roots — MANIFOLD_SOLID_BREP / BREP_WITH_VOIDS
    / FACETED_BREP (a single CLOSED_SHELL), SHELL_BASED_SURFACE_MODEL (a list of shells) —
    by summing the faces of every shell referenced (directly or in a list) from the root.
    Returns 1 on anything it can't read (a neutral weight)."""
    rec = pool.get(rid)
    if rec is None:
        return 1

    def _shell_faces(ref_id) -> int:
        s = pool.get(ref_id)
        if s is None:
            return 0
        if s.type in ("CLOSED_SHELL", "OPEN_SHELL") and len(s.args) >= 2 and isinstance(s.args[1], (list, tuple)):
            return len(s.args[1])
        return 0

    total = 0
    for a in rec.args:
        if isinstance(a, _Ref):
            total += _shell_faces(a.id)
        elif isinstance(a, (list, tuple)):
            for it in a:
                if isinstance(it, _Ref):
                    total += _shell_faces(it.id)
    return max(total, 1)


def _read_two_pass_lazy(filepath: Path, *, tolerant: bool, skipped, on_total=None):
    """Serial streaming read (import-to-Assembly path + the reference oracle): index once,
    then build + yield one Geometry per root. The parallel GLB path reuses the SAME
    ``prepare_stream_index`` / ``build_one_solid`` pieces across worker processes."""
    idx = prepare_stream_index(filepath, tolerant=tolerant, on_total=on_total)
    pool, resolver = idx.open_pool()
    idx_paths = [p for p in (idx.idx_ids_path, idx.idx_offs_path) if p]
    try:
        for seq, rid in enumerate(idx.roots):
            if seq == 0 or (seq + 1) % 1000 == 0:
                _mem_probe(f"streaming solid #{seq + 1}", step_path=idx.step_path, idx_paths=idx_paths)
            geom = build_one_solid(idx, pool, resolver, rid, seq, skipped=skipped)
            if geom is not None:
                yield geom
    finally:
        pool.close()
        idx.close()

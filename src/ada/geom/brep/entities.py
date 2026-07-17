"""Shared B-rep connectivity entities.

Unlike the value-object edge/loop/face types in :mod:`ada.geom.curves` and
:mod:`ada.geom.surfaces` (which describe *one* face's boundary and carry no
sharing), these entities have **identity**: a single :class:`BEdge` is referenced
by the two :class:`BCoEdge` uses of the two faces that meet along it, and a single
:class:`BVertex` is shared by every edge that ends on it. That shared graph is the
thing adapy has lacked — it is what a SAT/STEP body encodes via record references
and what a position-weld only approximates.

Geometry (the actual curve/surface shape) stays in ngeom; these types only add the
*connectivity* on top, referencing ngeom primitives as their payload. Pure
dataclasses — no CAD-kernel dependency.

Equality/hashing is by object identity (``eq=False``): two vertices at the same
position are the *same* vertex only if they are the same object, which the owning
:class:`~ada.geom.brep.store.BRepStore` guarantees on build. ``source_id`` records
the id of the record a producer built the entity from (a SAT ``$``-ref, a STEP
``#``-id) for provenance and to prove identity was preserved. ``link`` is an opaque
seam for a later higher layer (e.g. an :mod:`ada.topology` cell) to point back in;
it is unused in v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ada.geom.curves import CURVE_GEOM_TYPES
    from ada.geom.points import Point
    from ada.geom.surfaces import SURFACE_GEOM_TYPES


class LoopKind(Enum):
    OUTER = "outer"
    INNER = "inner"  # a hole loop


@dataclass(slots=True, eq=False)
class BVertex:
    id: int
    point: Point
    source_id: str | None = None
    link: Any = None


@dataclass(slots=True, eq=False)
class BEdge:
    """A shared edge: a curve trimmed between two vertices.

    ``t_start``/``t_end`` are the curve's parameter range at ``start``/``end``.
    They are carried explicitly because a closed or self-intersecting curve (a
    circle, a looping spline) has more than one arc between the same two points,
    so 3D endpoints alone do not identify which one this edge is — the same
    reason SAT records them on every edge.
    """

    id: int
    curve: CURVE_GEOM_TYPES
    start: BVertex
    end: BVertex
    t_start: float | None = None
    t_end: float | None = None
    # The SAT string-attribute name (e.g. "EDGE00001234"), when the source named
    # this edge — a Genie beam's ``<sat_reference>`` resolves to it, so it must
    # survive the roundtrip.
    name: str | None = None
    source_id: str | None = None
    link: Any = None


@dataclass(slots=True, eq=False)
class BCoEdge:
    """A directed *use* of an edge by one face's loop.

    ``sense`` is True when the loop traverses the edge from ``edge.start`` to
    ``edge.end``, False for the reverse. The set of coedges sharing one
    :class:`BEdge` is its partner ring (see :meth:`BRepStore.coedges_on`).
    """

    id: int
    edge: BEdge
    sense: bool
    loop: BLoop | None = None
    # The per-coedge UV curve on the face's surface (an ngeom ``Pcurve2dBSpline``),
    # with its authored ``same_sense``. A spline face is unusable to ACIS without
    # it; a planar face carries none.
    pcurve: Any = None
    source_id: str | None = None
    link: Any = None


@dataclass(slots=True, eq=False)
class BLoop:
    id: int
    kind: LoopKind
    coedges: list[BCoEdge] = field(default_factory=list)
    face: BFace | None = None
    source_id: str | None = None
    link: Any = None


@dataclass(slots=True, eq=False)
class BFace:
    """A face: a surface bounded by one outer loop and zero or more inner (hole)
    loops. ``sense`` is the face normal's orientation relative to the surface
    (ACIS/STEP split the normal across face and surface; this carries the face's
    half)."""

    id: int
    surface: SURFACE_GEOM_TYPES
    sense: bool
    outer: BLoop | None = None
    inner: list[BLoop] = field(default_factory=list)
    shell: BShell | None = None
    # The SAT string-attribute name (e.g. "FACE00001480"); a Genie plate's
    # ``face_ref`` resolves to it, so it must survive the roundtrip.
    name: str | None = None
    source_id: str | None = None
    link: Any = None

    @property
    def loops(self) -> list[BLoop]:
        return ([self.outer] if self.outer is not None else []) + self.inner


@dataclass(slots=True, eq=False)
class BShell:
    id: int
    faces: list[BFace] = field(default_factory=list)
    lump: BLump | None = None
    source_id: str | None = None
    link: Any = None


@dataclass(slots=True, eq=False)
class BLump:
    id: int
    shells: list[BShell] = field(default_factory=list)
    source_id: str | None = None
    link: Any = None


@dataclass(slots=True, eq=False)
class BWire:
    """A wire: a set of edges not bounding a face (construction geometry, guide
    axes). Carried so the store is a *complete* mirror of the source body — a
    dropped wire is dropped topology, however peripheral."""

    id: int
    coedges: list[BCoEdge] = field(default_factory=list)
    shell: BShell | None = None
    source_id: str | None = None
    link: Any = None

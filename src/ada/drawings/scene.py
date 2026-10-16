"""Collect adapy objects into drawable items: triangle meshes for solids, polylines for lines.

Solids are tessellated through the active CAD backend (``ada.cad.active_backend()``); spheres
get an analytic outline so their silhouette is a true circle at any view angle. Beams are
drawn as solids, as centre lines, or both. :class:`Polyline` carries lines that are not
adapy objects of their own: cables, wires, guys, construction lines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

import numpy as np

from ada.config import logger

BeamStyle = Literal["solid", "centerline", "both"]
ItemKind = Literal["solid", "line"]


@dataclass
class Polyline:
    """A line (cable, wire, guy, construction line) drawn as-is, in model coordinates.

    ``style`` picks the line class: ``"wire"`` (continuous, occlusion tested) or
    ``"centerline"`` (dash-dot, always drawn).
    """

    points: Sequence[Sequence[float]]
    name: str = "polyline"
    style: Literal["wire", "centerline"] = "wire"


@dataclass
class DrawingItem:
    """One drawable thing. ``vertices``/``faces`` for a solid, ``lines`` for line items."""

    name: str
    group: str
    kind: ItemKind
    vertices: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    faces: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.int64))
    lines: list[np.ndarray] = field(default_factory=list)
    line_style: Literal["wire", "centerline"] = "wire"
    handle: Any = None  # backend shape handle (kernel backends use it for exact sections)
    sphere: tuple[np.ndarray, float] | None = None  # analytic outline (centre, radius)
    source: Any = None

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        pts = [self.vertices] if len(self.vertices) else []
        pts += [ln for ln in self.lines if len(ln)]
        if not pts:
            return None
        a = np.vstack(pts)
        return a.min(axis=0), a.max(axis=0)


def _default_group(obj) -> str:
    from ada import Beam, Pipe, Plate

    if isinstance(obj, Beam):
        return "beam"
    if isinstance(obj, Plate):
        return "plate"
    if isinstance(obj, Pipe):
        return "pipe"
    if isinstance(obj, Polyline):
        return "wire"
    return "shape"


def _iter_objects(objects) -> Iterable:
    from ada import Assembly, Part

    if isinstance(objects, (Part, Assembly)):
        yield from objects.get_all_physical_objects()
        return
    if isinstance(objects, (list, tuple, set)):
        for o in objects:
            yield from _iter_objects(o)
        return
    yield objects


def sphere_mesh(center, radius: float, deflection: float) -> tuple[np.ndarray, np.ndarray]:
    """UV sphere whose chords deviate at most ``deflection`` from the surface."""
    c = max(min(1.0 - deflection / max(radius, 1e-12), 1.0), -1.0)
    step = 2.0 * math.acos(c) if c < 1.0 else math.pi / 16
    n_lon = int(np.clip(math.ceil(2 * math.pi / max(step, 1e-6) / 8.0) * 8, 32, 512))
    n_lat = n_lon // 2
    th = np.linspace(0.0, math.pi, n_lat + 1)[1:-1]
    ph = np.linspace(0.0, 2 * math.pi, n_lon, endpoint=False)
    tt, pp = np.meshgrid(th, ph, indexing="ij")
    ring = np.stack([np.sin(tt) * np.cos(pp), np.sin(tt) * np.sin(pp), np.cos(tt)], axis=-1).reshape(-1, 3)
    v = np.vstack([[0, 0, 1], ring, [0, 0, -1]]) * radius + np.asarray(center, float)
    faces = []
    top, bot = 0, len(v) - 1

    def vid(i, j):
        return 1 + i * n_lon + (j % n_lon)

    for j in range(n_lon):
        faces.append((top, vid(0, j), vid(0, j + 1)))
        faces.append((bot, vid(n_lat - 2, j + 1), vid(n_lat - 2, j)))
    for i in range(n_lat - 2):
        for j in range(n_lon):
            a, b, c_, d = vid(i, j), vid(i, j + 1), vid(i + 1, j + 1), vid(i + 1, j)
            faces.append((a, d, c_))
            faces.append((a, c_, b))
    return v, np.asarray(faces, np.int64)


def _world(obj, pts: np.ndarray) -> np.ndarray:
    """Local -> world for objects placed inside a part (``BackendGeom.world_matrix``)."""
    fn = getattr(obj, "world_matrix", None)
    m = fn() if fn is not None else None
    if m is None:
        return pts
    m = np.asarray(m, float)
    return pts @ m[:3, :3].T + m[:3, 3]


def _tessellate(obj, deflection: float) -> tuple[np.ndarray, np.ndarray, Any]:
    from ada.cad import active_backend

    be = active_backend()
    handle = be.build(obj.solid_geom())
    fn = getattr(obj, "world_matrix", None)
    m = fn() if fn is not None else None
    if m is not None:
        handle = be.transform(handle, np.asarray(m, float), copy=True)
    mesh = be.tessellate(handle, deflection)
    v = np.asarray(mesh.positions, float).reshape(-1, 3)
    f = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
    return v, f, handle


def collect(
    objects,
    group: str | None,
    beam_style: BeamStyle,
    deflection: float,
    skipped: list[str],
) -> list[DrawingItem]:
    """Turn adapy objects (a Part/Assembly, an object, or a list of them) into drawing items."""
    from ada import Beam, PrimSphere

    items: list[DrawingItem] = []
    for obj in _iter_objects(objects):
        grp = group or _default_group(obj)
        name = getattr(obj, "name", type(obj).__name__)
        if isinstance(obj, Polyline):
            pts = np.asarray(obj.points, float).reshape(-1, 3)
            items.append(DrawingItem(name, grp, "line", lines=[pts], line_style=obj.style, source=obj))
            continue
        if isinstance(obj, Beam) and beam_style in ("centerline", "both"):
            line = _world(obj, np.asarray([obj.n1.p, obj.n2.p], float))
            items.append(DrawingItem(name, grp, "line", lines=[line], line_style="centerline", source=obj))
            if beam_style == "centerline":
                continue
        if isinstance(obj, PrimSphere):
            c = _world(obj, np.asarray(obj.cog, float).reshape(1, 3))[0]
            r = float(obj.radius)
            v, f = sphere_mesh(c, r, deflection)
            items.append(DrawingItem(name, grp, "solid", vertices=v, faces=f, sphere=(c, r), source=obj))
            continue
        try:
            v, f, handle = _tessellate(obj, deflection)
        except Exception as exc:  # noqa: BLE001 -- report and keep drawing the rest
            segments = getattr(obj, "segments", None)
            if segments:
                items += collect(list(segments), grp, beam_style, deflection, skipped)
                continue
            logger.warning(f"drawing: could not tessellate {name!r} ({type(obj).__name__}): {exc}")
            skipped.append(name)
            continue
        if len(f) == 0:
            skipped.append(name)
            continue
        items.append(DrawingItem(name, grp, "solid", vertices=v, faces=f, handle=handle, source=obj))
    return items

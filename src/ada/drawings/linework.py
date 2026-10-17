"""Linework backends: turn drawing items + a view into 2D strokes (cut, projection, hidden, ...).

A backend implements :class:`LineworkBackend`. Two ship with adapy:

``"mesh"`` (:class:`MeshLinework`)
    Works on triangle meshes (the active CAD backend's tessellation). Cut lines come from slicing
    the mesh with the section plane; projection lines are the mesh's crease, boundary and
    silhouette edges; visibility is decided per sample along each edge by casting it against the
    triangles in front and the section caps (hidden-line removal). Kernel independent.

``"kernel"`` (:class:`KernelSectionLinework`)
    The mesh backend, but cut lines come from the CAD kernel's exact plane section
    (``CadBackend.section_with_plane``), falling back to mesh slicing where the kernel gives none.

Other kernels plug in with :func:`register_linework_backend`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

import numpy as np

from ada.drawings.mesh_ops import (
    Occluder,
    chain_segments,
    clip_mesh,
    edge_topology,
    face_normals,
    slice_mesh,
    weld,
)
from ada.drawings.scene import DrawingItem
from ada.drawings.view import SectionPlane, View

StrokeKind = Literal["cut", "projection", "hidden", "centerline", "wire"]


@dataclass
class Stroke:
    """A polyline in screen coordinates (model units, y up)."""

    points: np.ndarray
    kind: StrokeKind
    group: str


@dataclass
class CutRegion:
    """The cut face of one item: closed loops in screen coordinates (filled even-odd)."""

    loops: list[np.ndarray]
    group: str
    name: str


@dataclass
class Linework:
    strokes: list[Stroke] = field(default_factory=list)
    regions: list[CutRegion] = field(default_factory=list)

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        pts = [s.points for s in self.strokes if len(s.points)]
        pts += [lp for r in self.regions for lp in r.loops if len(lp)]
        if not pts:
            return None
        a = np.vstack(pts)
        return a.min(axis=0), a.max(axis=0)

    def by_kind(self, kind: StrokeKind) -> list[Stroke]:
        return [s for s in self.strokes if s.kind == kind]


@dataclass
class LineworkOptions:
    """Tolerances in model units. ``sample_spacing`` is the step of the visibility test along
    an edge; ``crease_angle`` (degrees) is the dihedral angle above which a mesh edge is drawn."""

    tolerance: float = 1e-4
    sample_spacing: float = 0.01
    crease_angle: float = 30.0
    show_hidden: bool = False
    hidden_wires: bool = True


class LineworkBackend(Protocol):
    name: str

    def compute(
        self, items: list[DrawingItem], view: View, section: SectionPlane | None, opts: LineworkOptions
    ) -> Linework: ...


def _plane_to_screen(plane: SectionPlane, view: View) -> tuple[np.ndarray, np.ndarray]:
    r, u, d = view.basis()
    n = plane.unit_normal()
    o = view.to_screen(np.asarray(plane.origin, float))[0]
    return o, np.array([n @ r, n @ u, n @ d])


class MeshLinework:
    """Hidden-line removal and plane sections on triangle meshes."""

    name = "mesh"

    # -- hooks ---------------------------------------------------------------------------------
    def section_loops(self, item: DrawingItem, v: np.ndarray, f: np.ndarray, plane: SectionPlane) -> list[np.ndarray]:
        """Cut loops (model coordinates) of one item."""
        return slice_mesh(v, f, plane.origin, plane.unit_normal())

    # -- main ----------------------------------------------------------------------------------
    def compute(
        self, items: list[DrawingItem], view: View, section: SectionPlane | None, opts: LineworkOptions
    ) -> Linework:
        tol = opts.tolerance
        planes = view.clip_planes() + ([section] if section is not None else [])
        planes_s = [_plane_to_screen(p, view) for p in planes]
        cos_crease = np.cos(np.radians(opts.crease_angle))

        lw = Linework()
        occ_tris: list[np.ndarray] = []
        caps: list[list[np.ndarray]] = []
        cand: list[tuple[np.ndarray, np.ndarray, str, StrokeKind, bool]] = []  # a, b, group, kind, test

        bnds = [it.bounds() for it in items]
        bnds = [b for b in bnds if b is not None]
        size = float(np.linalg.norm(np.max([b[1] for b in bnds], 0) - np.min([b[0] for b in bnds], 0))) if bnds else 1
        size = max(size, tol)

        for it in items:
            if it.kind == "line":
                kind: StrokeKind = "centerline" if it.line_style == "centerline" else "wire"
                for ln in it.lines:
                    s = view.to_screen(ln)
                    if len(s) >= 2:
                        cand.append((s[:-1], s[1:], it.group, kind, kind == "wire"))
                continue
            v, f = weld(it.vertices, it.faces, tol)
            if len(f) == 0:
                continue
            if section is not None:
                loops3 = self.section_loops(it, v, f, section)
                loops2 = [view.to_screen(lp)[:, :2] for lp in loops3 if len(lp) >= 2]
                if loops2:
                    closed = [lp for lp in loops2 if len(lp) >= 4 and np.linalg.norm(lp[0] - lp[-1]) <= 10 * tol]
                    lw.regions.append(CutRegion(closed, it.group, it.name))
                    lw.strokes += [Stroke(lp, "cut", it.group) for lp in loops2]
                    if closed:
                        caps.append(closed)
            flags = None
            for p in planes:
                v, f, flags = clip_mesh(v, f, p.origin, p.unit_normal(), tol * 0.5, flags)
            if len(f) == 0:
                continue
            vs = view.to_screen(v)
            occ_tris.append(vs[f])
            topo = edge_topology(f)
            nrm = face_normals(vs, f)
            nz = nrm[:, 2]
            fa, fb = topo.face_a, topo.face_b
            two = topo.count == 2
            fbb = np.where(fb >= 0, fb, fa)
            crease = two & (np.einsum("ij,ij->i", nrm[fa], nrm[fbb]) < cos_crease)
            eps_n = 1e-9
            front_a, front_b = nz[fa] < -eps_n, nz[fbb] < -eps_n
            silhouette = two & (front_a != front_b)
            if it.sphere is not None:
                silhouette[:] = False  # drawn as an analytic circle below
            keep = (topo.count != 2) | crease | silhouette
            if flags is not None:
                keep &= ~(flags[topo.edges[:, 0]] & flags[topo.edges[:, 1]])  # the clip's own rim
            e = topo.edges[keep]
            if len(e):
                cand.append((vs[e[:, 0]], vs[e[:, 1]], it.group, "projection", True))
            if it.sphere is not None:
                c, r = it.sphere
                cs = view.to_screen(c)[0]
                n = max(64, int(2 * np.pi * r / max(opts.sample_spacing, 1e-9) / 4))
                t = np.linspace(0.0, 2 * np.pi, n + 1)
                circ = np.stack([cs[0] + r * np.cos(t), cs[1] + r * np.sin(t), np.full_like(t, cs[2])], axis=1)
                cand.append((circ[:-1], circ[1:], it.group, "projection", True))

        tris = np.concatenate(occ_tris) if occ_tris else np.zeros((0, 3, 3))
        occluder = Occluder(tris, depth_tol=max(1e-6 * size, 0.5 * tol), eps=1e-7 * size, caps=caps)
        segs: dict[tuple[str, StrokeKind], list] = {}
        for a, b, group, kind, test in cand:
            for p0, p1, state in _classify(a, b, occluder, planes_s, opts.sample_spacing, test, tol):
                if state == 2:
                    continue
                k: StrokeKind = kind
                if state == 1:
                    if not opts.show_hidden or (kind == "wire" and not opts.hidden_wires):
                        continue
                    k = "hidden"
                segs.setdefault((group, k), []).append((p0, p1))
        for (group, kind), ss in segs.items():
            for pl in chain_segments(ss, tol):
                lw.strokes.append(Stroke(pl, kind, group))
        return lw


def _classify(a, b, occluder: Occluder, planes_s, spacing: float, test: bool, tol: float):
    """Split segments a[i]->b[i] (screen, (E, 3)) into runs: 0 visible, 1 hidden, 2 clipped away."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ln = np.linalg.norm((b - a)[:, :2], axis=1)
    n = np.maximum(1, np.ceil(ln / max(spacing, 1e-12)).astype(int))
    rep = np.repeat(np.arange(len(a)), n)
    start = np.concatenate([[0], np.cumsum(n)[:-1]])
    k = np.arange(len(rep)) - np.repeat(start, n)
    t_mid = (k + 0.5) / n[rep]
    pts = a[rep] + t_mid[:, None] * (b - a)[rep]
    state = np.zeros(len(pts), np.int8)
    for o, nn in planes_s:
        state[(pts - o) @ nn < -tol] = 2
    if test:
        live = state == 0
        if live.any():
            hid = occluder.hidden(pts[live])
            idx = np.nonzero(live)[0]
            state[idx[hid]] = 1
    out = []
    for i in range(len(a)):
        s = state[start[i] : start[i] + n[i]]
        p0, p1 = a[i, :2], b[i, :2]
        if s.min() == s.max():
            out.append((p0, p1, int(s[0])))
            continue
        cuts = np.nonzero(np.diff(s))[0] + 1
        bounds = np.concatenate([[0], cuts, [len(s)]])
        for j in range(len(bounds) - 1):
            t0, t1 = bounds[j] / n[i], bounds[j + 1] / n[i]
            out.append((p0 + t0 * (p1 - p0), p0 + t1 * (p1 - p0), int(s[bounds[j]])))
    return out


class KernelSectionLinework(MeshLinework):
    """Mesh linework with the CAD kernel's exact plane section for the cut lines."""

    name = "kernel"

    def section_loops(self, item: DrawingItem, v: np.ndarray, f: np.ndarray, plane: SectionPlane) -> list[np.ndarray]:
        if item.handle is None:
            return super().section_loops(item, v, f, plane)
        from ada.cad import active_backend

        be = active_backend()
        try:
            size = 4.0 * float(np.linalg.norm(v.max(axis=0) - v.min(axis=0))) + 1.0
            sec = be.section_with_plane(item.handle, plane.origin, tuple(plane.unit_normal()), size)
            mesh = be.tessellate(sec)
            sv = np.asarray(mesh.positions, float).reshape(-1, 3)
            sf = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
        except Exception:  # noqa: BLE001 -- the kernel has no section here: slice the mesh
            return super().section_loops(item, v, f, plane)
        if len(sf) == 0:
            return super().section_loops(item, v, f, plane)
        # the kernel returns the triangulated cut face; its boundary edges are the cut lines
        tol = 1e-6 * max(float(np.linalg.norm(sv.max(axis=0) - sv.min(axis=0))), 1e-9)
        sv, sf = weld(sv, sf, tol)
        topo = edge_topology(sf)
        rim = topo.edges[topo.count == 1]
        segs = [(sv[i], sv[j]) for i, j in rim]
        return _chain3(segs, tol)


def _chain3(segs: list[tuple[np.ndarray, np.ndarray]], tol: float) -> list[np.ndarray]:
    """Chain 3D segments into polylines (shared end points within ``tol``)."""
    from ada.drawings.mesh_ops import _walk_any

    pts: dict = {}
    links: dict = {}
    for a, b in segs:
        ka, kb = tuple(np.round(a / tol).astype(np.int64)), tuple(np.round(b / tol).astype(np.int64))
        pts.setdefault(ka, a)
        pts.setdefault(kb, b)
        links.setdefault(ka, []).append(kb)
        links.setdefault(kb, []).append(ka)
    return [np.asarray([pts[k] for k in ch]) for ch in _walk_any(links)]


_BACKENDS: dict[str, Callable[[], LineworkBackend]] = {
    "mesh": MeshLinework,
    "kernel": KernelSectionLinework,
}


def register_linework_backend(name: str, factory: Callable[[], LineworkBackend]) -> None:
    """Make a linework backend selectable by name in :class:`ada.drawings.Drawing`."""
    _BACKENDS[name] = factory


def get_linework_backend(name: str) -> LineworkBackend:
    if name not in _BACKENDS:
        raise ValueError(f"unknown linework backend {name!r}; available: {sorted(_BACKENDS)}")
    return _BACKENDS[name]()


def available_linework_backends() -> list[str]:
    return sorted(_BACKENDS)

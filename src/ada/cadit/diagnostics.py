"""Backend-agnostic geometry diagnostics.

A single place to answer "did this object's geometry survive each stage, and if
not, how did it fail?" — across the three stages a shape passes through:

* **geom**       — ``obj.solid_geom()`` builds an ``ada.geom`` B-rep, and the
  active CAD backend turns it into a shape (``active_backend().build``). A
  failure here is a *definition* bug (degenerate profile, bad sweep, malformed
  imported surface).
* **tessellate** — ``active_backend().tessellate`` meshes the shape. A failure
  here is empty/degenerate/non-manifold output even when the B-rep "built".
* **parse**      — for imported files (STEP/IFC/SAT), how many source objects
  produced geometry at all (consumed from an importer's own stats, e.g.
  ``ada_stream_stats``).

Everything routes through ``active_backend()`` verbs (``build`` / ``tessellate``
/ ``shape_type``), so the same report describes OCC and adacpp — a divergence
between the two backends on the same object is itself a finding.

This is the in-tree counterpart to the step2glb coverage/degenerate report:
step2glb audits an external STEP file; this audits adapy's *own* geometry
pipeline so a bad profile or tessellation defect is caught at the source
(affecting FEM/beam/plate/IFC export alike), not only when it reaches a viewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada import Part
    from ada.base.physical_objects import BackendGeom

# A triangle whose doubled area (||e1 x e2||) is below this is treated as a
# sliver/degenerate. Scaled by the mesh bbox diagonal so it is unit-agnostic.
_DEGENERATE_AREA_FRAC = 1e-10
# A triangle smaller than this fraction of the median triangle area is a sliver
# (sub-resolution stitching), reported but not a failure.
_SLIVER_REL_FRAC = 1e-4


@dataclass
class StageResult:
    """Outcome of one geometry stage for one object."""

    stage: str  # "geom" | "tessellate"
    ok: bool
    error: str | None = None
    metrics: dict = field(default_factory=dict)


@dataclass
class GeometryDiagnostic:
    """Per-object geometry health across stages, for the active backend."""

    name: str
    obj_type: str
    backend: str
    stages: list[StageResult] = field(default_factory=list)

    def stage(self, name: str) -> StageResult | None:
        for s in self.stages:
            if s.stage == name:
                return s
        return None

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.stages)

    @property
    def first_failure(self) -> StageResult | None:
        for s in self.stages:
            if not s.ok:
                return s
        return None

    def summary(self) -> str:
        if self.ok:
            t = self.stage("tessellate")
            m = t.metrics if t else {}
            warn = f"  [warn: {'; '.join(m['warnings'])}]" if m.get("warnings") else ""
            return f"OK   {self.name} ({self.obj_type}) tris={m.get('n_tris', '?')}{warn}"
        f = self.first_failure
        return f"FAIL {self.name} ({self.obj_type}) @ {f.stage}: {f.error}"


def _mesh_buffers(mesh) -> tuple:
    """Extract (positions, indices) from whatever a backend's tessellate returns.

    The CAD-backend ``Mesh`` Protocol declares ``.indices``, but the OCC backend
    returns a ``TriangleMesh`` whose triangle buffer is ``.faces`` (and adacpp
    may return either). Accept both so the diagnostic is genuinely backend-neutral.
    """
    pos = getattr(mesh, "positions", None)
    idx = getattr(mesh, "indices", None)
    if idx is None:
        idx = getattr(mesh, "faces", None)
    if pos is None or idx is None:
        raise AttributeError(f"tessellate returned {type(mesh).__name__} without positions/(indices|faces)")
    return pos, idx


def mesh_health(positions, indices, *, shape_type: str | None = None) -> dict:
    """Compute mesh-validity metrics from flat position/index buffers.

    Pure numpy; no trimesh dependency. Flags the failure modes that matter for
    adapy's pipeline: empty output, NaN/Inf vertices, collapsed (repeated-index)
    triangles, zero-area slivers, non-manifold edges (a proxy for a torn or
    non-watertight solid), and a runaway bbox (trim/p-curve blow-ups).
    """
    pos = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    idx = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    n_verts = len(pos)
    n_tris = len(idx)
    health: dict = {"n_verts": n_verts, "n_tris": n_tris}
    if shape_type:
        health["shape_type"] = shape_type
    if n_tris == 0:
        health["empty"] = True
        return health
    health["empty"] = False

    finite = np.isfinite(pos).all()
    health["nonfinite_verts"] = int((~np.isfinite(pos).all(axis=1)).sum()) if not finite else 0

    # collapsed triangles: two or more identical vertex indices
    collapsed = (idx[:, 0] == idx[:, 1]) | (idx[:, 1] == idx[:, 2]) | (idx[:, 0] == idx[:, 2])
    health["collapsed_tris"] = int(collapsed.sum())

    tri = pos[idx]  # (n_tris, 3, 3)
    e1 = tri[:, 1] - tri[:, 0]
    e2 = tri[:, 2] - tri[:, 0]
    cross = np.cross(e1, e2)
    dbl_area = np.linalg.norm(cross, axis=1)  # = 2 * triangle area
    area = float(np.nansum(dbl_area) * 0.5)
    health["area"] = area

    bb_min = pos.min(axis=0)
    bb_max = pos.max(axis=0)
    diag = float(np.linalg.norm(bb_max - bb_min))
    health["bbox_diag"] = diag
    # area threshold scaled to the model so it is unit-agnostic
    area_eps = (diag * diag) * _DEGENERATE_AREA_FRAC if diag > 0 else 0.0
    degenerate = dbl_area <= max(area_eps, 0.0)
    health["degenerate_tris"] = int(degenerate.sum())
    health["degenerate_frac"] = round(float(degenerate.mean()), 4)
    # Slivers: triangles far smaller than the typical triangle. These are not
    # zero-area (so not "degenerate") but are sub-resolution stitching triangles
    # that tessellators (OCC and others alike) emit at hard seams; they are the
    # usual source of a few non-manifold edges and are reported, not failed.
    nonzero = dbl_area[dbl_area > 0]
    if nonzero.size:
        med = float(np.median(nonzero))
        sliver = dbl_area <= med * _SLIVER_REL_FRAC
        health["sliver_tris"] = int(sliver.sum())

    # Topology metrics need WELDED vertices: OCC (and most tessellators) emit
    # unwelded buffers (3 verts/triangle, no sharing), under which every edge
    # looks like a boundary. Weld coincident positions on a bbox-relative grid,
    # remap indices, drop collapsed tris, then count undirected edge usage.
    if diag > 0:
        grid = np.round(pos / (diag * 1e-7)).astype(np.int64)
        _, welded_inv = np.unique(grid, axis=0, return_inverse=True)
        health["welded_verts"] = int(welded_inv.max()) + 1 if len(welded_inv) else 0
        widx = welded_inv[idx]
    else:
        widx = idx
        health["welded_verts"] = n_verts
    wcollapsed = (widx[:, 0] == widx[:, 1]) | (widx[:, 1] == widx[:, 2]) | (widx[:, 0] == widx[:, 2])
    health["welded_collapsed_tris"] = int(wcollapsed.sum())
    we = widx[~wcollapsed]
    if len(we):
        edges = np.concatenate([we[:, [0, 1]], we[:, [1, 2]], we[:, [2, 0]]], axis=0)
        edges.sort(axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        health["boundary_edges"] = int((counts == 1).sum())  # 0 => closed solid
        health["nonmanifold_edges"] = int((counts > 2).sum())
        health["watertight"] = bool((counts == 2).all())
    else:
        health["boundary_edges"] = 0
        health["nonmanifold_edges"] = 0
        health["watertight"] = False
    return health


# Pervasive degeneracy/non-manifoldness is a real defect; a handful of either is
# tessellation noise (pole/seam slivers) that OCC and other kernels alike emit.
_DEGENERATE_FRAC_FAIL = 0.02
_NONMANIFOLD_FRAC_FAIL = 0.01  # of total welded edges


def _flag_mesh(health: dict) -> tuple[bool, str | None, list[str]]:
    """Reduce mesh_health metrics to (ok, reason, warnings).

    Hard failures are unambiguous breakage: empty output, non-finite verts, zero
    total area, or *pervasive* degeneracy/non-manifoldness. A few non-manifold
    edges or slivers — the seam-stitching every tessellator emits at hard
    junctions, verified present in step2glb's output too — are reported as
    warnings, not failures, so a hard FAIL always means real geometry loss
    (e.g. the SAT-pcurve zero-area-plate bug) rather than tessellation noise.
    """
    warnings: list[str] = []
    if health.get("empty"):
        return False, "tessellated to zero triangles", warnings
    problems = []
    if health.get("nonfinite_verts"):
        problems.append(f"{health['nonfinite_verts']} non-finite verts")
    if health.get("area", 0.0) <= 0.0:
        problems.append("zero total area")
    deg = health.get("degenerate_tris", 0)
    frac = health.get("degenerate_frac", 0.0)
    if frac > _DEGENERATE_FRAC_FAIL:
        problems.append(f"{deg} degenerate tris ({frac:.1%})")
    elif deg:
        warnings.append(f"{deg} zero-area tris ({frac:.1%})")

    nm = health.get("nonmanifold_edges", 0)
    n_edges = max(health.get("n_tris", 0) * 3, 1)
    if nm and nm > _NONMANIFOLD_FRAC_FAIL * n_edges:
        problems.append(f"{nm} non-manifold edges ({nm / n_edges:.1%} of edges)")
    elif nm:
        warnings.append(f"{nm} non-manifold edges (seam slivers)")
    if health.get("sliver_tris"):
        warnings.append(f"{health['sliver_tris']} sliver tris")
    if health.get("boundary_edges") and health.get("shape_type") == "solid":
        warnings.append(f"{health['boundary_edges']} open boundary edges on a solid")
    if problems:
        return False, "; ".join(problems), warnings
    return True, None, warnings


def diagnose_object(obj: "BackendGeom", *, linear_deflection: float = -1.0) -> GeometryDiagnostic:
    """Run one object through geom-build + tessellate on the active backend."""
    from ada.cad import active_backend

    backend = active_backend()
    # NB: keep the default lazy — getattr's 3rd arg is always evaluated, and a
    # broken object's repr/name property can itself raise.
    name = getattr(obj, "name", None) or "<unnamed>"
    diag = GeometryDiagnostic(name=name, obj_type=type(obj).__name__, backend=type(backend).__name__)

    # --- stage: geom (definition -> backend shape) ---
    # Two geometry sources in adapy: concept objects (Beam/Plate/Prim) expose
    # solid_geom() -> ada.geom.Geometry which the backend builds; imported objects
    # (Shape from STEP/IFC/SAT) have no solid_geom and instead carry a ready
    # ShapeHandle via solid_occ(). Cover both so the audit spans parse+geom.
    shape = None
    shape_type = None
    source = None
    try:
        try:
            geometry = obj.solid_geom()
            shape = backend.build(geometry)
            source = "solid_geom"
        except NotImplementedError:
            shape = obj.solid_occ()  # imported B-rep -> ShapeHandle
            source = "solid_occ"
        try:
            shape_type = backend.shape_type(shape)
        except Exception:
            shape_type = None
        diag.stages.append(StageResult("geom", True, metrics={"shape_type": shape_type, "source": source}))
    except Exception as ex:  # noqa: BLE001 - the diagnostic must capture any failure
        diag.stages.append(StageResult("geom", False, error=f"{type(ex).__name__}: {ex}"))
        return diag

    # --- stage: tessellate (shape -> mesh -> validity) ---
    try:
        mesh = backend.tessellate(shape, linear_deflection=linear_deflection)
        pos, idx = _mesh_buffers(mesh)
        health = mesh_health(pos, idx, shape_type=shape_type)
        ok, reason, warnings = _flag_mesh(health)
        if warnings:
            health["warnings"] = warnings
        diag.stages.append(StageResult("tessellate", ok, error=reason, metrics=health))
    except Exception as ex:  # noqa: BLE001
        diag.stages.append(StageResult("tessellate", False, error=f"{type(ex).__name__}: {ex}"))
    return diag


@dataclass
class DiagnosticReport:
    """A run over many objects (e.g. a Part) on one backend."""

    backend: str
    diagnostics: list[GeometryDiagnostic] = field(default_factory=list)
    parse: dict | None = None  # importer stats (e.g. ada_stream_stats), if any

    @property
    def failures(self) -> list[GeometryDiagnostic]:
        return [d for d in self.diagnostics if not d.ok]

    def by_stage(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.failures:
            f = d.first_failure
            out[f.stage] = out.get(f.stage, 0) + 1
        return out

    def text_report(self) -> str:
        lines = [
            f"geometry diagnostics — backend={self.backend} objects={len(self.diagnostics)} failures={len(self.failures)}"
        ]
        if self.parse:
            lines.append(f"  parse: {self.parse}")
        if self.failures:
            lines.append(f"  failures by stage: {self.by_stage()}")
            for d in self.failures:
                lines.append("  " + d.summary())
        return "\n".join(lines)


def diagnose_part(part: "Part", *, linear_deflection: float = -1.0, include_ok: bool = True) -> DiagnosticReport:
    """Diagnose every physical geometry object in a Part/Assembly on the active backend."""
    from ada.cad import active_backend

    report = DiagnosticReport(backend=type(active_backend()).__name__)
    objs: list = []
    objs += list(getattr(part, "get_all_physical_objects", lambda: [])())
    if not objs:
        # Fallback: walk the common containers directly.
        for attr in ("beams", "plates", "shapes"):
            objs += list(getattr(part, attr, []) or [])
    for obj in objs:
        if not hasattr(obj, "solid_geom"):
            continue
        try:
            d = diagnose_object(obj, linear_deflection=linear_deflection)
        except Exception as ex:  # noqa: BLE001 - never let one object abort the audit
            d = GeometryDiagnostic(name=getattr(obj, "name", "?"), obj_type=type(obj).__name__, backend=report.backend)
            d.stages.append(StageResult("geom", False, error=f"diagnose crashed: {type(ex).__name__}: {ex}"))
            logger.exception("diagnose_object crashed for %s", getattr(obj, "name", obj))
        if include_ok or not d.ok:
            report.diagnostics.append(d)
    return report


# --------------------------------------------------------------------------- #
# Face-level tessellation coverage (the OCC build + mesh path)
# --------------------------------------------------------------------------- #
# "Did every face of this solid survive BUILD and MESH?"  total = ada.geom faces;
# built = faces that became OCC faces in the shell; meshed = built faces BRepMesh
# produced triangles for. The gaps — dropped (total-built) and unmeshed
# (built-meshed) — are exactly the residual a coverage-complete kernel must recover.
# This is the baseline metric + regression gate for the coverage work.

_GEOM_SURF_LABEL = {
    "Plane": "plane",
    "CurveBoundedPlane": "plane",
    "CylindricalSurface": "cylinder",
    "ConicalSurface": "cone",
    "SphericalSurface": "sphere",
    "ToroidalSurface": "torus",
    "BSplineSurfaceWithKnots": "bspline",
    "RationalBSplineSurfaceWithKnots": "bspline",
}


def _geom_face_label(cfs_face) -> str:
    surf = getattr(cfs_face, "face_surface", None)
    if surf is None:
        return "polyloop"  # plain faceted Face (no analytic surface)
    return _GEOM_SURF_LABEL.get(type(surf).__name__, "other")


_CANON_LABELS = {"plane", "cylinder", "cone", "sphere", "torus", "bspline", "polyloop"}


def _normalize_label(lbl: str) -> str:
    """Collapse a backend's ``face_surface_type`` string to the canonical set used by
    ``_geom_face_label`` so geom-total and built/meshed counts line up by type."""
    if lbl in _CANON_LABELS:
        return lbl
    if lbl == "bezier":
        return "bspline"
    return "other"


@dataclass
class FaceCoverage:
    """Per-face build+mesh coverage of a solid's faces, by surface type."""

    total: int = 0
    built: int = 0
    meshed: int = 0
    by_type: dict = field(default_factory=dict)  # label -> [meshed, built, total]

    @property
    def pct(self) -> float:
        return 100.0 * self.meshed / self.total if self.total else 100.0

    def add(self, other: "FaceCoverage") -> None:
        self.total += other.total
        self.built += other.built
        self.meshed += other.meshed
        for k, v in other.by_type.items():
            cur = self.by_type.setdefault(k, [0, 0, 0])
            for i in range(3):
                cur[i] += v[i]

    def text(self) -> str:
        lines = [
            f"face coverage: {self.meshed}/{self.total} meshed ({self.pct:.1f}%); "
            f"dropped {self.total - self.built}, unmeshed {self.built - self.meshed}"
        ]
        for k in sorted(self.by_type):
            m, b, t = self.by_type[k]
            lines.append(f"  {k:9s} meshed {m}/{t} (dropped {t - b}, unmeshed {b - m})")
        return "\n".join(lines)


def face_coverage(geom, *, linear_deflection: float = -1.0) -> FaceCoverage:
    """Build ``geom`` on the active backend, then per-face check how many of its
    faces survive to a non-empty triangulation, broken down by surface type —
    routed entirely through ``CadBackend`` verbs (``build`` / ``faces`` /
    ``face_surface_type`` / ``tessellate``) so it describes OCC *and* adacpp.

    ``geom`` is an :class:`ada.geom.Geometry` (its ``.geometry`` is the shell/solid
    carrying ``cfs_faces``). For B-rep imports the explicit ``cfs_faces`` list is the
    intended face count, so build-dropped faces show as ``total>built``; procedural
    primitives have no such list, so ``total`` is taken from the built faces."""
    from ada.cad import active_backend

    cov = FaceCoverage()
    shell = getattr(geom, "geometry", geom)
    for f in list(getattr(shell, "cfs_faces", []) or []):
        cov.total += 1
        cov.by_type.setdefault(_geom_face_label(f), [0, 0, 0])[2] += 1
    had_geom_faces = cov.total > 0

    backend = active_backend()
    try:
        shape = backend.build(geom)
        face_handles = backend.faces(shape)
    except Exception as ex:  # noqa: BLE001 - whole-solid build/decompose failure → 0 built
        logger.debug("face_coverage: build/faces failed: %s", ex)
        return cov

    for face in face_handles:
        try:
            lbl = _normalize_label(backend.face_surface_type(face))
        except Exception:  # noqa: BLE001 - unknown surface type
            lbl = "other"
        rec = cov.by_type.setdefault(lbl, [0, 0, 0])
        cov.built += 1
        rec[1] += 1
        try:
            pos, idx = _mesh_buffers(backend.tessellate(face, linear_deflection))
            n_tris = 0 if idx is None else len(idx)
        except Exception:  # noqa: BLE001 - face that won't mesh (the unmeshed signal)
            n_tris = 0
        if n_tris > 0:
            cov.meshed += 1
            rec[0] += 1

    if not had_geom_faces:
        # primitive geom: the build is the source of truth for "total"
        cov.total = cov.built
        for rec in cov.by_type.values():
            rec[2] = rec[1]
    return cov


# --------------------------------------------------------------------------- #
# GLB-vs-GLB geometry comparison (e.g. adapy tess path vs step2glb)
# --------------------------------------------------------------------------- #
# Both adapy's GLB writer and the step2glb pipeline emit the same viewer
# contract (scenes[0].extras.id_hierarchy + draw_ranges_node<matid>), so ONE
# extraction path splits either GLB into the same per-part records. Parts are
# matched by LOCATION, not name: step2glb keeps the STEP product names while
# adapy's stream reader emits generic solid_<n>, and instance counts differ —
# so name matching is unreliable, but the same solid tessellated by either
# engine has a near-identical centroid. Both sides are welded and measured the
# same way (never a welded mesh vs a parse-success count — the mistake that
# produced a false non-manifold "bug" earlier), so a divergence is real.

_GLB_MAGIC = 0x46546C67
_CHUNK_JSON = 0x4E4F534A
_COMPONENT_DTYPE = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_TYPE_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _read_glb(glb: "bytes | str") -> tuple[dict, bytes]:
    """Return (gltf_json, bin_chunk) from a GLB given as bytes or a path."""
    import json
    import struct
    from pathlib import Path

    raw = glb if isinstance(glb, (bytes, bytearray)) else Path(glb).read_bytes()
    raw = bytes(raw)
    magic, _ver, _total = struct.unpack_from("<III", raw, 0)
    if magic != _GLB_MAGIC:
        raise ValueError("not a GLB (bad magic)")
    jlen, jtype = struct.unpack_from("<II", raw, 12)
    if jtype != _CHUNK_JSON:
        raise ValueError("first GLB chunk is not JSON")
    tree = json.loads(raw[20 : 20 + jlen])
    bin_chunk = b""
    off = 20 + jlen
    if off + 8 <= len(raw):
        blen, _btype = struct.unpack_from("<II", raw, off)
        bin_chunk = raw[off + 8 : off + 8 + blen]
    return tree, bin_chunk


def _accessor_array(tree: dict, bin_chunk: bytes, acc_idx: int) -> np.ndarray:
    acc = tree["accessors"][acc_idx]
    bv = tree["bufferViews"][acc["bufferView"]]
    dtype = _COMPONENT_DTYPE[acc["componentType"]]
    ncomp = _TYPE_NCOMP[acc["type"]]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"] * ncomp
    arr = np.frombuffer(bin_chunk, dtype=dtype, count=count, offset=start)
    return arr.reshape(-1, ncomp) if ncomp > 1 else arr


@dataclass
class PartRecord:
    """One selectable part of a GLB, measured for comparison."""

    name: str
    area: float
    n_tris: int
    degenerate_tris: int
    centroid: "np.ndarray"  # (3,)
    diag: float  # bbox diagonal (part size)


def glb_parts(glb: "bytes | str") -> list[PartRecord]:
    """Split a GLB into per-part records (one per draw-range, instances kept
    separate), measuring each with the same welded mesh health used everywhere.

    Works on any GLB carrying the adapy viewer contract — adapy's own or
    step2glb's. Falls back to one record per glTF mesh node when extras absent.
    """
    tree, bin_chunk = _read_glb(glb)
    scenes = tree.get("scenes") or [{}]
    extras = scenes[0].get("extras") or {}
    id_hier = extras.get("id_hierarchy") or {}
    nodes = tree.get("nodes", [])
    meshes = tree.get("meshes", [])
    out: list[PartRecord] = []

    def _record(name: str, verts: np.ndarray, flat_idx: np.ndarray):
        if not len(flat_idx):
            return
        used, inv = np.unique(flat_idx, return_inverse=True)
        v = verts[used]
        faces = inv.reshape(-1, 3)
        h = mesh_health(v.reshape(-1), faces.reshape(-1))
        lo, hi = v.min(0), v.max(0)
        out.append(
            PartRecord(
                name=str(name),
                area=float(h.get("area", 0.0)),
                n_tris=int(h.get("n_tris", 0)),
                degenerate_tris=int(h.get("degenerate_tris", 0)),
                centroid=(lo + hi) * 0.5,
                diag=float(np.linalg.norm(hi - lo)),
            )
        )

    for node in nodes:
        if "mesh" not in node:
            continue
        name = node.get("name", "")
        dr = extras.get(f"draw_ranges_{name}")
        prim = meshes[node["mesh"]]["primitives"][0]
        pos = _accessor_array(tree, bin_chunk, prim["attributes"]["POSITION"])
        idx = _accessor_array(tree, bin_chunk, prim["indices"]).reshape(-1).astype(np.int64)
        if dr:
            for part_id, (s0, ln) in dr.items():
                pname = (id_hier.get(part_id) or [part_id])[0]
                _record(pname, pos, idx[s0 : s0 + ln])
        else:
            _record(name or f"mesh{node['mesh']}", pos, idx)
    return out


def _match_by_centroid(parts_a: list[PartRecord], parts_b: list[PartRecord]) -> list[tuple[int, int]]:
    """Greedy nearest-centroid match between two part lists, gated so only the
    same physical solid pairs up: centroids within half the smaller part's size
    and areas within 3x. O(n) via a grid hash (no scipy). Returns (i, j) pairs."""
    if not parts_a or not parts_b:
        return []
    ca = np.array([p.centroid for p in parts_a])
    cb = np.array([p.centroid for p in parts_b])
    allc = np.vstack([ca, cb])
    diag = float(np.linalg.norm(allc.max(0) - allc.min(0))) or 1.0
    cell = diag / 256.0
    grid: dict = {}
    for i, c in enumerate(ca):
        key = tuple((c / cell).astype(np.int64))
        grid.setdefault(key, []).append(i)
    claimed = set()
    pairs: list[tuple[int, int]] = []
    for j, c in enumerate(cb):
        base = (c / cell).astype(np.int64)
        best_i, best_d = -1, np.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()):
                        if i in claimed:
                            continue
                        d = float(np.linalg.norm(ca[i] - c))
                        if d < best_d:
                            best_d, best_i = d, i
        if best_i < 0:
            continue
        pa, pb = parts_a[best_i], parts_b[j]
        tol = 0.5 * min(pa.diag, pb.diag) + cell
        amax = max(pa.area, pb.area)
        area_ok = amax <= 0 or (min(pa.area, pb.area) / amax) >= (1.0 / 3.0)
        if best_d <= tol and area_ok:
            claimed.add(best_i)
            pairs.append((best_i, j))
    return pairs


@dataclass
class GlbComparison:
    """Result of comparing two GLBs' geometry part-by-part (A vs B, by location)."""

    parts_a: int
    parts_b: int
    matched: int
    only_in_a: list[PartRecord]
    only_in_b: list[PartRecord]
    diverged: list[dict]
    totals: dict

    def text_report(self, limit: int = 15) -> str:
        t = self.totals
        lines = [
            f"GLB geometry comparison — A:{self.parts_a} parts B:{self.parts_b} parts, matched {self.matched}",
            f"  total area  A={t['area_a']:.5g}  B={t['area_b']:.5g}  (A/B={t['area_ratio']:.4f})",
            f"  total tris  A={t['tris_a']}  B={t['tris_b']}",
        ]
        if self.only_in_b:
            area = sum(p.area for p in self.only_in_b)
            lines.append(
                f"  MISSING in A (present in B): {len(self.only_in_b)} parts, area={area:.5g} "
                f"e.g. {[p.name for p in self.only_in_b[:8]]}"
            )
        if self.only_in_a:
            area = sum(p.area for p in self.only_in_a)
            lines.append(
                f"  EXTRA in A (absent in B): {len(self.only_in_a)} parts, area={area:.5g} "
                f"e.g. {[p.name for p in self.only_in_a[:8]]}"
            )
        if self.diverged:
            lines.append(f"  DIVERGED area ({len(self.diverged)} matched parts, worst first):")
            for d in self.diverged[:limit]:
                lines.append(
                    f"    A:{d['name_a']} / B:{d['name_b']}: area A={d['area_a']:.4g} "
                    f"B={d['area_b']:.4g} ratio={d['ratio']:.3f}"
                )
        return "\n".join(lines)


@dataclass
class _Agg:
    """A name's geometry summed over its parts/instances (for name matching)."""

    name: str
    area: float
    n_tris: int
    n_parts: int


# adapy labels extra instances of a part ``<name>/<k>``; the streamed emitter bakes
# each instance under the bare product name. Strip the suffix so a product's instances
# aggregate together on both sides.
_INSTANCE_SUFFIX = re.compile(r"/\d+$")


def _aggregate_by_name(parts: list[PartRecord]) -> dict[str, _Agg]:
    agg: dict[str, _Agg] = {}
    for p in parts:
        name = _INSTANCE_SUFFIX.sub("", p.name)
        a = agg.get(name)
        if a is None:
            agg[name] = _Agg(name, p.area, p.n_tris, 1)
        else:
            a.area += p.area
            a.n_tris += p.n_tris
            a.n_parts += 1
    return agg


def compare_glb_geometry(
    glb_a: "bytes | str", glb_b: "bytes | str", *, area_rel_tol: float = 0.1, match: str = "auto"
) -> GlbComparison:
    """Compare two GLBs (A = adapy tess path, B = step2glb).

    ``match``:
      * ``"name"`` — aggregate parts by hierarchy name and compare per name. Robust
        to instance count / granularity differences; use when both sides carry the
        same product names (adapy's stream reader and step2glb both do).
      * ``"spatial"`` — pair individual parts by nearest centroid. Use when names
        don't align.
      * ``"auto"`` (default) — name matching when the two share >50% of names,
        else spatial.

    A name/part in B with no match in A is geometry adapy's tess path lacks; matched
    entries whose welded areas differ by more than ``area_rel_tol`` land in
    ``diverged``. Both sides are welded and measured identically, so differences are
    real (not a welded mesh vs a parse-success count).
    """
    pa = glb_parts(glb_a)
    pb = glb_parts(glb_b)
    totals = {
        "area_a": sum(p.area for p in pa),
        "area_b": sum(p.area for p in pb),
        "tris_a": sum(p.n_tris for p in pa),
        "tris_b": sum(p.n_tris for p in pb),
    }
    totals["area_ratio"] = (totals["area_a"] / totals["area_b"]) if totals["area_b"] else float("inf")

    if match == "auto":
        na, nb = {p.name for p in pa}, {p.name for p in pb}
        overlap = len(na & nb) / max(min(len(na), len(nb)), 1)
        match = "name" if overlap > 0.5 else "spatial"

    if match == "name":
        aa, bb = _aggregate_by_name(pa), _aggregate_by_name(pb)
        shared = set(aa) & set(bb)
        diverged = []
        for name in shared:
            x, y = aa[name].area, bb[name].area
            hi = max(x, y)
            ratio = (min(x, y) / hi) if hi > 0 else 1.0
            if hi > 0 and ratio < (1.0 - area_rel_tol):
                diverged.append({"name_a": name, "name_b": name, "area_a": x, "area_b": y, "ratio": ratio})
        diverged.sort(key=lambda d: d["ratio"])
        return GlbComparison(
            parts_a=len(pa),
            parts_b=len(pb),
            matched=len(shared),
            only_in_a=[aa[n] for n in (set(aa) - set(bb))],
            only_in_b=[bb[n] for n in (set(bb) - set(aa))],
            diverged=diverged,
            totals=totals,
        )

    pairs = _match_by_centroid(pa, pb)
    matched_a = {i for i, _ in pairs}
    matched_b = {j for _, j in pairs}
    diverged = []
    for i, j in pairs:
        x, y = pa[i].area, pb[j].area
        hi = max(x, y)
        ratio = (min(x, y) / hi) if hi > 0 else 1.0
        if hi > 0 and ratio < (1.0 - area_rel_tol):
            diverged.append({"name_a": pa[i].name, "name_b": pb[j].name, "area_a": x, "area_b": y, "ratio": ratio})
    diverged.sort(key=lambda d: d["ratio"])
    return GlbComparison(
        parts_a=len(pa),
        parts_b=len(pb),
        matched=len(pairs),
        only_in_a=[pa[i] for i in range(len(pa)) if i not in matched_a],
        only_in_b=[pb[j] for j in range(len(pb)) if j not in matched_b],
        diverged=diverged,
        totals=totals,
    )

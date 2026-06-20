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
        lines = [f"geometry diagnostics — backend={self.backend} objects={len(self.diagnostics)} failures={len(self.failures)}"]
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

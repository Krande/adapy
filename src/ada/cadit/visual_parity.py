"""Cross-format visual-parity validation.

The same model exported to different formats (GLB, IFC, Genie XML, STEP) must
carry the *same geometry*. A divergence means a converter silently dropped,
merged, or invented geometry on the way through that format — exactly the class
of audit failure a smoke test misses (an empty scene, an IFC that imports no
geometry, a STEP that loses solids).

The AUDIT path (:func:`parity_from_produced_files`, driven by the worker) reads
the ALREADY-PRODUCED output blobs — the ones the audit converted+uploaded with
the production analytic (``cylinder``) strategy — and compares a FORMAT-AGNOSTIC
GEOMETRY INVARIANT: bounding-box extent (strict gate) plus a coarse surface-area
floor. It re-derives nothing, so it validates exactly what ships and does zero
extra conversion.

This replaced an earlier count-based design (re-derive with
``merge_strategy=None``, compare per-object element counts) which had two bugs:
it validated an UNMERGED model production never ships (~71k plates instead of a
handful of analytic cylinder faces), and the ``None`` re-derivation wrote ~1 GB
of temp files per model, stalling the audit worker on nvme write-contention.
Entity-count equality also cannot work under the analytic model — one physical
tube is a single CYLINDRICAL_SURFACE in STEP but several SAT faces in Genie XML —
so a geometry measure, not a count, is the correct invariant.

STEP *sources* keep a separate streaming instance-count fast path
(:func:`parity_for_step_file`); they were never the memory/temp-file problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ada.config import logger

if TYPE_CHECKING:
    import trimesh

    from ada import Assembly

# Structure-preserving formats: (writer(assembly, path), reader(path) -> Assembly, suffix).
# STEP is written via the non-OCC stream writer (the kernel-free AP242 path prod uses), which
# preserves mapped/instanced shapes the OCC writer drops; it falls back to OCC per-file only for
# solids the analytic stream writer can't yet author (swept/revolved/tapered). Read via the
# streaming reader with an OCC fallback (reader="auto").
_FORMAT_IO: dict[str, tuple[Callable, Callable, str]] = {}


def _write_step_parity(assembly, path) -> None:
    """Write STEP for the parity round-trip via the non-OCC stream writer (matches the prod
    ifc/step→step converter path, and preserves multi-instance mapped shapes the OCC writer
    collapses). If the stream writer skipped any solid it can't author analytically
    (swept/revolved/tapered), re-write the whole file with the OCC writer, which covers those —
    so the leg stays lossless either way. (A model mixing mapped instances AND swept solids would
    lose the mapped instances to the OCC rewrite; none exist in the corpus and the stream writer's
    coverage is being extended to remove even that.)"""
    stats = assembly.to_stp(path, writer="stream")
    if isinstance(stats, dict) and stats.get("skipped", 0) > 0:
        assembly.to_stp(path, writer="occ")


def _register_default_formats() -> None:
    if _FORMAT_IO:
        return
    import ada

    _FORMAT_IO["ifc"] = (lambda a, p: a.to_ifc(p), lambda p: ada.from_ifc(p), ".ifc")
    _FORMAT_IO["xml"] = (lambda a, p: a.to_genie_xml(p), lambda p: ada.from_genie_xml(p), ".xml")
    _FORMAT_IO["step"] = (_write_step_parity, lambda p: ada.from_step(p, reader="auto"), ".step")


def visualized_element_count(scene: "trimesh.Scene") -> int:
    """Number of renderable elements in a trimesh scene.

    Counts mesh / polyline entries one-per-object (build the scene with
    ``merge_meshes=False``); excludes the placeholder point cloud the converter
    seeds for otherwise-empty scenes, and zero-vertex entries (the OCC STEP
    fallback reader materializes one empty Shape from a geometry-less file —
    it renders nothing, so it must not count as an element).
    """
    import trimesh

    n = 0
    for geom in scene.geometry.values():
        if isinstance(geom, trimesh.PointCloud):
            continue  # empty-scene placeholder
        if len(getattr(geom, "vertices", ())) == 0:
            continue  # degenerate/empty body — renders nothing
        n += 1
    return n


def assembly_element_count(assembly: "Assembly") -> int:
    """Visualized-element count for an adapy Assembly.

    Counted from the raw tessellation stream — one MeshStore per renderable
    object, the exact set ``meshes_to_trimesh(merge_meshes=False)`` turns into
    scene entries — WITHOUT assembling a trimesh scene. Scene assembly
    (trimesh objects, materials, normals) was ~1/3 of a parity cell's wall
    time and contributes nothing to the count. Same exclusions as
    :func:`visualized_element_count`: point clouds (the empty-scene
    placeholder) and zero-vertex stores (degenerate bodies render nothing)."""
    from itertools import chain

    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.meshes import MeshType

    bt = BatchTessellator()
    # Mirror tessellate_part's object set: physical objects (pipes as
    # segments) + welds, which live in their own per-Part container.
    objects = chain(assembly.get_all_physical_objects(pipe_to_segments=True), assembly.get_all_welds())
    n = 0
    for ms in bt.batch_tessellate(objects):
        if ms is None or ms.type == MeshType.POINTS:
            continue
        if len(ms.position) == 0:
            continue
        n += 1
    return n


@dataclass
class ParityResult:
    # format label -> the per-format measure. Two shapes coexist: the legacy
    # count-based paths (STEP/whole-model) store an int element count; the
    # geometry-invariant path (:func:`parity_from_produced_files`) stores a small
    # dict ``{"area": .., "bbox": .., "tris": ..}`` — a format-agnostic measure of
    # the geometry that actually shipped. ``summary`` renders both.
    counts: dict[str, "int | dict"]
    expected: int  # baseline: legacy = source element count; geometry path = reference triangle count
    consistent: bool  # True iff every compared format matches the baseline / consensus
    # format -> the diverging value (legacy: the mismatching count; geometry path:
    # a short "area/bbox vs ref" reason string). Non-empty => inconsistent.
    mismatches: dict[str, "int | str"] = field(default_factory=dict)
    errors: dict[str, str] = field(
        default_factory=dict
    )  # format -> error message when that format failed to round-trip
    # format -> reason a format was deliberately not compared (it structurally
    # can't represent this source's geometry, so a 0-count isn't a converter
    # fault). Excluded from the consistency verdict.
    skipped: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        status = "OK" if self.consistent and not self.errors else "MISMATCH"

        def _fmt(v) -> str:
            if isinstance(v, dict):
                # geometry measure: show area (the primary invariant) compactly
                if "area" in v:
                    return f"{v['area']:.4g}m2"
                return str(v)
            return str(v)

        parts = [f"{k}={_fmt(v)}" for k, v in self.counts.items()]
        if self.errors:
            parts += [f"{k}=ERR" for k in self.errors]
        if self.skipped:
            parts += [f"{k}=SKIP" for k in self.skipped]
        return f"[{status}] expected={self.expected} " + " ".join(parts)


def load_assembly_auto(path: str | Path) -> "Assembly":
    """Load a source model from disk by suffix, using the same readers the parity
    round-trip exports through. STEP uses ``reader="auto"`` (streaming fast-path,
    OCC fallback) so a large source doesn't force an OCC load."""
    import ada

    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".ifc":
        return ada.from_ifc(p)
    if ext in (".step", ".stp"):
        return ada.from_step(p, reader="auto")
    if ext in (".sat", ".acis"):
        return ada.from_acis(p)
    if ext == ".xml":
        return ada.from_genie_xml(p)
    if ext in (".fem", ".inp", ".sif", ".sin"):
        asm = ada.from_fem(p)
        # Mirror the FEM->CAD converter (_apply_fem_to_objects): it rebuilds
        # Beam/Plate concept objects from the mesh before any structure-
        # preserving export, because those writers emit *concepts*, not the raw
        # mesh. Without this the parity round-trip exports an objectless
        # assembly and every format reads back empty — a false "dropped all
        # geometry", when the converter never exports a bare mesh.
        asm.create_objects_from_fem(merge=True)
        # Drop the mesh so the baseline counts the exported concept geometry,
        # not the auxiliary FEM-mesh viz (mass/spring elements with no concept
        # representation) that no structure-preserving format carries — else the
        # source over-counts against every format.
        from ada.fem import FEM

        for part in asm.get_all_parts_in_assembly(include_self=True):
            if part.fem is not None and len(part.fem.elements) > 0:
                part.fem = FEM(part.fem.name, parent=part)
        return asm
    raise ValueError(f"visual_parity: no loader for source suffix {ext!r}")


# ── Geometry-invariant parity over ALREADY-PRODUCED output files ─────────────
#
# The audit converts each source to its production outputs (step/ifc/xml with the
# analytic ``cylinder`` strategy, glb via to_gltf) and uploads them. This path
# reads those SAME blobs back and compares a FORMAT-AGNOSTIC GEOMETRY MEASURE —
# it never re-derives, so it validates exactly what ships and does zero extra
# conversion. This replaces the old "re-derive with merge_strategy=None, compare
# entity counts" design, which (a) validated an unmerged model production never
# ships and (b) wrote ~1 GB of temp files per model, stalling on nvme contention.
#
# Entity-count equality cannot work under the analytic model: one physical tube is
# a single CYLINDRICAL_SURFACE in STEP but several SAT faces in Genie XML, so the
# counts legitimately differ while the geometry is identical.
#
# INVARIANT CHOICE (validated locally — see the commit): the BOUNDING-BOX EXTENT is
# the strict cross-format gate; total surface AREA is a coarse secondary floor only.
# Absolute area is NOT reliably comparable across adapy's writers because they use
# different DIMENSIONAL representations of the same object: the STEP stream writer
# emits a plate as a single mid-surface (area = one face) while Genie-XML/IFC can
# emit it as a thin solid (area ~= both faces + edges = ~2x), and the shipped glb
# renders FEM beams as zero-area *lines*. The bounding box is invariant to all of
# that — a mid-surface, a thin solid and a line span the same extent — so a dropped
# solid / region / empty output (the "geometry left behind" failure modes) shows as
# a shrunk or zero bbox in every representation, with no false positive from the
# solid-vs-surface split.
_BBOX_REL_TOL = 0.02  # bbox diagonal extent within 2 % of the consensus (largest)
# Secondary floor: a CAD/structural format retaining less than this fraction of the
# largest CAD area has grossly dropped geometry (near-empty). Set well below the
# legitimate solid-vs-mid-surface ratio (~0.5) so that representation difference
# never trips it — only a real, large loss does.
_AREA_GROSS_FLOOR = 0.34

# Extensions measured DIRECTLY as a mesh (already tessellated). Everything else is
# a CAD/structural format that we tessellate through the production ``to_gltf``
# path before measuring.
_MESH_MEASURE_EXTS = frozenset({".glb", ".gltf", ".obj", ".stl", ".ply", ".off"})

# Formats whose area feeds the coarse area floor. The analytic CAD/structural trio
# render solids/surfaces (comparable up to the ~2x solid-vs-surface factor). Mesh
# formats (glb/obj/stl) are excluded from the area floor entirely: their beams are
# zero-area lines, so their absolute area is not comparable — they are validated on
# bbox alone (which DOES include the beam lines, see _measure_scene).
_AREA_FLOOR_FORMATS = frozenset({"step", "stp", "ifc", "xml"})


@dataclass
class _GeomMeasure:
    area: float  # total tessellated surface area (native model units, squared)
    bbox: float  # bounding-box diagonal length (native model units)
    tris: int  # triangle count (secondary; representation-dependent)
    empty: bool  # True when the format produced no renderable geometry at all


def _measure_scene(scene) -> "_GeomMeasure":
    """Geometry measure of a trimesh scene:

    * bbox diagonal — from ``scene.bounds``, which spans ALL geometry including
      Path3D line entities (FEM beams render as lines), so a line-beam and a
      solid-beam of the same member measure the same extent. Scene-graph transforms
      are applied, so translated/rotated instances measure correctly.
    * surface area + triangle count — from the concatenated mesh geometry only
      (lines have no area); transforms baked in via ``dump(concatenate=True)``.

    ``empty`` is True only when the scene has no spatial extent at all (no meshes
    AND no lines) — a total geometry loss."""
    import numpy as np

    bounds = getattr(scene, "bounds", None)
    if bounds is None:
        return _GeomMeasure(0.0, 0.0, 0, True)
    d = np.asarray(bounds[1], dtype=float) - np.asarray(bounds[0], dtype=float)
    bbox = float(np.sqrt(float((d * d).sum())))

    # Concatenate the mesh geometry (transforms baked in) for area/tris. Prefer the
    # newer ``to_geometry`` and fall back to ``dump(concatenate=True)`` on older
    # trimesh — lines have no area, so a line-only scene yields no mesh here.
    dumped = None
    if hasattr(scene, "to_geometry"):
        try:
            dumped = scene.to_geometry()
        except Exception:  # noqa: BLE001 - only line/point geometry, or version quirk
            dumped = None
    if dumped is None:
        try:
            dumped = scene.dump(concatenate=True)
        except Exception:  # noqa: BLE001 - a scene with only line/point geometry
            dumped = None
    if dumped is None or not hasattr(dumped, "area") or len(getattr(dumped, "vertices", ())) == 0:
        # No meshes, but a finite bbox (e.g. a line-only export) is not "empty".
        return _GeomMeasure(0.0, bbox, 0, bbox <= 0.0)
    area = float(dumped.area)
    tris = int(len(getattr(dumped, "faces", ())))
    return _GeomMeasure(area, bbox, tris, bbox <= 0.0)


def _measure_produced_file(fmt: str, path: Path) -> "_GeomMeasure":
    """Load one produced output blob and measure its geometry.

    Mesh formats (glb/obj/stl/…) are measured directly. CAD/structural formats
    (step/ifc/xml) are tessellated through the SAME ``to_gltf`` path production
    ships and then measured, so every format ends up in one consistent unit
    system (all derive from one source; no unit conversion is injected, which
    could itself manufacture a false mismatch). Runs inside the OOM-isolated
    parity child, so a blow-up fails the cell, not the pod."""
    import trimesh

    ext = path.suffix.lower()
    if ext in _MESH_MEASURE_EXTS:
        scene = trimesh.load(str(path), file_type=ext.lstrip("."), process=False)
        if isinstance(scene, trimesh.Trimesh):
            scene = trimesh.Scene(scene)
        return _measure_scene(scene)

    # CAD/structural: tessellate via to_gltf (the production →GLB path), read back.
    import io as _io

    asm = load_assembly_auto(path)
    # A concept format with no physical objects (e.g. FEM→ifc/xml/step of a
    # solid-only mesh — solids have no shell/beam concepts) exports nothing;
    # measure it as empty instead of letting to_gltf raise on an empty scene.
    if not any(True for _ in asm.get_all_physical_objects()):
        return _GeomMeasure(0.0, 0.0, 0, True)
    buf = _io.BytesIO()
    try:
        asm.to_gltf(buf, merge_meshes=True)
    except ValueError as ex:  # trimesh: "Can't export empty scenes!"
        if "empty scen" in str(ex).lower():
            return _GeomMeasure(0.0, 0.0, 0, True)
        raise
    buf.seek(0)
    scene = trimesh.load(buf, file_type="glb", process=False)
    return _measure_scene(scene)


def parity_from_produced_files(source_key: str, produced: dict[str, "Path | None"]) -> ParityResult:
    """Cross-format geometry-invariant parity over already-produced output blobs.

    ``produced`` maps each compared format (``step``/``ifc``/``xml``/``glb``) to the
    local path of its produced blob, or ``None`` when that format's conversion
    failed or was skipped (recorded, never re-derived). For each present format we
    measure bbox diagonal + surface area + triangle count, then flag divergence:

    * BBOX (strict gate, all formats, ``_BBOX_REL_TOL``): reference = the largest
      bbox diagonal. A format that dropped a solid / region / everything — including
      the shipped glb — shrinks (or zeroes) its bbox and is flagged. This is the
      representation-independent invariant (mid-surface, thin solid and line-beam of
      one member all span the same extent), so it never false-positives on the
      solid-vs-surface representation split the way absolute area would.
    * AREA (coarse secondary floor, CAD/structural trio only): a format retaining
      less than ``_AREA_GROSS_FLOOR`` of the largest CAD area has grossly dropped
      geometry (near-empty) — a backstop for a gross loss that somehow preserved the
      bbox. The floor sits well below the legitimate ~0.5 solid-vs-mid-surface ratio
      so that representation difference never trips it.

    ``expected`` (the persisted baseline) is the reference format's triangle count —
    an always-positive integer stand-in for the old element count. The per-format
    ``{"area","bbox","tris"}`` measures go in ``counts``."""
    measures: dict[str, _GeomMeasure] = {}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for fmt in sorted(produced):
        path = produced[fmt]
        if path is None:
            skipped[fmt] = "no produced blob (conversion failed or was skipped)"
            continue
        try:
            measures[fmt] = _measure_produced_file(fmt, Path(path))
        except Exception as ex:  # noqa: BLE001 - record and continue with the other formats
            errors[fmt] = f"{type(ex).__name__}: {ex}"
            logger.warning(f"parity_from_produced_files: measuring {fmt} failed: {ex}")

    counts: dict[str, dict] = {
        fmt: {"area": round(m.area, 3), "bbox": round(m.bbox, 4), "tris": m.tris} for fmt, m in measures.items()
    }

    mismatches: dict[str, str] = {}
    expected = 0

    # A structure-preserving concept format (step/ifc/xml) that carries no geometry
    # is NOT a drop when the source has no concepts to begin with: a solid-only /
    # mesh-only FEM has no shells or beams to reconstruct, so those formats
    # correctly export nothing (the glb still carries the element mesh). Record
    # them as skipped rather than flagging every solid FEM as a mismatch.
    for fmt, m in measures.items():
        if fmt in _AREA_FLOOR_FORMATS and m.empty:
            skipped.setdefault(fmt, "source has no concept geometry (solid-only / mesh-only)")

    # Compare only formats that actually carry geometry. If NONE do, every format
    # agrees there is nothing to render — consistent, not a mismatch.
    live = {fmt: m for fmt, m in measures.items() if not m.empty and fmt not in skipped}
    if live:
        bbox_ref_fmt = max(live, key=lambda f: live[f].bbox)
        bbox_ref = live[bbox_ref_fmt].bbox
        area_ref = max((m.area for f, m in live.items() if f in _AREA_FLOOR_FORMATS), default=0.0)
        expected = int(live[bbox_ref_fmt].tris)

        for fmt, m in live.items():
            reasons: list[str] = []
            if bbox_ref > 0 and m.bbox < (1.0 - _BBOX_REL_TOL) * bbox_ref:
                reasons.append(f"bbox {m.bbox:.4g} vs ref {bbox_ref:.4g} ({(m.bbox / bbox_ref - 1) * 100:+.1f}%)")
            if fmt in _AREA_FLOOR_FORMATS and area_ref > 0 and m.area < _AREA_GROSS_FLOOR * area_ref:
                reasons.append(f"area {m.area:.4g} << ref {area_ref:.4g} (grossly dropped)")
            if reasons:
                mismatches[fmt] = "; ".join(reasons)
        # A NON-concept format (glb, the shipped mesh) that is empty while concepts
        # carry geometry is a genuine total loss — flag it.
        for fmt, m in measures.items():
            if m.empty and fmt not in _AREA_FLOOR_FORMATS and fmt not in skipped:
                mismatches[fmt] = "produced no renderable geometry"

    consistent = bool(measures) and not mismatches and not errors
    return ParityResult(
        counts=counts,
        expected=expected,
        consistent=consistent,
        mismatches=mismatches,
        errors=errors,
        skipped=skipped,
    )


# Formats the audit compares geometry across (∩ with the source's viable targets).
# STEP/IFC/Genie-XML carry analytic solids; GLB is the mesh the viewer loads. OBJ/
# STL are deliberately out — they add no representation the compared set lacks.
PARITY_GEOMETRY_FORMATS: tuple[str, ...] = ("step", "ifc", "xml", "glb")


def _derive_produced_for_parity(path: str | Path, formats: tuple[str, ...], work_dir: Path) -> dict[str, "Path | None"]:
    """Offline fallback for :func:`parity_from_produced_files`: derive the same
    outputs the AUDIT would have uploaded, using the PRODUCTION strategy (analytic
    ``cylinder`` for step/ifc/xml, ``to_gltf`` for glb) — NOT the retired
    ``merge_strategy=None`` unmerged model. Used by the offline
    ``parity_for_source_file`` when no produced blobs are available (local `ada`
    usage); the audit worker fetches the real blobs instead of calling this."""
    import ada

    p = Path(path)
    is_fem = p.suffix.lower() in _FEM_PARITY_EXTS
    produced: dict[str, Path | None] = {}
    for fmt in formats:
        out = work_dir / f"produced.{fmt}"
        try:
            asm = ada.from_fem(p) if is_fem else load_assembly_auto(p)
            if fmt == "glb":
                asm.to_gltf(out, merge_meshes=True)
            elif fmt in ("step", "stp"):
                asm.to_stp(out, writer="stream", merge_strategy="cylinder", fuse_fem=True)
            elif fmt == "ifc":
                asm.to_ifc(destination=str(out), streaming=True, merge_strategy="cylinder")
            elif fmt == "xml":
                asm.to_genie_xml(destination_xml=str(out), streaming=True, merge_strategy="cylinder")
            else:
                continue
            produced[fmt] = out if out.exists() and out.stat().st_size > 0 else None
        except Exception as ex:  # noqa: BLE001 - a format that can't carry this source is recorded, not fatal
            logger.warning(f"_derive_produced_for_parity: {fmt} derive failed: {ex}")
            produced[fmt] = None
    return produced


def _count_curve_set_roots(path: str | Path, size_limit: int = 64_000_000) -> int:
    """Number of loose curve/geometric-set roots (one per placed curve body — e.g. an evaluated
    alignment reference curve) in a STEP file. The solid-only native reader skips these; each is a
    distinct GEOMETRIC_CURVE_SET / GEOMETRIC_SET entity definition. Size-bounded (such wireframe
    bodies are tiny; a multi-GB solid assembly carries none worth a full scan)."""
    try:
        p = Path(path)
        if p.stat().st_size > size_limit:
            return 0
        data = p.read_bytes()
    except OSError:
        return 0
    # "GEOMETRIC_CURVE_SET(" does not contain "GEOMETRIC_SET(" as a substring, so counting both
    # names never double-counts.
    return data.count(b"GEOMETRIC_CURVE_SET(") + data.count(b"GEOMETRIC_SET(")


def _count_step_instances(path: str | Path) -> int:
    """Number of placed solid instances in a STEP file. Counted by streaming the
    native parser and reading each solid's transform list from the metadata —
    WITHOUT hydrating ada.geom or tessellating (the geometry is never needed, only
    the count). Falls back to the pure-Python streaming reader."""
    from ada.cadit.step.read.native_reader import native_adacpp_step_available

    # Native StepNgeomStream counts the solids; add the loose curve/geometric-set roots it can't
    # see (one per placed curve body). This keeps native grouping/transform semantics instead of a
    # whole-model reload, which would ungroup an OCC-exploded multi-face solid and over-count.
    if native_adacpp_step_available():
        import adacpp

        total = 0
        for _nbytes, meta in adacpp.cad.StepNgeomStream(str(path)):
            tf = meta.transforms
            total += len(tf) if tf else 1
        return total + _count_curve_set_roots(path)

    import ada

    return sum(len(g.transforms) if g.transforms else 1 for g in ada.iter_from_step(path, reader="tolerant"))


def _count_step_product_instances(path: str | Path) -> int | None:
    """Placed *product* instances in a STEP file — the round-trip count that matches
    how the source object model and the IFC leg count elements.

    A writer may split one object's multi-body geometry into several geometry roots
    under a single product (OCC's ``to_stp`` writes one SHELL_BASED_SURFACE_MODEL per
    shell of a sewn multi-shell body; a mapped IFC representation becomes one brep per
    mapped item) — the object is still ONE element per placed instance, so roots that
    share their owning product's representation are grouped before counting. Roots
    without assembly metadata (flat/baked files) group by their own id, i.e. count
    exactly as :func:`_count_step_instances`. None when the native parser is
    unavailable (caller falls back to the reload count)."""
    from ada.cadit.step.read.native_reader import native_adacpp_step_available

    if not native_adacpp_step_available():
        return None
    import adacpp

    groups: dict[tuple, int] = {}
    for _nbytes, meta in adacpp.cad.StepNgeomStream(str(path)):
        ip = meta.instance_paths
        # the deepest path level is the solid's own (rep_id, product_name)
        key = tuple(ip[0][-1]) if (ip and ip[0]) else ("root", meta.id)
        n = len(meta.transforms) or 1
        groups[key] = max(groups.get(key, 0), n)
    # The native StepNgeomStream is solid-only but GROUPS a multi-face solid (OCC's to_stp splits a
    # sectioned/swept or sewn multi-shell solid into many SHELL_BASED_SURFACE_MODEL roots) back into
    # its one owning product. A whole-model reload would UNGROUP that explosion and massively
    # over-count, so instead of deferring we keep the grouped solid count and ADD the loose
    # curve/geometric-set roots (one per placed curve body) the native reader can't see.
    return sum(groups.values()) + _count_curve_set_roots(path)


def _count_ifc_proxies(path: str | Path) -> int:
    """Count ``IfcBuildingElementProxy`` entities in an IFC file via a bounded text
    scan — the streaming STEP→IFC writer emits exactly one per placed solid instance,
    so this matches :func:`_count_step_instances` for a clean round-trip."""
    n = 0
    with open(path, "r", errors="ignore") as f:
        for line in f:
            if "IFCBUILDINGELEMENTPROXY(" in line.upper():
                n += 1
    return n


def _native_step_parity(path: str | Path, formats: tuple[str, ...]) -> "ParityResult | None":
    """Cross-format parity in ONE native parse via ``adacpp.cad.step_parity`` — resolve
    each root once and run both the STEP->IFC and STEP->STEP emitters over it, counting
    placed instances + faces WITHOUT writing any output. Returns None (caller falls back
    to the per-writer path) when adacpp / the verb is unavailable or errors.

    The metric is unchanged from the per-writer path: ``expected`` is the source
    placed-instance count (``total_instances``) and each format's count is the instances
    it emitted, so a dropped solid shows as a mismatch. A format that emits a solid but
    loses faces (``faces_dropped`` > 0) is flagged as an error even if its instance count
    matches — the finer-grained 'no geometry left behind' guard the counts alone can't see.
    """
    try:
        from ada.cadit.step.read.native_reader import native_adacpp_step_available

        if not native_adacpp_step_available():
            return None
        import adacpp

        if not hasattr(adacpp.cad, "step_parity"):
            return None
    except Exception:  # noqa: BLE001
        return None

    try:
        d = adacpp.cad.step_parity(str(path))
    except Exception as ex:  # noqa: BLE001 - fall back to the per-writer path on any native error
        logger.warning(f"native step_parity failed ({ex}); falling back to per-writer parity")
        return None

    baseline = int(d.get("total_instances") or d.get("solids_in") or 0)
    counts: dict[str, int] = {"source": baseline}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    fmt_stats = {"ifc": d.get("ifc"), "step": d.get("step")}
    for fmt in formats:
        if fmt in fmt_stats and fmt_stats[fmt] is not None:
            fd = fmt_stats[fmt]
            counts[fmt] = int(fd.get("instances") or 0)
            dropped = int(fd.get("faces_dropped") or 0)
            if dropped:
                errors[fmt] = f"{dropped} faces dropped: {dict(fd.get('drop_reasons') or {})}"
        elif fmt == "xml":
            skipped["xml"] = "raw CAD B-rep has no Genie-XML concept representation"
        else:
            errors[fmt] = f"unknown format {fmt!r}"

    mismatches = {k: v for k, v in counts.items() if k != "source" and v != baseline}
    return ParityResult(
        counts=counts,
        expected=baseline,
        consistent=not mismatches and not errors,
        mismatches=mismatches,
        errors=errors,
        skipped=skipped,
    )


def parity_for_step_file(
    path: str | Path,
    formats: tuple[str, ...] = ("ifc", "xml", "step"),
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """Streaming cross-format parity for a STEP source — never loads or tessellates
    the whole model. The metric is the placed-instance count, which the streaming
    writers now report directly from their single native parse (``instances`` = solids
    they emitted, ``total_instances`` = every source instance they saw), so parity no
    longer re-parses the multi-GB outputs to count them:

    * source — the writers' ``total_instances`` (the source-side count from the same
      native parse that drives the export; a separate ``_count_step_instances`` pass
      is only the fallback when no writer runs).
    * step   — ``stream_step_to_step`` returns the instances it wrote.
    * ifc    — ``stream_step_to_ifc`` returns the IfcBuildingElementProxy it wrote.
    * xml    — skipped: raw CAD B-rep has no Genie-XML concept representation.

    A writer that drops a solid (unsupported geometry) reports ``instances <
    total_instances``, which surfaces as a mismatch — the exact case parity exists to
    catch — without the whole-model re-read + re-tessellation that OOM'd / timed out.

    Fast path: ``adacpp.cad.step_parity`` does all of the above in ONE native parse
    (both emitters share the per-solid resolve, nothing is serialised to disk) — ~17x
    faster than driving the two Python writers. Falls back to them when unavailable.
    """
    native = _native_step_parity(path, formats)
    if native is not None:
        return native

    import tempfile

    from ada.cadit.step.write.stream_step_to_ifc import stream_step_to_ifc
    from ada.cadit.step.write.stream_step_to_step import stream_step_to_step

    tmp_ctx = None
    if work_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory()
        work_dir = tmp_ctx.name
    work_dir = Path(work_dir)

    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    seen: int | None = None  # source instance count, taken from the writers' single parse
    try:
        for fmt in formats:
            try:
                if fmt == "step":
                    stats = stream_step_to_step(path, work_dir / "parity.step")
                    counts["step"] = stats["instances"]
                    seen = stats["total_instances"] if seen is None else seen
                elif fmt == "ifc":
                    stats = stream_step_to_ifc(path, work_dir / "parity.ifc")
                    counts["ifc"] = stats["instances"]
                    seen = stats["total_instances"] if seen is None else seen
                elif fmt == "xml":
                    skipped["xml"] = "raw CAD B-rep has no Genie-XML concept representation"
                else:
                    errors[fmt] = f"unknown format {fmt!r}"
            except Exception as ex:  # noqa: BLE001 - record and continue with the other formats
                errors[fmt] = f"{type(ex).__name__}: {ex}"
                logger.warning(f"parity_for_step_file: {fmt} round-trip failed: {ex}")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    # Fall back to an explicit native source count only when no writer produced one
    # (e.g. formats == ("xml",)); otherwise reuse the writers' parse (no extra pass).
    baseline = seen if seen is not None else _count_step_instances(path)
    counts = {"source": baseline, **counts}
    mismatches = {k: v for k, v in counts.items() if k != "source" and v != baseline}
    return ParityResult(
        counts=counts,
        expected=baseline,
        consistent=not mismatches and not errors,
        mismatches=mismatches,
        errors=errors,
        skipped=skipped,
    )


# NOTE: The old count-based FEM parity (parity_for_fem_file + its
# _streaming_baseline_count / _count_format_entities / _PARITY_MERGE_STRATEGY=None
# helpers) has been RETIRED. It re-derived the source with merge_strategy=None (one
# plate per FEM element) and compared entity counts for equality — validating an
# unmerged model production never ships, and writing ~1 GB of temp files per model
# (which stalled on nvme write-contention). The FEM parity now reads the
# already-produced analytic (cylinder) output blobs and compares a geometry
# invariant (parity_from_produced_files); the offline fallback derives those same
# production outputs via _derive_produced_for_parity.

_FEM_PARITY_EXTS = (".fem", ".inp", ".sif", ".sin")


def parity_for_source_file(
    path: str | Path,
    formats: tuple[str, ...] = PARITY_GEOMETRY_FORMATS,
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """OFFLINE cross-format parity for a source on disk — the fallback used when no
    already-produced blobs are available (local `ada` usage). The audit worker does
    NOT call this: it fetches the real produced blobs and calls
    :func:`parity_from_produced_files` directly.

    For a FEM source this DERIVES the production outputs (analytic ``cylinder`` for
    step/ifc/xml, ``to_gltf`` for glb — exactly what ships) and compares the
    GEOMETRY INVARIANT, so local and audit agree. This replaces the retired
    ``merge_strategy=None`` + entity-count design, which validated an unmerged model
    production never ships and wrote ~1 GB of temp files per model. STEP sources keep
    the streaming instance-count fast path (never a memory/temp-file problem)."""
    ext = Path(path).suffix.lower()
    # cross_format_parity is count-based over structure-preserving formats only (no
    # glb); keep glb out of the formats it sees.
    count_formats = tuple(f for f in formats if f in ("ifc", "xml", "step")) or ("ifc", "xml", "step")

    if ext in (".step", ".stp"):
        try:
            return parity_for_step_file(path, count_formats, work_dir=work_dir)
        except Exception as ex:  # noqa: BLE001 - fall back to the whole-model path on any failure
            logger.warning(f"parity_for_source_file: STEP fast-path failed ({ex}); using whole-model path")
        return cross_format_parity(load_assembly_auto(path), count_formats, work_dir=work_dir)

    if ext in _FEM_PARITY_EXTS:
        import tempfile

        geom_formats = tuple(f for f in formats if f in PARITY_GEOMETRY_FORMATS) or PARITY_GEOMETRY_FORMATS
        tmp_ctx = None
        if work_dir is None:
            tmp_ctx = tempfile.TemporaryDirectory()
            wd = Path(tmp_ctx.name)
        else:
            wd = Path(work_dir)
        try:
            produced = _derive_produced_for_parity(path, geom_formats, wd)
            return parity_from_produced_files(str(path), produced)
        finally:
            if tmp_ctx is not None:
                tmp_ctx.cleanup()

    return cross_format_parity(load_assembly_auto(path), count_formats, work_dir=work_dir)


def cross_format_parity(
    assembly: "Assembly",
    formats: tuple[str, ...] = ("ifc", "xml", "step"),
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """Export ``assembly`` to each structure-preserving ``format``, reload it, and
    compare the visualized-element count against the source.

    Returns a :class:`ParityResult`. A format that fails to round-trip is recorded
    in ``errors`` (and treated as inconsistent) rather than aborting the others.
    """
    import tempfile

    _register_default_formats()

    baseline = assembly_element_count(assembly)
    counts: dict[str, int] = {"source": baseline}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}

    tmp_ctx = None
    if work_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory()
        work_dir = tmp_ctx.name
    work_dir = Path(work_dir)

    try:
        for fmt in formats:
            io = _FORMAT_IO.get(fmt)
            if io is None:
                errors[fmt] = f"unknown format {fmt!r}"
                continue
            reason = _unrepresentable_reason(fmt, assembly)
            if reason is not None:
                # The format structurally can't carry this source's geometry, so
                # a 0-count is the format's limit, not a converter fault — record
                # and exclude from the verdict instead of flagging a mismatch.
                skipped[fmt] = reason
                continue
            writer, reader, suffix = io
            out = work_dir / f"parity{suffix}"
            try:
                writer(assembly, out)
                # STEP: count placed product instances from the file's metadata rather
                # than reloading — a reload makes one Shape per geometry ROOT, so an
                # object whose body the writer split into several roots under one
                # product (multi-shell SBSM, mapped-item breps) over-counts vs the
                # source and the IFC leg. Grouping by owning product restores the
                # one-element-per-object convention both other legs use.
                n = _count_step_product_instances(out) if fmt == "step" else None
                if fmt == "step" and n == 0:
                    # The native stream counter sees only solid/B-rep roots — a
                    # wireframe-only output (GEOMETRIC_CURVE_SET wire bodies)
                    # counts 0 there even though the geometry is present. A
                    # 0-count file is small by construction, so the exact reload
                    # count is affordable.
                    n = None
                counts[fmt] = n if n is not None else assembly_element_count(reader(out))
            except Exception as ex:  # noqa: BLE001 - record and continue with the other formats
                errors[fmt] = f"{type(ex).__name__}: {ex}"
                logger.warning(f"cross_format_parity: {fmt} round-trip failed: {ex}")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    mismatches = {k: v for k, v in counts.items() if k != "source" and v != baseline}
    consistent = not mismatches and not errors
    return ParityResult(
        counts=counts,
        expected=baseline,
        consistent=consistent,
        mismatches=mismatches,
        errors=errors,
        skipped=skipped,
    )


def _unrepresentable_reason(fmt: str, assembly: "Assembly") -> str | None:
    """Why ``fmt`` can't carry ``assembly``'s geometry at all, or None if it can.

    Genie XML is a structural-concept format — it only writes Beam / Plate
    (Section) elements, not arbitrary B-rep solids. A source made entirely of
    imported generic ``Shape`` bodies (e.g. a CAD ``.ifc`` / ``.stp`` / ``.sat``)
    therefore round-trips to an empty XML, which is the format's limit rather
    than a converter dropping geometry — so we skip it instead of flagging a
    permanent mismatch. IFC and STEP carry solids, so they're never skipped."""
    if fmt != "xml":
        return None
    from ada import Beam, Plate

    if any(isinstance(o, (Beam, Plate)) for o in assembly.get_all_physical_objects()):
        return None
    # A FEM source is streamed straight from the mesh (Part.iter_objects_from_fem):
    # its Beam/Plate concepts are never materialised into the part containers, so the
    # get_all_physical_objects() scan above sees none. A part carrying shell/line
    # elements DOES stream to plates/beams, so it is representable in Genie XML.
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        fem = getattr(part, "fem", None)
        if fem is not None and (len(fem.elements.shell) or len(fem.elements.lines)):
            return None
    return "Genie XML carries only Beam/Plate concepts, not generic solids"

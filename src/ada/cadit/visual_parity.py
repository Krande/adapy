"""Cross-format visual-parity validation.

The same model exported to different structure-preserving formats (GLB, IFC,
Genie XML, STEP) and rendered must show the *same number of visualized
elements*. A divergence means a converter silently dropped, merged, or invented
geometry on the way through that format — exactly the class of audit failure
that a count-only smoke test misses (e.g. an empty scene, an IFC that imports no
geometry, a STEP that loses solids).

The metric is the number of renderable scene entries built with ``merge_meshes``
disabled, so each physical object maps to one entry (placeholder point clouds
that the converter seeds for empty scenes are not counted). Mesh-only formats
(STL/OBJ/PLY) are intentionally excluded: they carry no per-object identity and
always collapse to a single mesh soup, so they cannot preserve an element count.
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
# STEP is written via the OCC writer (full geometry, not just extrusions) and
# read via the streaming reader with an OCC fallback (reader="auto").
_FORMAT_IO: dict[str, tuple[Callable, Callable, str]] = {}


def _register_default_formats() -> None:
    if _FORMAT_IO:
        return
    import ada

    _FORMAT_IO["ifc"] = (lambda a, p: a.to_ifc(p), lambda p: ada.from_ifc(p), ".ifc")
    _FORMAT_IO["xml"] = (lambda a, p: a.to_genie_xml(p), lambda p: ada.from_genie_xml(p), ".xml")
    _FORMAT_IO["step"] = (lambda a, p: a.to_stp(p), lambda p: ada.from_step(p, reader="auto"), ".step")


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
    counts: dict[str, int]  # format label -> visualized element count ("source" is the baseline)
    expected: int  # the baseline (source) count
    consistent: bool  # True iff every format matches the baseline
    mismatches: dict[str, int] = field(default_factory=dict)  # format -> count, for the ones that differ
    errors: dict[str, str] = field(
        default_factory=dict
    )  # format -> error message when that format failed to round-trip
    # format -> reason a format was deliberately not compared (it structurally
    # can't represent this source's geometry, so a 0-count isn't a converter
    # fault). Excluded from the consistency verdict.
    skipped: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        status = "OK" if self.consistent and not self.errors else "MISMATCH"
        parts = [f"{k}={v}" for k, v in self.counts.items()]
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


def _count_step_instances(path: str | Path) -> int:
    """Number of placed solid instances in a STEP file. Counted by streaming the
    native parser and reading each solid's transform list from the metadata —
    WITHOUT hydrating ada.geom or tessellating (the geometry is never needed, only
    the count). Falls back to the pure-Python streaming reader."""
    from ada.cadit.step.read.native_reader import native_adacpp_step_available
    from ada.cadit.step.write._solid_source import step_has_curve_set_roots

    # The native StepNgeomStream is solid-only; a file with loose curve/geometric-set roots
    # (wireframe bodies) needs the pure-Python tolerant reader to count them too.
    if native_adacpp_step_available() and not step_has_curve_set_roots(path):
        import adacpp

        total = 0
        for _nbytes, meta in adacpp.cad.StepNgeomStream(str(path)):
            tf = meta.transforms
            total += len(tf) if tf else 1
        return total

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
    from ada.cadit.step.write._solid_source import step_has_curve_set_roots

    if not native_adacpp_step_available():
        return None
    # The native StepNgeomStream is solid-only; for a file carrying loose curve/geometric-set
    # roots (wireframe bodies) it undercounts — defer to the caller's lossless reload count.
    if step_has_curve_set_roots(path):
        return None
    import adacpp

    groups: dict[tuple, int] = {}
    for _nbytes, meta in adacpp.cad.StepNgeomStream(str(path)):
        ip = meta.instance_paths
        # the deepest path level is the solid's own (rep_id, product_name)
        key = tuple(ip[0][-1]) if (ip and ip[0]) else ("root", meta.id)
        n = len(meta.transforms) or 1
        groups[key] = max(groups.get(key, 0), n)
    return sum(groups.values())


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


def _concept_count(assembly: "Assembly") -> int:
    """Number of structure-preserving concept objects (beams + plates + shapes) in an
    assembly — the parity metric, counted directly instead of tessellating each."""
    n = 0
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        n += len(part.beams) + len(part.plates) + len(part.shapes)
    return n


def _count_format_entities(fmt: str, path: str | Path) -> int:
    """Count the structural elements in an exported file WITHOUT re-reading or
    re-tessellating the whole model: a bounded text scan (ifc/xml) or the streaming
    solid count (step). One entity per source concept on a clean round-trip."""
    if fmt == "step":
        return _count_step_instances(path)
    n = 0
    if fmt == "ifc":
        # one IfcBeam / IfcPlate per concept; '(' after the type excludes IfcBeamType etc.
        with open(path, errors="ignore") as f:
            for line in f:
                u = line.upper()
                if "IFCBEAM(" in u or "IFCPLATE(" in u or "IFCBUILDINGELEMENTPROXY(" in u:
                    n += 1
    elif fmt == "xml":
        # count the GEOMETRY-bearing elements specifically (each beam -> <straight_beam>,
        # each plate -> <flat_plate>); the generic <structure> wrapper is shared with
        # masses / BCs, which ifc/step don't emit as countable entities.
        with open(path, errors="ignore") as f:
            for line in f:
                n += line.count("<straight_beam") + line.count("<flat_plate")
    return n


# Above this concept count the OCC STEP writer (a.to_stp) reliably runs the worker out
# of memory (Standard_MMgrRaw malloc fail), so the step round-trip is skipped — ifc/xml
# (the concept-native formats) still validate. Below it, OCC handles the export.
_STEP_OCC_CONCEPT_LIMIT = 50_000


def parity_for_fem_file(
    path: str | Path,
    formats: tuple[str, ...] = ("ifc", "xml", "step"),
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """Count-based cross-format parity for a FEM source — rebuild concept objects
    (the converter does the same), export each format, and count the OUTPUT entities
    by a bounded text scan / solid count instead of re-reading + re-tessellating the
    (100k+ plate) model 4×. The whole-model tessellation that timed out the parity is
    gone; only the (unavoidable) concept rebuild + per-format exports remain.
    """
    import tempfile

    import ada
    from ada.fem import FEM

    tmp_ctx = None
    if work_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory()
        work_dir = tmp_ctx.name
    work_dir = Path(work_dir)

    asm = ada.from_fem(path)
    asm.create_objects_from_fem(merge=True)
    # Drop the FEM mesh so the baseline counts the exported concept geometry, not the
    # auxiliary mesh viz (mirrors load_assembly_auto).
    for part in asm.get_all_parts_in_assembly(include_self=True):
        if part.fem is not None and len(part.fem.elements) > 0:
            part.fem = FEM(part.fem.name, parent=part)

    baseline = _concept_count(asm)
    # ifc/xml: the STREAMING writers (merge_strategy=None streams the already-built
    # concepts — bounded, count-matches the baseline). step: the OCC writer, which
    # doesn't scale past tens of thousands of plates (malloc fail), so it's guarded
    # by concept count below.
    writers: dict[str, tuple] = {
        "ifc": (lambda a, p: a.to_ifc(p, streaming=True, merge_strategy=None), ".ifc"),
        "xml": (lambda a, p: a.to_genie_xml(p, streaming=True, merge_strategy=None), ".xml"),
        "step": (lambda a, p: a.to_stp(p), ".step"),
    }
    counts: dict[str, int] = {"source": baseline}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    try:
        for fmt in formats:
            wsuf = writers.get(fmt)
            if wsuf is None:
                errors[fmt] = f"unknown format {fmt!r}"
                continue
            reason = _unrepresentable_reason(fmt, asm)
            if reason is not None:
                skipped[fmt] = reason
                continue
            if fmt == "step" and baseline > _STEP_OCC_CONCEPT_LIMIT:
                skipped["step"] = f"OCC STEP writer does not scale to {baseline} concepts"
                continue
            writer, suffix = wsuf
            out = work_dir / f"parity{suffix}"
            try:
                writer(asm, out)
                counts[fmt] = _count_format_entities(fmt, out)
            except Exception as ex:  # noqa: BLE001 - record and continue with the other formats
                errors[fmt] = f"{type(ex).__name__}: {ex}"
                logger.warning(f"parity_for_fem_file: {fmt} round-trip failed: {ex}")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    mismatches = {k: v for k, v in counts.items() if k != "source" and v != baseline}
    return ParityResult(
        counts=counts,
        expected=baseline,
        consistent=not mismatches and not errors,
        mismatches=mismatches,
        errors=errors,
        skipped=skipped,
    )


_FEM_PARITY_EXTS = (".fem", ".inp", ".sif", ".sin")


def parity_for_source_file(
    path: str | Path,
    formats: tuple[str, ...] = ("ifc", "xml", "step"),
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """Load a source model from disk and run :func:`cross_format_parity` on it.

    STEP and FEM sources take count-based fast paths (:func:`parity_for_step_file` /
    :func:`parity_for_fem_file`) — bounded memory, no whole-model tessellation — since
    those are exactly the multi-GB / 100k-plate models that OOM'd / timed out the
    tessellation path. Each falls back to the whole-model path on any failure."""
    ext = Path(path).suffix.lower()
    fast = None
    if ext in (".step", ".stp"):
        fast = parity_for_step_file
    elif ext in _FEM_PARITY_EXTS:
        fast = parity_for_fem_file
    if fast is not None:
        try:
            return fast(path, formats, work_dir=work_dir)
        except Exception as ex:  # noqa: BLE001 - fall back to the whole-model path on any failure
            logger.warning(f"parity_for_source_file: count-based parity failed ({ex}); using whole-model path")
    return cross_format_parity(load_assembly_auto(path), formats, work_dir=work_dir)


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
    return "Genie XML carries only Beam/Plate concepts, not generic solids"

"""Source-to-target format converter for the hosted viewer.

Synchronous, stateless function: takes a path to a local source file
(the worker has streamed it from object storage to a tempfile already)
and returns the bytes of the requested target format. The worker runs
this in a threadpool so it doesn't block the asyncio loop.

Source-on-disk rather than source-as-bytes is deliberate — Sesam SIF
result decks routinely run 500 MB-1 GB and we don't want the byte
buffer in worker RAM.

Three flavors of target:

* GLB / glTF — for the in-browser viewer. Direct GLB pass-through;
  trimesh handles OBJ / STL / PLY / DAE / OFF / glTF; ada-loadable
  formats go through ada.from_<format> -> Assembly -> to_gltf; SIF
  goes through read_sif_file -> FEAResult -> to_gltf with one chosen
  (step, field) pair as the default.

* Non-GLB (IFC, Genie XML) — for user download only. Source must be
  ada-loadable so we can build an Assembly first, then export via the
  matching writer (model.to_ifc / model.to_genie_xml).

The on_progress callback is invoked at named stages so the worker can
update the queue's progress field; values are best-effort estimates,
not measured ratios.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
from typing import Callable, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

# Progress contract: stage name (str), fraction (0..1).
ProgressFn = Callable[[str, float], None]


class UnsupportedFormat(ValueError):
    pass


# Extensions we hand to trimesh directly. trimesh will infer the type
# and emit GLB without ada-py needing to be involved at all.
_TRIMESH_EXTS: frozenset[str] = frozenset({".obj", ".stl", ".ply", ".dae", ".off"})

# Extensions we just pass through unchanged (only meaningful for GLB target).
_PASSTHROUGH_EXTS: frozenset[str] = frozenset({".glb"})

# Source formats that ada-py can load. Required for any non-GLB target.
_ADA_LOADABLE_EXTS: frozenset[str] = frozenset(
    {".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis"}
)

# Multi-file analysis bundles, packaged as zip. Currently only Abaqus
# (`.inp` with `*INCLUDE` chains) is supported; bundle.py rejects other
# families with a clear error.
_BUNDLE_EXTS: frozenset[str] = frozenset({".zip"})

# FEA result files. Sesam SIF is text-based; gets parsed into a
# `FEAResult` and rendered as a tessellated GLB with one chosen
# (step, field) pair. Distinct from `_ADA_LOADABLE_EXTS` because the
# producer is `read_sif_file` → `FEAResult`, not a `Part/Assembly`,
# and the export call signature is different (see `_via_fea_result`).
_FEA_RESULT_EXTS: frozenset[str] = frozenset({".sif"})

# Allowed target formats. Each value is the file extension (with dot)
# of the produced bytes.
TARGET_FORMATS: frozenset[str] = frozenset({"glb", "ifc", "xml"})


def _ext(key: str) -> str:
    return pathlib.PurePosixPath(key).suffix.lower()


def derived_key_for(
    source_key: str,
    target_format: str = "glb",
    *,
    step: int | None = None,
    field: str | None = None,
) -> str:
    """Map a source key to its derived blob key.

    Convention: derived path mirrors the source path under `_derived/`,
    with `.{target_format}` appended so multiple targets coexist for
    the same source (`_derived/wall.ifc.glb`, `_derived/wall.ifc.xml`,
    ...).

    For FEA result sources (.sif), an explicit (step, field) selection
    produces a distinct key so picked combos cache independently from
    the auto-convert default. Leaving both unset (or the source not
    being a SIF) keeps the bare ``_derived/<src>.<fmt>`` shape.
    """
    fmt = target_format.lstrip(".").lower()
    if fmt not in TARGET_FORMATS:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    src = source_key.strip("/")
    if step is not None and field is not None and is_fea_result_key(source_key):
        # Sanitize field for path-safety: strip / and whitespace,
        # replace anything else weird with _.
        sanitized = "".join(c if c.isalnum() or c in "-_." else "_" for c in field)
        return f"_derived/{src}.s{int(step)}.{sanitized}.{fmt}"
    return f"_derived/{src}.{fmt}"


# Suffix appended to derived keys for cached result-meta JSON. Lives in
# the same _derived/ namespace, so it's hidden from the user file list
# but still scoped to the source.
_FEA_META_SUFFIX = ".meta.json"


def fea_meta_key_for(source_key: str) -> str:
    src = source_key.strip("/")
    return f"_derived/{src}{_FEA_META_SUFFIX}"


def is_derived_key(key: str) -> bool:
    return key.lstrip("/").startswith("_derived/")


def is_versions_artefact_key(key: str) -> bool:
    """``versions/<branch>/<commit>/<file>`` blobs are pre-built outputs
    pushed by CI rather than conversion sources, so the supported-source
    extension whitelist doesn't apply to them. The ``_derived/`` guard
    still does.
    """
    return key.lstrip("/").startswith("versions/")


def is_supported_source(key: str) -> bool:
    ext = _ext(key)
    return (
        ext in _PASSTHROUGH_EXTS
        or ext in _TRIMESH_EXTS
        or ext in {".gltf"}
        or ext in _ADA_LOADABLE_EXTS
        or ext in _BUNDLE_EXTS
        or ext in _FEA_RESULT_EXTS
    )


def supported_targets_for(source_key: str) -> list[str]:
    """Return the target formats viable for a given source key. Used
    by the frontend to render only the conversion options that will
    actually succeed."""
    ext = _ext(source_key)
    targets: list[str] = []
    if ext in _PASSTHROUGH_EXTS or ext in _TRIMESH_EXTS or ext == ".gltf":
        targets.append("glb")
    if ext in _ADA_LOADABLE_EXTS:
        # ada-loadable sources can produce any target.
        targets = ["glb", "ifc", "xml"]
    if ext in _BUNDLE_EXTS:
        # Bundles unpack to an Abaqus deck, which goes through the
        # same ada-py path as a single .inp — so all three targets are
        # available. Validation runs at convert time, not here.
        targets = ["glb", "ifc", "xml"]
    if ext in _FEA_RESULT_EXTS:
        # SIF carries result fields, not geometry an IFC/XML writer can
        # consume. Visual GLB only for now.
        targets = ["glb"]
    return targets


def _passthrough(src_path: pathlib.Path, on_progress: ProgressFn) -> bytes:
    on_progress("ready", 1.0)
    return src_path.read_bytes()


def _via_trimesh(src_path: pathlib.Path, ext: str, on_progress: ProgressFn) -> bytes:
    import trimesh

    on_progress("loading", 0.2)
    scene = trimesh.load(str(src_path), file_type=ext.lstrip("."))
    on_progress("exporting", 0.8)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type="glb")
    on_progress("ready", 1.0)
    return out.getvalue()


def _via_gltf_to_glb(src_path: pathlib.Path, on_progress: ProgressFn) -> bytes:
    """glTF (text JSON) → GLB (binary). trimesh handles this round-trip."""
    return _via_trimesh(src_path, ".gltf", on_progress)


def _load_with_ada(src_path: pathlib.Path, ext: str):
    import ada

    if ext == ".ifc":
        return ada.from_ifc(src_path)
    if ext in {".step", ".stp"}:
        return ada.from_step(src_path)
    if ext == ".xml":
        return ada.from_genie_xml(src_path)
    if ext in {".inp", ".fem"}:
        return ada.from_fem(src_path)
    if ext in {".sat", ".acis"}:
        return ada.from_acis(src_path)
    raise UnsupportedFormat(f"ada path does not handle {ext!r}")


def _export_with_ada(model, target_format: str, out_path: pathlib.Path, on_progress: ProgressFn) -> bytes:
    """Run the matching ada exporter and read back the produced bytes."""
    if target_format == "glb":
        on_progress("tessellating", 0.55)
        buf = io.BytesIO()
        # Debug knob: ``ADA_GLB_MERGE_MESHES=false`` (or 0/no) yields one
        # glTF node per source object, naming each by its plate/face id.
        # Useful when triaging tessellation issues — you can compare
        # exporter output by name in any glTF viewer.
        import os as _os
        merge_env = (_os.environ.get("ADA_GLB_MERGE_MESHES") or "").strip().lower()
        merge_meshes = not (merge_env in {"0", "false", "no", "off"})
        model.to_gltf(buf, merge_meshes=merge_meshes)
        on_progress("ready", 1.0)
        return buf.getvalue()
    if target_format == "ifc":
        on_progress("writing-ifc", 0.55)
        model.to_ifc(destination=str(out_path))
    elif target_format == "xml":
        on_progress("writing-xml", 0.55)
        model.to_genie_xml(destination_xml=str(out_path))
    else:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    on_progress("ready", 1.0)
    return out_path.read_bytes()


def _via_ada(src_path: pathlib.Path, source_ext: str, target_format: str, on_progress: ProgressFn) -> bytes:
    """Heavy path: load with ada, export to target format. Used for any
    non-trivial source/target combination that needs the full ada-py
    stack. Source already lives on disk (worker streamed it there)."""
    on_progress("parsing", 0.15)
    suffix = ".glb" if target_format == "glb" else f".{target_format}"
    out_path = pathlib.Path(tempfile.mkstemp(suffix=suffix)[1])
    try:
        model = _load_with_ada(src_path, source_ext)
        return _export_with_ada(model, target_format, out_path, on_progress)
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


def _via_bundle(src_path: pathlib.Path, target_format: str, on_progress: ProgressFn) -> bytes:
    """Unpack a zip, validate the include chain, then run ada-py on the
    entry-point with the bundle's tempdir as cwd so relative INCLUDEs
    resolve.

    Bundle errors propagate as :class:`bundle.BundleError`, which the
    worker translates into a job-level ``error`` audit row with the
    user-visible reason ("missing include: foo.inp", "ambiguous
    entry-point: a.inp, b.inp", etc.).
    """
    from . import bundle as bundle_mod

    on_progress("unpacking", 0.05)
    # The bundle module currently inspects from a bytes blob; reading
    # the zip from disk is fine — bundles are bounded (validation
    # rejects pathological archives before we'd OOM).
    data = src_path.read_bytes()
    tmp, info = bundle_mod.unpack_and_inspect(data)
    try:
        on_progress("parsing", 0.20)
        # ada.from_fem reads the file at `info.entry`; the includes it
        # references are next to it under the same tempdir, so relative
        # resolution Just Works without us touching cwd.
        model = _load_with_ada(info.entry, _ext(info.entry.name))
        suffix = ".glb" if target_format == "glb" else f".{target_format}"
        out_path = pathlib.Path(tempfile.mkstemp(suffix=suffix)[1])
        try:
            return _export_with_ada(model, target_format, out_path, on_progress)
        finally:
            try:
                out_path.unlink()
            except OSError:
                pass
    finally:
        tmp.cleanup()


def _pick_default_step_field(result: "FEAResult") -> tuple[int, str]:
    """Choose a reasonable default (step, field) for a fresh SIF render.

    First step from the result's step list, first field name from the
    grouping. Caller surfaces a clean error when either list is empty
    (no result data → nothing to colorize)."""
    steps = result.get_steps()
    fields = result.get_results_grouped_by_field_value()
    if not steps:
        raise UnsupportedFormat("SIF contains no result steps to render")
    if not fields:
        raise UnsupportedFormat("SIF contains no nodal/element fields to render")
    return int(steps[0]), next(iter(fields.keys()))


def is_fea_result_key(key: str) -> bool:
    return _ext(key) in _FEA_RESULT_EXTS


def compute_fea_meta(src_path: pathlib.Path) -> dict:
    """Inspect a result deck and return a JSON-serializable description.

    Shape::
        {
            "steps": [int, ...],
            "fields": [{"name": str, "steps": [int, ...]}, ...],
            "default_step": int,
            "default_field": str,
        }

    Each field carries the list of steps it has data for, so the picker
    can disable invalid combinations. Sesam SIF typically reports every
    field at every step, but we don't assume.

    Caller is expected to run this in a threadpool — read_sif_file is
    synchronous and CPU-heavy on large decks.
    """
    from ada.fem.formats.sesam.results.read_sif import read_sif_file

    result = read_sif_file(str(src_path))
    grouped = result.get_results_grouped_by_field_value()
    steps_global = [int(s) for s in result.get_steps()]
    if not steps_global:
        raise UnsupportedFormat("SIF contains no result steps to render")
    if not grouped:
        raise UnsupportedFormat("SIF contains no nodal/element fields to render")

    fields_payload = []
    for name, datas in grouped.items():
        per_field_steps = sorted({int(d.step) for d in datas})
        fields_payload.append({"name": name, "steps": per_field_steps})

    default_step, default_field = _pick_default_step_field(result)
    return {
        "steps": steps_global,
        "fields": fields_payload,
        "default_step": int(default_step),
        "default_field": default_field,
    }


def _via_fea_result(
    src_path: pathlib.Path,
    target_format: str,
    on_progress: ProgressFn,
    *,
    step: int | None = None,
    field: str | None = None,
) -> bytes:
    """Sesam SIF (text result deck) → GLB tessellated visualisation.

    Uses ``read_sif_file`` to parse the deck into a ``FEAResult`` and
    ``FEAResult.to_gltf`` to write a coloured/warped GLB. When the
    caller leaves step/field unset we fall back to the first available
    pair so an auto-convert at upload time still produces something
    viewable.
    """
    if target_format != "glb":
        raise UnsupportedFormat(
            f"SIF can only target glb, got {target_format!r}"
        )
    from ada.fem.formats.sesam.results.read_sif import read_sif_file

    on_progress("parsing", 0.10)
    result = read_sif_file(str(src_path))

    on_progress("selecting-field", 0.50)
    if step is None or field is None:
        step, field = _pick_default_step_field(result)
    else:
        # Guard against a stale picker selection — the user may have
        # uploaded a new SIF under the same name. Bail with an error
        # the worker will surface to the queued job's audit row.
        available = result.get_results_grouped_by_field_value()
        if field not in available:
            raise UnsupportedFormat(
                f"field {field!r} not in SIF; available: {sorted(available)}"
            )
        if int(step) not in {int(d.step) for d in available[field]}:
            avail_steps = sorted({int(d.step) for d in available[field]})
            raise UnsupportedFormat(
                f"field {field!r} has no data at step {step}; available: {avail_steps}"
            )

    on_progress("tessellating", 0.65)
    out_path = pathlib.Path(tempfile.mkstemp(suffix=".glb")[1])
    try:
        result.to_gltf(out_path, step=int(step), field=field)
        on_progress("ready", 1.0)
        return out_path.read_bytes()
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


def convert(
    src_path: pathlib.Path,
    source_key: str,
    target_format: str = "glb",
    on_progress: ProgressFn | None = None,
    *,
    step: int | None = None,
    field: str | None = None,
) -> bytes:
    """Convert a local source file to the requested target format.

    The worker streams the source from object storage into a tempfile
    and passes its path here, so we never round-trip the full payload
    through a `bytes` buffer in memory. Output is still returned as
    bytes — the worker uploads it via `Storage.put_bytes`.

    ``step`` / ``field`` only apply to FEA result sources (.sif). When
    unset the converter picks the first available pair, matching the
    behavior of the auto-convert at upload time.
    """
    progress = on_progress or (lambda _stage, _frac: None)
    progress("starting", 0.0)

    fmt = target_format.lstrip(".").lower()
    if fmt not in TARGET_FORMATS:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")

    src_ext = _ext(source_key)
    if not src_ext:
        raise UnsupportedFormat(f"missing extension on key {source_key!r}")

    if src_ext in _BUNDLE_EXTS:
        return _via_bundle(src_path, fmt, progress)

    if src_ext in _FEA_RESULT_EXTS:
        return _via_fea_result(src_path, fmt, progress, step=step, field=field)

    if fmt == "glb":
        if src_ext in _PASSTHROUGH_EXTS:
            return _passthrough(src_path, progress)
        if src_ext == ".gltf":
            return _via_gltf_to_glb(src_path, progress)
        if src_ext in _TRIMESH_EXTS:
            return _via_trimesh(src_path, src_ext, progress)
        return _via_ada(src_path, src_ext, "glb", progress)

    # Non-GLB targets require an ada-loadable source.
    if src_ext not in _ADA_LOADABLE_EXTS:
        raise UnsupportedFormat(
            f"target {fmt!r} requires an ada-loadable source; got {src_ext!r}"
        )
    return _via_ada(src_path, src_ext, fmt, progress)


def supported_extensions() -> Iterable[str]:
    return sorted(
        _PASSTHROUGH_EXTS
        | _TRIMESH_EXTS
        | {".gltf"}
        | _ADA_LOADABLE_EXTS
        | _FEA_RESULT_EXTS
        | _BUNDLE_EXTS
    )

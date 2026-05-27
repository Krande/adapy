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


# ── Converter registry ─────────────────────────────────────────────
#
# Each ``(from_ext, to_ext)`` pair lands one entry here at module
# load. Adding a new pair is one line:
#
#     @converter(".step", ".stl")
#     def _step_to_stl(src, on_progress, **_): ...
#
# Two things consume the registry:
#
#  * :func:`convert` dispatches the worker side — no more if-elif
#    chain on the (ext, target) pair.
#  * :meth:`ConverterRegistry.matrix` is what the worker publishes
#    to NATS KV (``conversions: [{from, to: [...]}, ...]``). The
#    API merges every live worker's matrix and surfaces it through
#    ``/api/config``; the SPA's /convert page uses it to populate
#    the target dropdown per-source-extension. New pairs light up
#    in the UI as soon as the registering worker registers.
#
# Both keys carry the leading dot ("." prefix) to stay symmetric
# with the rest of the module (`_ext()` returns suffix with dot).
# The target side is also recorded with a leading dot internally;
# the matrix JSON strips the dot on serialization so the wire
# format matches what /api/config already publishes for
# ``source_exts``.

ConverterFn = Callable[..., bytes]


class ConverterRegistry:
    """Module-level (from_ext, to_ext) → handler table.

    Population happens at import time via the :func:`converter`
    decorator (or :meth:`register` for programmatic registrations
    that fan one handler across multiple source extensions). The
    table is intentionally a plain dict — the worker's job loop
    looks up once per job, so atomicity is not a concern.
    """

    _entries: dict[tuple[str, str], ConverterFn] = {}

    @classmethod
    def register(cls, from_ext: str, to_ext: str, fn: ConverterFn) -> None:
        cls._entries[(from_ext.lower(), to_ext.lower())] = fn

    @classmethod
    def lookup(cls, from_ext: str, to_ext: str) -> ConverterFn | None:
        return cls._entries.get((from_ext.lower(), to_ext.lower()))

    @classmethod
    def all_sources(cls) -> frozenset[str]:
        return frozenset(f for (f, _) in cls._entries)

    @classmethod
    def all_targets(cls) -> frozenset[str]:
        """Target extensions WITHOUT the leading dot — matches the
        rest of the codebase's ``target_format`` convention
        (``"glb"`` not ``".glb"``).
        """
        return frozenset(t.lstrip(".") for (_, t) in cls._entries)

    @classmethod
    def targets_for(cls, from_ext: str) -> list[str]:
        """Sorted list of target_format values viable for the given
        source extension. Targets are returned without the leading
        dot (``["glb", "ifc"]`` not ``[".glb", ".ifc"]``).
        """
        f = from_ext.lower()
        return sorted({t.lstrip(".") for (frm, t) in cls._entries if frm == f})

    @classmethod
    def matrix(cls) -> list[dict]:
        """JSON-serialisable rollup ``[{"from": ".step", "to": ["glb",
        "ifc", "stl"]}, ...]``. Stable ordering so the worker's
        published payload diffs cleanly across heartbeats.
        """
        by_from: dict[str, set[str]] = {}
        for (f, t) in cls._entries:
            by_from.setdefault(f, set()).add(t.lstrip("."))
        return [
            {"from": f, "to": sorted(by_from[f])}
            for f in sorted(by_from)
        ]


def converter(from_ext: str, to_ext: str):
    """Decorator: register ``fn`` as the handler for the
    ``(from_ext, to_ext)`` conversion pair. Both arguments should
    carry the leading dot (``.step`` / ``.glb``) for symmetry with
    the rest of the module.

    Handler signature::

        fn(src_path: Path, on_progress: ProgressFn, **kwargs) -> bytes

    ``**kwargs`` is forwarded from :func:`convert`; today only the
    FEA result handler reads ``step`` / ``field``. Future handlers
    can pull their own knobs out of kwargs without touching the
    dispatch.
    """

    def deco(fn: ConverterFn) -> ConverterFn:
        ConverterRegistry.register(from_ext, to_ext, fn)
        return fn

    return deco


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
_FEA_RESULT_EXTS: frozenset[str] = frozenset({".sif", ".sin"})

# FEA result files supported by the streaming-viewer bake endpoint
# (`/api/scopes/{scope}/fea/manifest`) but NOT by the legacy
# `convert` GLB pipeline. RMED lands here so uploads validate and
# the manifest endpoint accepts the source, without forcing the
# legacy `_via_fea_result` SIF-only handler to grow an RMED branch.
_STREAMING_FEA_EXTS: frozenset[str] = frozenset({".rmed"})

# Source extensions accepted by the streaming-viewer bake (manifest
# endpoint). Mirror of ``ada.fem.results.artefacts.FEA_ARTEFACT_EXTENSIONS``,
# duplicated here because the slim API container can't import that
# module — it transitively pulls ada.fem.results.common which the
# image doesn't carry. Keep both in sync; there's no shared parent
# module both can import.
FEA_ARTEFACT_SOURCE_EXTS: frozenset[str] = frozenset({".rmed", ".sif", ".sin"})

# Union of source extensions the legacy /convert pipeline knows how
# to handle (any target). Computed at the bottom of this module from
# :class:`ConverterRegistry` so adding a ``@converter`` registration
# also widens this set without manual upkeep. Used by /api/config to
# compute the streaming-only subset of worker-advertised extensions
# — i.e. those the SPA should NOT auto-trigger /convert for after
# upload, because the call would 415.


def is_fea_artefact_source(src_key_or_path) -> bool:
    """True if the source extension is in scope for the streaming bake.

    Phase 1 covers .rmed and .sif. The bake itself runs in the worker
    (see ada.fem.results.artefacts.make_stream_reader for the
    extension dispatch); this predicate is the API-side gate.
    """

    suffix = pathlib.PurePosixPath(str(src_key_or_path)).suffix.lower()
    return suffix in FEA_ARTEFACT_SOURCE_EXTS

# ``TARGET_FORMATS`` is computed at the bottom of this module from
# :class:`ConverterRegistry.all_targets` once every ``@converter``
# registration has fired. Module-level so other code can
# ``from .converter import TARGET_FORMATS`` without paying a method
# call on every check. The set's content is the same shape it always
# was (no leading dots; ``{"glb", "ifc", "xml", ...}``).


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

# Per-source prefix for the streaming-viewer artefact tree. Holds the
# manifest, the geometry-only mesh GLB, and one binary blob per field.
# Distinct from `_FEA_META_SUFFIX` (the legacy steps/fields inventory)
# so the two can coexist during the streaming-viewer rollout.
_FEA_ARTEFACT_SUFFIX = ".fea/"


def fea_artefact_prefix_for(source_key: str) -> str:
    """Per-source storage prefix for streaming-viewer FEA artefacts.

    For source ``models/wall.rmed`` the manifest lives at
    ``_derived/models/wall.rmed.fea/fea.manifest.json``; field blobs
    at ``_derived/models/wall.rmed.fea/fea.<field>.bin``; mesh GLB at
    ``_derived/models/wall.rmed.fea/fea.mesh.glb``.
    """

    src = source_key.strip("/")
    return f"_derived/{src}{_FEA_ARTEFACT_SUFFIX}"


def fea_artefact_manifest_key_for(source_key: str) -> str:
    return fea_artefact_prefix_for(source_key) + "fea.manifest.json"


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
        ext in ConverterRegistry.all_sources()
        or ext in _BUNDLE_EXTS
        or ext in _STREAMING_FEA_EXTS
    )


def supported_targets_for(source_key: str) -> list[str]:
    """Return the target formats viable for a given source key.

    Reads from :class:`ConverterRegistry` so new ``@converter``
    registrations show up here automatically. Bundles
    (``.zip``) inherit the targets of the inner-deck format the
    bundle currently emits — which is the same Abaqus ``.inp``
    family the ada-loadable group covers, so we mirror that
    group's targets without re-implementing bundle inspection
    just to answer the dropdown.
    """
    ext = _ext(source_key)
    if ext in _BUNDLE_EXTS:
        # Bundles unpack to a single Abaqus deck; the inner deck is
        # ada-loadable, so it can target any of that group's
        # registrations. We synthesize the answer rather than peek
        # inside the zip on every dropdown call.
        return ConverterRegistry.targets_for(".inp")
    return ConverterRegistry.targets_for(ext)


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
    """Sesam SIF / SIN result deck → GLB tessellated visualisation.

    Routes by extension: ``.sif`` (text) → :func:`read_sif_file`;
    ``.sin`` (Norsam binary) → :func:`read_sin_file` (the pure-Python
    direct path, no SIF text intermediate). Both yield the same
    :class:`FEAResult` shape, then :meth:`FEAResult.to_gltf` writes a
    coloured/warped GLB. When the caller leaves step/field unset we
    fall back to the first available pair so an auto-convert at
    upload time still produces something viewable.
    """
    if target_format != "glb":
        raise UnsupportedFormat(
            f"Sesam results can only target glb, got {target_format!r}"
        )

    on_progress("parsing", 0.10)
    is_sin = src_path.suffix.lower() == ".sin"
    if is_sin:
        from ada.fem.formats.sesam.results.read_sin import (
            read_sin_file,
            read_sin_metadata,
        )

        # When the caller didn't pick a step, use the cheap metadata
        # path to pick one — avoids materialising every step's records
        # just to throw them away (EigenR100: 200 modes × 1.17 M
        # records would blow the 4 GiB worker budget). Then load
        # only that step.
        if step is None or field is None:
            meta = read_sin_metadata(str(src_path))
            if not meta.fields or not meta.steps:
                raise UnsupportedFormat(
                    f"SIN {src_path.name} has no RV* result fields"
                )
            if step is None:
                step = meta.steps[0]
            if field is None:
                # Map SIN type name → FEAResult field name. read_sin
                # exposes the SIN names verbatim; the downstream
                # display layer remaps them.
                field = meta.fields[0]
        result = read_sin_file(str(src_path), step=int(step))
    else:
        from ada.fem.formats.sesam.results.read_sif import read_sif_file

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
            # On the SIN single-step path the field name may be the
            # SIN card name (RVNODDIS, RVFORCES, RVSTRESS) — let the
            # caller's picker remap to whatever the FEAResult emits.
            if is_sin and available:
                field = next(iter(available))
            else:
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


def _via_ada_to_trimesh(
    src_path: pathlib.Path,
    source_ext: str,
    target_ext: str,
    on_progress: ProgressFn,
) -> bytes:
    """Ada-loadable source → trimesh mesh export (``.stl`` / ``.obj``).

    Bridges the same ada-loadable formats ``_via_ada`` handles to
    trimesh's mesh-only export targets. We go through
    :meth:`Part.to_trimesh_scene` so tessellation honours adapy's
    geom-repr / merge-meshes conventions; trimesh just serialises the
    resulting scene.

    No native STL/OBJ ada exporter is needed — trimesh's own writers
    are mature and the GLB pipeline already proves the round-trip
    works.
    """

    import trimesh  # noqa: F401 — verifies the dep is importable

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    on_progress("tessellating", 0.55)
    scene = model.to_trimesh_scene()
    on_progress("exporting", 0.85)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type=target_ext.lstrip("."))
    on_progress("ready", 1.0)
    return out.getvalue()


def _via_ada_to_step(
    src_path: pathlib.Path,
    source_ext: str,
    on_progress: ProgressFn,
) -> bytes:
    """Ada-loadable source → STEP via the OCC writer.

    Primary use is the IFC → STEP interop case (no STEP writer in
    ifcopenshell itself); also exercised by .step / .stp identity
    re-exports, which can be useful for normalising a malformed STEP
    through OCC's parser.
    """

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    on_progress("writing-step", 0.55)
    out_path = pathlib.Path(tempfile.mkstemp(suffix=".step")[1])
    try:
        model.to_stp(str(out_path))
        on_progress("ready", 1.0)
        return out_path.read_bytes()
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


def _via_glb_to_trimesh(
    src_path: pathlib.Path,
    target_ext: str,
    on_progress: ProgressFn,
) -> bytes:
    """``.glb`` → mesh container (``.stl`` / ``.obj``) via trimesh.

    Pure round-trip with no ada involvement; trimesh reads GLB and
    writes whichever mesh format the target asks for. Used to make
    /convert useful as a general 3D-format swiss-knife for users who
    already have a GLB and want a downstream-friendly mesh.
    """

    import trimesh

    on_progress("loading", 0.20)
    scene = trimesh.load(str(src_path), file_type="glb")
    on_progress("exporting", 0.80)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type=target_ext.lstrip("."))
    on_progress("ready", 1.0)
    return out.getvalue()


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

    Dispatches via :class:`ConverterRegistry` — every viable (from,
    to) pair has an explicit registration at the bottom of this
    module. The only special case is multi-file bundles (``.zip``):
    we unpack first, then re-enter the registry against the inner
    entry-point's extension.

    The worker streams the source from object storage into a tempfile
    and passes its path here, so we never round-trip the full payload
    through a `bytes` buffer in memory. Output is still returned as
    bytes — the worker uploads it via `Storage.put_bytes`.

    ``step`` / ``field`` only apply to FEA result sources (.sif /
    .sin). When unset the FEA handler picks the first available pair,
    matching the behavior of the auto-convert at upload time.
    """
    progress = on_progress or (lambda _stage, _frac: None)
    progress("starting", 0.0)

    fmt = target_format.lstrip(".").lower()
    src_ext = _ext(source_key)
    if not src_ext:
        raise UnsupportedFormat(f"missing extension on key {source_key!r}")

    # Multi-file analysis bundle: unpack, then re-enter the registry
    # via the inner entry-point's extension. The recursion stops one
    # level deep because no inner format is itself ``.zip``.
    if src_ext in _BUNDLE_EXTS:
        return _via_bundle(src_path, fmt, progress)

    handler = ConverterRegistry.lookup(src_ext, fmt)
    if handler is None:
        raise UnsupportedFormat(
            f"no converter registered for {src_ext!r} -> {fmt!r}; "
            f"viable targets: {ConverterRegistry.targets_for(src_ext) or 'none'}"
        )
    return handler(src_path, progress, step=step, field=field)


def supported_extensions() -> Iterable[str]:
    """Sorted list of every source extension at least one registered
    converter accepts. Includes bundle wrappers (``.zip``) — those are
    dispatched separately in :func:`convert` but still count as
    supported uploads.
    """
    return sorted(ConverterRegistry.all_sources() | _BUNDLE_EXTS)


# ── Registry population ────────────────────────────────────────────
#
# Every (from, to) pair the worker can serve is enumerated below.
# Handlers above are parameterised on ``src_ext`` / ``target_ext``
# where it makes sense to share code (e.g. one ``_via_ada`` handles
# the eight ada-loadable sources × three writers via a 3-line lambda
# adapter); pairs that need bespoke handling get their own
# registered function.
#
# When you wire a new conversion path in adapy, register it here and
# the worker's NATS KV publication + the SPA's /convert dropdown
# pick it up automatically.


def _register_passthrough_glb() -> None:
    def _h(src, on_progress, **_):
        return _passthrough(src, on_progress)

    ConverterRegistry.register(".glb", "glb", _h)


def _register_trimesh_to_glb() -> None:
    def _gltf(src, on_progress, **_):
        return _via_gltf_to_glb(src, on_progress)

    ConverterRegistry.register(".gltf", "glb", _gltf)

    for ext in _TRIMESH_EXTS:
        # Closure-over-loop-var trap: bind ext via default arg so each
        # registered handler captures its own source extension and the
        # last iteration's value doesn't leak into earlier entries.
        def _h(src, on_progress, *, _ext=ext, **_kw):
            return _via_trimesh(src, _ext, on_progress)

        ConverterRegistry.register(ext, "glb", _h)


def _register_ada_loadable() -> None:
    # Original three targets (glb/ifc/xml) via the long-standing ada
    # writers.
    for ext in _ADA_LOADABLE_EXTS:
        for tgt in ("glb", "ifc", "xml"):
            def _h(src, on_progress, *, _ext=ext, _tgt=tgt, **_kw):
                return _via_ada(src, _ext, _tgt, on_progress)

            ConverterRegistry.register(ext, tgt, _h)

    # New M2 targets: stl / obj (via to_trimesh_scene) and step (via
    # to_stp). All eight ada-loadable sources support all three new
    # targets — to_trimesh_scene tessellates whatever Part the ada
    # loader returned, and to_stp re-exports through OCC.
    for ext in _ADA_LOADABLE_EXTS:
        for tgt in ("stl", "obj"):
            def _h(src, on_progress, *, _ext=ext, _tgt=tgt, **_kw):
                return _via_ada_to_trimesh(src, _ext, f".{_tgt}", on_progress)

            ConverterRegistry.register(ext, tgt, _h)

        def _step(src, on_progress, *, _ext=ext, **_kw):
            return _via_ada_to_step(src, _ext, on_progress)

        ConverterRegistry.register(ext, "step", _step)


def _register_fea_result_to_glb() -> None:
    for ext in _FEA_RESULT_EXTS:
        def _h(src, on_progress, *, _ext=ext, step=None, field=None, **_kw):
            return _via_fea_result(
                src, "glb", on_progress, step=step, field=field,
            )

        ConverterRegistry.register(ext, "glb", _h)


def _register_glb_to_mesh() -> None:
    # GLB → STL / OBJ via pure trimesh. No ada round-trip needed and
    # no ada Assembly is materialised — the user came in with a mesh
    # and wants a mesh out, in a different container.
    for tgt in ("stl", "obj"):
        def _h(src, on_progress, *, _tgt=tgt, **_kw):
            return _via_glb_to_trimesh(src, f".{_tgt}", on_progress)

        ConverterRegistry.register(".glb", tgt, _h)


_register_passthrough_glb()
_register_trimesh_to_glb()
_register_ada_loadable()
_register_fea_result_to_glb()
_register_glb_to_mesh()


# Allowed target formats — populated from the registry once every
# ``_register_*`` call above has fired. Same surface as before
# (frozenset of bare-name target extensions) so external imports
# (``from .converter import TARGET_FORMATS``) keep working.
TARGET_FORMATS: frozenset[str] = ConverterRegistry.all_targets()

# Union of source extensions backed by at least one registered
# converter (legacy ``/convert`` pipeline reach). Bundles are
# included because they unpack to a registered source.
LEGACY_CONVERT_EXTS: frozenset[str] = ConverterRegistry.all_sources() | _BUNDLE_EXTS

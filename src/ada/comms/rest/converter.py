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
import re
import tempfile
from typing import TYPE_CHECKING, Callable, Iterable

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


# Per-pair option schema. Each option is described by a small dict so
# the SPA can render the right widget without baking option names into
# its source. Same shape used both in :class:`ConverterRegistry` and
# on the wire via ``/api/config["conversionMatrix"]``.
#
#   {
#       "name": "mesh_only",        # passed back through the convert
#                                   # body's ``conversion_options`` map
#       "type": "bool",             # bool | string | int | enum
#       "default": False,
#       "description": "...",
#       "enum": [...]               # only when type == "enum"
#   }


class ConverterRegistry:
    """Module-level (from_ext, to_ext) → handler table.

    Population happens at import time via the :func:`converter`
    decorator (or :meth:`register` for programmatic registrations
    that fan one handler across multiple source extensions). The
    table is intentionally a plain dict — the worker's job loop
    looks up once per job, so atomicity is not a concern.

    Each registered pair carries an optional option schema
    (``options_for(from_ext, to_ext)``) describing per-job knobs
    the SPA can surface. The schema is the source of truth — the
    API allowlist and the worker's env-mapping both derive from
    it instead of repeating hardcoded enum lists.
    """

    _entries: dict[tuple[str, str], ConverterFn] = {}
    _options: dict[tuple[str, str], list[dict]] = {}

    @staticmethod
    def _norm_key(from_ext: str, to_ext: str) -> tuple[str, str]:
        """Canonical registry key: source extension WITH leading dot
        (matches ``_ext()`` output), target extension WITHOUT
        (matches the ``target_format`` convention used everywhere
        else — ``"glb"`` not ``".glb"``). Caller can pass either
        form on either side; we normalise both."""
        f = from_ext.lower()
        if not f.startswith("."):
            f = "." + f
        t = to_ext.lower().lstrip(".")
        return (f, t)

    @classmethod
    def register(
        cls,
        from_ext: str,
        to_ext: str,
        fn: ConverterFn,
        *,
        options: list[dict] | None = None,
    ) -> None:
        key = cls._norm_key(from_ext, to_ext)
        cls._entries[key] = fn
        if options:
            cls._options[key] = list(options)

    @classmethod
    def lookup(cls, from_ext: str, to_ext: str) -> ConverterFn | None:
        return cls._entries.get(cls._norm_key(from_ext, to_ext))

    @classmethod
    def options_for(cls, from_ext: str, to_ext: str) -> list[dict]:
        """Option schema for one (from, to) pair. Empty list when
        the pair has no per-job knobs. Returned list is a shallow
        copy so callers can mutate without poisoning the registry."""
        return list(cls._options.get(cls._norm_key(from_ext, to_ext), ()))

    @classmethod
    def all_options(cls) -> set[str]:
        """Union of option names across every registered pair.

        Used by the API's per-job ``conversion_options`` validator
        (replaces the hardcoded allowlist) — any name that no
        registered converter declares gets dropped from the body.
        """
        out: set[str] = set()
        for opts in cls._options.values():
            for opt in opts:
                name = opt.get("name")
                if isinstance(name, str):
                    out.add(name)
        return out

    @classmethod
    def all_sources(cls) -> frozenset[str]:
        return frozenset(f for (f, _) in cls._entries)

    @classmethod
    def all_targets(cls) -> frozenset[str]:
        """Target extensions WITHOUT the leading dot — matches the
        rest of the codebase's ``target_format`` convention
        (``"glb"`` not ``".glb"``). Stored that way already; this
        is just a projection over the key space.
        """
        return frozenset(t for (_, t) in cls._entries)

    @classmethod
    def targets_for(cls, from_ext: str) -> list[str]:
        """Sorted list of target_format values viable for the given
        source extension. Targets are returned without the leading
        dot (``["glb", "ifc"]`` not ``[".glb", ".ifc"]``).
        """
        f = from_ext.lower()
        if not f.startswith("."):
            f = "." + f
        return sorted({t for (frm, t) in cls._entries if frm == f})

    @classmethod
    def matrix(cls) -> list[dict]:
        """JSON-serialisable rollup. Wire shape::

            [{
                "from": ".step",
                "to": ["glb", "ifc", "stl"],
                "options": {
                    "glb": [{"name": ..., "type": ..., ...}, ...],
                    "ifc": [...],
                    "stl": [...],
                },
             }, ...]

        ``options`` is always present; pairs with no per-job knobs
        get an empty list, so a frontend can render unconditionally
        without testing for the key.
        """
        by_from: dict[str, set[str]] = {}
        for f, t in cls._entries:
            by_from.setdefault(f, set()).add(t)
        rows: list[dict] = []
        for f in sorted(by_from):
            targets = sorted(by_from[f])
            opts: dict[str, list[dict]] = {}
            for t in targets:
                opts[t] = list(cls._options.get((f, t), ()))
            rows.append({"from": f, "to": targets, "options": opts})
        return rows


def converter(
    *args,
    accepts: list[str] | None = None,
    exports: list[str] | None = None,
    exclude_identity: bool = True,
    options: list[dict] | None = None,
):
    """Decorator that registers ``fn`` for one or more (from, to)
    conversion pairs.

    Two equivalent invocation styles:

    * **Single pair (positional, legacy):**

      .. code-block:: python

          @converter(".step", ".stl")
          def step_to_stl(src, on_progress, **_): ...

    * **Multi-format with options (new):**

      .. code-block:: python

          @converter(
              accepts=[".inp", ".fem", ".med"],
              exports=[".inp", ".fem", ".med"],
              exclude_identity=True,
              options=[
                  {"name": "mesh_only", "type": "bool", "default": False,
                   "description": "Skip BCs / loads / sections; mesh only."},
              ],
          )
          def fea_to_fea(src, on_progress, *, source_ext, target_ext,
                         mesh_only=False, **_): ...

    Handler signature in the multi-format form receives ``source_ext``
    and ``target_ext`` kwargs so one function body can serve every
    cell in the cartesian product. ``exclude_identity=True`` drops
    same-from-same-to pairs (``.inp → .inp`` etc.) — flip to
    ``False`` if a self-conversion is actually meaningful (e.g.
    re-export through a parser for normalisation).

    ``options`` is a list of schema dicts (see the module-level
    comment on the wire shape) shared across every registered cell
    — declare per-target options by splitting the registrations.
    """

    # Legacy positional form: @converter(".step", ".stl")
    if args:
        if accepts is not None or exports is not None:
            raise TypeError("@converter: pass either positional (from, to) OR " "accepts/exports, not both")
        if len(args) != 2:
            raise TypeError("@converter(positional) expects exactly (from_ext, to_ext)")
        accepts = [args[0]]
        exports = [args[1]]

    if not accepts or not exports:
        raise TypeError("@converter: need at least one accepts and one exports entry")

    # Source extensions are stored WITH a leading dot to match
    # ``_ext()`` output; target extensions are stored WITHOUT
    # (mirrors the ``target_format`` convention everywhere else in
    # the module — ``"glb"``, not ``".glb"``). Normalise the inputs
    # here so caller can pass either form.
    accepts_norm = [("." + a.lstrip(".").lower()) for a in accepts]
    exports_norm = [e.lstrip(".").lower() for e in exports]

    def deco(fn: ConverterFn) -> ConverterFn:
        for src_ext in accepts_norm:
            # ``src_ext`` carries the leading dot (``.inp``);
            # ``tgt_ext`` does not (``fem``). The identity check
            # strips both to the bare extension so ``.inp`` and
            # ``inp`` count as the same format.
            src_bare = src_ext.lstrip(".")
            for tgt_ext in exports_norm:
                if exclude_identity and src_bare == tgt_ext:
                    continue

                # Wrap the bare handler so the registry's uniform
                # ``(src, on_progress, **kw)`` shape gets the
                # source/target pair injected — the user-side body
                # then reads them off kwargs (or named params).
                # Bind src_ext / tgt_ext via default args so each
                # closure captures its own values instead of the
                # loop's last iteration. The handler receives the
                # canonical ``.<ext>`` form for both since user code
                # typically does suffix-based dispatch.
                def _adapter(src, on_progress, *, _s=src_ext, _t=tgt_ext, **kw):
                    return fn(
                        src,
                        on_progress,
                        source_ext=_s,
                        target_ext=f".{_t}",
                        **kw,
                    )

                ConverterRegistry.register(
                    src_ext,
                    tgt_ext,
                    _adapter,
                    options=options,
                )
        return fn

    return deco


# Extensions we hand to trimesh directly. trimesh will infer the type
# and emit GLB without ada-py needing to be involved at all.
_TRIMESH_EXTS: frozenset[str] = frozenset({".obj", ".stl", ".ply", ".dae", ".off"})

# Extensions we just pass through unchanged (only meaningful for GLB target).
_PASSTHROUGH_EXTS: frozenset[str] = frozenset({".glb"})

# Source formats that ada-py can load. Required for any non-GLB target.
_ADA_LOADABLE_EXTS: frozenset[str] = frozenset({".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis"})

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
    return ext in ConverterRegistry.all_sources() or ext in _BUNDLE_EXTS or ext in _STREAMING_FEA_EXTS


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


# FEM source extensions that carry a mesh (nodes + elements) rather than
# concept geometry, and the CAD targets where rebuilding concept objects
# from that mesh is worthwhile.
_FEM_SOURCE_EXTS: frozenset[str] = frozenset({".inp", ".fem", ".sif"})
_FEM_OBJECT_CAD_TARGETS: frozenset[str] = frozenset({"ifc", "xml", "step", "stp"})


def _apply_fem_to_objects(
    model,
    source_ext: str,
    target_format: str,
    fem_to_objects: bool | None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
) -> None:
    """Rebuild concept Beam/Plate objects from a FEM mesh before a CAD
    export.

    A Sesam/Abaqus FEM deck is a mesh (nodes + shell/line elements); the
    IFC / Genie-XML / STEP writers only emit *concept* objects, so without
    this step a FEM → CAD conversion produces almost-empty output. Gated by
    the per-job ``fem_to_objects`` option (default ``True``). No-op for
    non-FEM sources, mesh targets (glb/stl/obj), or an explicit opt-out.

    ``merge_fem_objects`` (default ``True``) merges coplanar shell plates
    and colinear beams of matching section/material so the export isn't a
    cloud of one-object-per-element geometry.

    ``reconstruct_surfaces`` (default ``False``, opt-in) recovers smooth
    structured quad panels as single curved B-spline plates instead of one
    flat plate per element — a large size/time reduction for meshes generated
    from curved panels. Non-reconstructable elements fall back to flat plates.
    """
    if fem_to_objects is False:
        return
    if source_ext.lower() not in _FEM_SOURCE_EXTS:
        return
    if target_format not in _FEM_OBJECT_CAD_TARGETS:
        return
    merge = True if merge_fem_objects is None else bool(merge_fem_objects)
    recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
    model.create_objects_from_fem(merge=merge, reconstruct_surfaces=recon)


def _export_with_ada(
    model,
    target_format: str,
    out_path: pathlib.Path,
    on_progress: ProgressFn,
    *,
    merge_meshes: bool | None = None,
) -> bytes:
    """Run the matching ada exporter and read back the produced bytes.

    ``merge_meshes`` is the per-job override for the ada-loadable →
    GLB pipeline. ``True`` (default) merges every geometry into one
    glTF node per material; ``False`` yields one node per source
    object (debug aid — lets you compare exporter output by Plate /
    Beam / Face name in any glTF viewer). ``None`` falls back to the
    ``ADA_GLB_MERGE_MESHES`` env var so admin-flipped defaults and
    explicit per-job kwargs both work; the kwarg wins when set.
    """
    if target_format == "glb":
        on_progress("tessellating", 0.55)
        buf = io.BytesIO()
        if merge_meshes is None:
            import os as _os

            merge_env = (_os.environ.get("ADA_GLB_MERGE_MESHES") or "").strip().lower()
            merge_meshes = merge_env not in {"0", "false", "no", "off"}
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


def _via_ada(
    src_path: pathlib.Path,
    source_ext: str,
    target_format: str,
    on_progress: ProgressFn,
    *,
    merge_meshes: bool | None = None,
    fem_to_objects: bool | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
) -> bytes:
    """Heavy path: load with ada, export to target format. Used for any
    non-trivial source/target combination that needs the full ada-py
    stack. Source already lives on disk (worker streamed it there).

    ``merge_meshes`` is forwarded to :func:`_export_with_ada` so the
    per-job kwarg path established by the convert() options dispatch
    reaches the actual GLB writer call. Other targets ignore it.
    """
    on_progress("parsing", 0.15)
    suffix = ".glb" if target_format == "glb" else f".{target_format}"
    out_path = pathlib.Path(tempfile.mkstemp(suffix=suffix)[1])
    try:
        model = _load_with_ada(src_path, source_ext)
        _apply_fem_to_objects(model, source_ext, target_format, fem_to_objects, merge_fem_objects, reconstruct_surfaces)
        return _export_with_ada(
            model,
            target_format,
            out_path,
            on_progress,
            merge_meshes=merge_meshes,
        )
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


def _via_bundle(
    src_path: pathlib.Path,
    target_format: str,
    on_progress: ProgressFn,
    *,
    options: dict | None = None,
) -> bytes:
    """Unpack a zip, validate the include chain, then run ada-py on the
    entry-point with the bundle's tempdir as cwd so relative INCLUDEs
    resolve.

    Bundle errors propagate as :class:`bundle.BundleError`, which the
    worker translates into a job-level ``error`` audit row with the
    user-visible reason ("missing include: foo.inp", "ambiguous
    entry-point: a.inp, b.inp", etc.).

    ``options`` is forwarded to :func:`_export_with_ada` so per-job
    knobs (``merge_meshes``, …) survive the unpack indirection.
    """
    from . import bundle as bundle_mod

    on_progress("unpacking", 0.05)
    # The bundle module currently inspects from a bytes blob; reading
    # the zip from disk is fine — bundles are bounded (validation
    # rejects pathological archives before we'd OOM).
    data = src_path.read_bytes()
    tmp, info = bundle_mod.unpack_and_inspect(data)
    opts = options or {}
    try:
        on_progress("parsing", 0.20)
        # ada.from_fem reads the file at `info.entry`; the includes it
        # references are next to it under the same tempdir, so relative
        # resolution Just Works without us touching cwd.
        entry_ext = _ext(info.entry.name)
        model = _load_with_ada(info.entry, entry_ext)
        _apply_fem_to_objects(
            model,
            entry_ext,
            target_format,
            opts.get("fem_to_objects"),
            opts.get("merge_fem_objects"),
            opts.get("reconstruct_surfaces"),
        )
        suffix = ".glb" if target_format == "glb" else f".{target_format}"
        out_path = pathlib.Path(tempfile.mkstemp(suffix=suffix)[1])
        try:
            return _export_with_ada(
                model,
                target_format,
                out_path,
                on_progress,
                merge_meshes=opts.get("merge_meshes"),
            )
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
        raise UnsupportedFormat(f"Sesam results can only target glb, got {target_format!r}")

    on_progress("parsing", 0.10)
    is_sin = src_path.suffix.lower() == ".sin"
    if is_sin:
        from ada.fem.formats.sesam.results.read_sin import (
            read_sin_file,
            read_sin_metadata,
        )

        # When the caller didn't pick a step, use the cheap metadata
        # path to pick one — avoids materialising every step's records
        # just to throw them away (a hundreds-of-modes eigen deck
        # would blow the 4 GiB worker budget). Then load only that
        # step.
        if step is None or field is None:
            meta = read_sin_metadata(str(src_path))
            if not meta.fields or not meta.steps:
                raise UnsupportedFormat(f"SIN {src_path.name} has no RV* result fields")
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
                raise UnsupportedFormat(f"field {field!r} not in SIF; available: {sorted(available)}")
        if int(step) not in {int(d.step) for d in available[field]}:
            avail_steps = sorted({int(d.step) for d in available[field]})
            raise UnsupportedFormat(f"field {field!r} has no data at step {step}; available: {avail_steps}")

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

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    on_progress("tessellating", 0.55)
    scene = model.to_trimesh_scene()
    _strip_unexportable_for(scene, target_ext)
    _seed_empty_scene(scene)
    on_progress("exporting", 0.85)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type=target_ext.lstrip("."))
    on_progress("ready", 1.0)
    return out.getvalue()


# Mesh-only export targets. trimesh's OBJ and STL writers iterate
# scene.geometry and assume every entry is a Trimesh — they break on
# Path3D (polyline-only geometries that adapy emits for line elements
# / open profiles). GLB tolerates Path3D natively so it stays
# unfiltered.
_MESH_ONLY_TARGETS: frozenset[str] = frozenset({".obj", ".stl", ".ply", ".off"})


def _strip_unexportable_for(scene, target_ext: str) -> None:
    """Drop scene entries that ``target_ext``'s writer can't handle.

    For OBJ/STL/PLY/OFF we keep only ``trimesh.Trimesh`` geometries.
    Path3D objects (line elements, open wireframes from the SAT /
    IFC importers) get filtered so the export doesn't AttributeError
    on the trimesh side. The dropped entities are recoverable in
    the GLB output if the user needs them.
    """
    import trimesh

    ext = target_ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in _MESH_ONLY_TARGETS:
        return
    to_drop = []
    for name, geom in scene.geometry.items():
        if not isinstance(geom, trimesh.Trimesh):
            to_drop.append(name)
    for name in to_drop:
        scene.delete_geometry(name)


def _seed_empty_scene(scene) -> None:
    """Trimesh refuses to export a 0-geometry scene with
    ``"Can't export empty scenes!"`` even though every backing format
    (glb/stl/obj) is perfectly happy with zero meshes. When adapy's
    parse drops every face (SAT files containing only construction
    geometry, IFC files with only metadata, etc.) we still want a
    valid file the viewer can load and the audit log to record
    ``status=done`` — the operator dug through the audit details if
    they want to know why the scene was empty.

    Same trick :mod:`ada.fem.results.artefacts` already uses for
    line-only FEA models: seed a degenerate ``PointCloud`` of a
    single origin point so trimesh has *something* to serialise.
    """
    import numpy as np
    import trimesh

    if len(scene.geometry) > 0:
        return
    placeholder = trimesh.PointCloud(vertices=np.zeros((1, 3), dtype=np.float64))
    scene.add_geometry(placeholder, node_name="empty", geom_name="empty")


def _via_ada_to_step(
    src_path: pathlib.Path,
    source_ext: str,
    on_progress: ProgressFn,
    *,
    fem_to_objects: bool | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
) -> bytes:
    """Ada-loadable source → STEP via the OCC writer.

    Primary use is the IFC → STEP interop case (no STEP writer in
    ifcopenshell itself); also exercised by .step / .stp identity
    re-exports, which can be useful for normalising a malformed STEP
    through OCC's parser.
    """

    from ada.config import logger

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    _apply_fem_to_objects(model, source_ext, "step", fem_to_objects, merge_fem_objects, reconstruct_surfaces)
    on_progress("writing-step", 0.55)
    out_path = pathlib.Path(tempfile.mkstemp(suffix=".step")[1])
    try:
        if source_ext.lower() in _FEM_SOURCE_EXTS:
            # A FEM mesh rebuilds into extruded plates/straight beams, which the
            # streaming AP242 writer emits one-at-a-time at constant memory. The
            # default OCC XCAF writer instead accumulates every solid plus a full
            # entity-graph copy and OOMs the worker on large jackets/ships.
            stats = model.to_stp(str(out_path), writer="stream")
            skipped = (stats or {}).get("skipped", 0)
            if skipped:
                logger.warning(f"streaming STEP writer skipped {skipped} non-extrudable object(s)")
        else:
            model.to_stp(str(out_path))
        on_progress("ready", 1.0)
        return out_path.read_bytes()
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


_INCLUDE_RE = re.compile(
    r"^\s*\*INCLUDE\s*,\s*INPUT\s*=\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _inline_abaqus_includes(top_inp: pathlib.Path, max_depth: int = 4) -> bytes:
    """Walk an Abaqus deck and inline every ``*INCLUDE,INPUT=...``
    statement into a single self-contained ``.inp``.

    Adapy's Abaqus writer (``write_parts.py`` / ``write_main_inp.py``)
    emits a multi-file deck — the main ``model.inp`` references
    ``bulk_<part>/aba_bulk.inp`` for mesh data and
    ``core_input_files/<bc|materials|…>.inp`` for the analysis
    surfaces. That layout is correct for running an analysis; it's
    wrong for the /convert contract of "one bytes blob per derived
    key" because anyone who downloads the bytes can't satisfy the
    relative-path includes.

    Resolution is relative to the directory of the file currently
    being walked, so nested includes (a core_input_files/<step>.inp
    that itself references another file) resolve correctly. Missing
    include targets get a passthrough ``** [missing: <path>]`` line
    rather than a hard raise — the writer emits placeholders for
    sections with no data, and we don't want a missing optional
    section file to nuke the conversion.

    ``max_depth`` caps recursion in case the writer ever emits a
    pathological self-referential include chain. Four levels is
    deeper than any layout the writer produces today.
    """

    def _walk(path: pathlib.Path, depth: int) -> str:
        if depth > max_depth:
            return f"** [/convert: include depth cap reached at {path.name}]\n"
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")

        out_lines: list[str] = []
        for line in text.splitlines(keepends=True):
            m = _INCLUDE_RE.match(line.rstrip("\r\n"))
            if not m:
                out_lines.append(line)
                continue
            # Abaqus paths use backslash on Windows-style emit; both
            # POSIX (.split("/")) and backslash-only paths normalize
            # through pathlib if we replace backslashes first.
            inc_rel = m.group(1).replace("\\", "/").strip().strip('"')
            inc_path = (path.parent / inc_rel).resolve()
            if not inc_path.is_file():
                out_lines.append(f"** [/convert: missing include {inc_rel}]\n")
                continue
            out_lines.append(f"** ─── inlined: {inc_rel} ───\n")
            out_lines.append(_walk(inc_path, depth + 1))
            if not out_lines[-1].endswith("\n"):
                out_lines.append("\n")
        return "".join(out_lines)

    return _walk(top_inp, 0).encode("utf-8")


def _via_fea_to_fem(
    src_path: pathlib.Path,
    source_ext: str,
    target_ext: str,
    on_progress: ProgressFn,
) -> bytes:
    """FEA input deck → FEA input deck.

    Both sides use adapy's general FEM dispatch: ``ada.from_fem(src)``
    materialises an ``Assembly`` carrying the full ``Part.fem`` (nodes,
    elements, materials, sections, BCs, loads), then
    ``Assembly.to_fem(name, fem_format, scratch_dir)`` runs the matching
    writer.

    Caveats by target:

    * **.inp** (Abaqus) — the writer emits ``model.inp`` + a sibling
      ``bulk_<part>/aba_bulk.inp`` + ``core_input_files/<...>.inp``
      tree. We inline every ``*INCLUDE`` so the returned bytes are a
      single self-contained deck — running Abaqus on the download
      doesn't need the sibling files.
    * **.fem** (Sesam) — single-file deck; bytes are returned as-is.
    * **.med** (Code_Aster) — the writer emits ``name.med`` (mesh +
      groups) plus ``name.comm`` (analysis-spec template) and a
      ``.adapy_fem.json`` sidecar. We return only the ``.med`` here;
      the ``.comm`` is template-driven and would round-trip a stub
      analysis the user didn't ask for. Honest mesh-export
      semantics; full multi-file deck packaging would be a separate
      zip-output target.
    """

    on_progress("parsing", 0.15)
    import ada

    assembly = ada.from_fem(src_path)
    on_progress("translating", 0.45)

    # ``fem_format`` is a string the general writer dispatcher knows
    # how to resolve (``"abaqus"`` / ``"sesam"`` / ``"code_aster"``).
    fmt = _FEM_TARGET_TO_FORMAT.get(target_ext.lower())
    if fmt is None:
        raise UnsupportedFormat(f"no FEA writer for target {target_ext!r}")

    out_dir = pathlib.Path(tempfile.mkdtemp(prefix="ada-convert-"))
    name = "model"
    try:
        on_progress("writing", 0.65)
        assembly.to_fem(
            name,
            fem_format=fmt,
            scratch_dir=out_dir,
            overwrite=True,
            write_input_files_only=True,
        )
        # Writers nest output in ``{scratch_dir}/{name}/`` — find the
        # produced deck file. Each writer has its own filename
        # conventions:
        #
        # * Abaqus: ``{name}.inp`` (lowercase, matches target_ext)
        # * Code_Aster: ``{name}.med`` plus a ``.comm`` sidecar we
        #   deliberately drop (see docstring)
        # * Sesam: ``{name}T1.FEM`` — uppercase extension, plus a
        #   ``T1`` super-element suffix the writer appends. Globbing
        #   case-insensitively against ``*{target_ext}`` covers both
        #   the suffix and the case difference without hard-coding
        #   either.
        deck = _find_writer_output(out_dir / name, name, target_ext) or _find_writer_output(out_dir, name, target_ext)
        if deck is None:
            raise UnsupportedFormat(
                f"FEA writer ran but no {target_ext} appeared under "
                f"{out_dir} — adapy writer layout may have changed."
            )
        on_progress("ready", 1.0)
        if target_ext.lower() == ".inp":
            return _inline_abaqus_includes(deck)
        return deck.read_bytes()
    finally:
        try:
            import shutil as _sh

            _sh.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


def _find_writer_output(
    directory: pathlib.Path,
    name: str,
    target_ext: str,
) -> pathlib.Path | None:
    """Locate the deck file an FEA writer dropped into ``directory``.

    Looks first for the exact ``{name}{target_ext}`` (lowercase
    case), then for any sibling matching ``*{target_ext}``
    case-insensitively. The fallback covers the Sesam writer's
    ``{name}T1.FEM`` convention (uppercase extension + super-element
    ``T1`` suffix) without each format needing its own special
    case here.
    """
    if not directory.is_dir():
        return None
    canonical = directory / f"{name}{target_ext}"
    if canonical.is_file():
        return canonical
    ext_lower = target_ext.lower()
    for p in directory.iterdir():
        if p.is_file() and p.suffix.lower() == ext_lower:
            return p
    return None


# Map M3 target extensions to the ``fem_format`` strings adapy's
# write-dispatcher understands. Source-side ada.from_fem auto-detects
# from the file extension so no symmetric map is needed.
_FEM_TARGET_TO_FORMAT: dict[str, str] = {
    ".inp": "abaqus",
    ".fem": "sesam",
    ".med": "code_aster",
}


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
    if not isinstance(scene, trimesh.Scene):
        # ``trimesh.load`` can return a single Trimesh for
        # one-mesh sources; wrap so the empty-scene helper has a
        # consistent shape to inspect.
        wrapped = trimesh.Scene()
        wrapped.add_geometry(scene)
        scene = wrapped
    _seed_empty_scene(scene)
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
    options: dict | None = None,
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

    ``options`` is a per-job knob dict — keys match option ``name``
    fields declared at the ``@converter(options=[...])`` site for the
    selected (from, to) pair. Forwarded to the handler as kwargs; the
    handler's adapter (registered by ``@converter``) unpacks the
    options it understands and ignores the rest, so passing unknown
    keys is harmless. Legacy env-var-driven options (use_sat_pcurves
    / pcurve_drive_edge / skip_shapefix) still flow through env vars
    set on the worker subprocess; they're not in the registry schema
    today because the consuming code is in deep OCC paths
    (ada/occ/geom/surfaces.py) that don't yet accept these as
    function parameters. Migrating them is the natural follow-up.
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
        return _via_bundle(src_path, fmt, progress, options=options)

    handler = ConverterRegistry.lookup(src_ext, fmt)
    if handler is None:
        raise UnsupportedFormat(
            f"no converter registered for {src_ext!r} -> {fmt!r}; "
            f"viable targets: {ConverterRegistry.targets_for(src_ext) or 'none'}"
        )
    opts = options or {}
    return handler(src_path, progress, step=step, field=field, **opts)


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
    # Schema for the ada-loadable → GLB pairs. ``merge_meshes`` flows
    # straight into :func:`_export_with_ada`'s ``model.to_gltf`` call.
    # Declared per-target so it shows up on the GLB row only — IFC /
    # XML / STL / OBJ writers don't honour it.
    glb_options = [
        {
            "name": "merge_meshes",
            "type": "bool",
            "default": True,
            "description": (
                "Merge every geometry into a single glTF node per "
                "material (default). Disable to emit one node per "
                "source object — useful for debugging tessellation "
                "by Plate / Beam / Face name."
            ),
        },
    ]

    # Schema for FEM-source → CAD-target pairs. ``fem_to_objects`` rebuilds
    # concept Beam/Plate objects from the mesh before export — without it a
    # FEM → IFC/XML conversion is near-empty (the writers only emit concept
    # geometry). Shown on IFC/XML rows for FEM sources only.
    fem_to_objects_options = [
        {
            "name": "fem_to_objects",
            "type": "bool",
            "default": True,
            "description": (
                "Rebuild concept Beam/Plate objects from the FEM mesh "
                "before export (recommended for CAD targets). Disable to "
                "export only pre-existing concept geometry."
            ),
        },
        {
            "name": "merge_fem_objects",
            "type": "bool",
            "default": True,
            "description": (
                "Merge coplanar shell plates (same material + thickness) "
                "and colinear beams (same section + material) into single "
                "objects. Disable to keep one object per FEM element."
            ),
        },
        {
            "name": "reconstruct_surfaces",
            "type": "bool",
            "default": False,
            "description": (
                "Experimental: recover smooth structured quad panels as single "
                "curved B-spline plates instead of one flat plate per shell "
                "element — far smaller/faster CAD output for meshes generated "
                "from curved panels. Non-reconstructable regions fall back to "
                "flat plates."
            ),
        },
    ]

    # Original three targets (glb/ifc/xml) via the long-standing ada
    # writers.
    for ext in _ADA_LOADABLE_EXTS:
        for tgt in ("glb", "ifc", "xml"):

            def _h(
                src,
                on_progress,
                *,
                _ext=ext,
                _tgt=tgt,
                merge_meshes=None,
                fem_to_objects=None,
                merge_fem_objects=None,
                reconstruct_surfaces=None,
                **_kw,
            ):
                return _via_ada(
                    src,
                    _ext,
                    _tgt,
                    on_progress,
                    merge_meshes=merge_meshes,
                    fem_to_objects=fem_to_objects,
                    merge_fem_objects=merge_fem_objects,
                    reconstruct_surfaces=reconstruct_surfaces,
                )

            if tgt == "glb":
                row_options = glb_options
            elif tgt in ("ifc", "xml") and ext in _FEM_SOURCE_EXTS:
                row_options = fem_to_objects_options
            else:
                row_options = None

            ConverterRegistry.register(ext, tgt, _h, options=row_options)

    # New M2 targets: stl / obj (via to_trimesh_scene) and step (via
    # to_stp). All eight ada-loadable sources support all three new
    # targets — to_trimesh_scene tessellates whatever Part the ada
    # loader returned, and to_stp re-exports through OCC.
    for ext in _ADA_LOADABLE_EXTS:
        for tgt in ("stl", "obj"):

            def _h(src, on_progress, *, _ext=ext, _tgt=tgt, **_kw):
                return _via_ada_to_trimesh(src, _ext, f".{_tgt}", on_progress)

            ConverterRegistry.register(ext, tgt, _h)

        def _step(
            src, on_progress, *, _ext=ext, fem_to_objects=None, merge_fem_objects=None, reconstruct_surfaces=None, **_kw
        ):
            return _via_ada_to_step(
                src,
                _ext,
                on_progress,
                fem_to_objects=fem_to_objects,
                merge_fem_objects=merge_fem_objects,
                reconstruct_surfaces=reconstruct_surfaces,
            )

        ConverterRegistry.register(
            ext,
            "step",
            _step,
            options=(fem_to_objects_options if ext in _FEM_SOURCE_EXTS else None),
        )


def _register_fea_result_to_glb() -> None:
    for ext in _FEA_RESULT_EXTS:

        def _h(src, on_progress, *, _ext=ext, step=None, field=None, **_kw):
            return _via_fea_result(
                src,
                "glb",
                on_progress,
                step=step,
                field=field,
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


# M3: FEA input-deck ↔ FEA input-deck. Six new (from, to) cells in
# one ``@converter`` call. The cartesian product registers everything
# pairwise; ``exclude_identity=True`` drops the three self-pairs
# (``.inp → .inp`` etc.). Same handler body serves every cell — it
# reads ``source_ext`` / ``target_ext`` from kwargs and lets the
# generic adapy dispatcher pick the right writer.


@converter(
    accepts=[".inp", ".fem", ".med"],
    exports=[".inp", ".fem", ".med"],
    exclude_identity=True,
    # Schema-shipping with no entries: confirms the wire format
    # round-trips an empty options list and stays additive. Real
    # per-pair knobs (e.g. ``mesh_only``) land in a follow-up CL
    # together with the worker plumbing that forwards
    # conversion_options into the handler's kwargs (today they're
    # consumed env-var-style before convert() is invoked).
    options=[],
)
def _fea_to_fea(src, on_progress, *, source_ext, target_ext, **_):
    """Abaqus ↔ Sesam ↔ Code_Aster input-deck cross-conversion.

    All three formats have readers AND writers in adapy; cells where
    both ends support the same constructs (nodes, elements,
    materials, sections, BCs, loads) round-trip cleanly. Writers
    fail with adapy-internal errors on sources missing constructs
    they expect — e.g. the Sesam writer raises on inputs without a
    populated ``fem.sections`` table. Those surface as job errors in
    the conversion toast rather than being caught here, since the
    failure mode is informative and silently degrading would hide
    real adapy regressions.

    Code_Aster output is the ``.med`` (mesh + groups) only; the
    matching ``.comm`` analysis-spec template is dropped (see
    :func:`_via_fea_to_fem` for the rationale).
    """

    return _via_fea_to_fem(src, source_ext, target_ext, on_progress)


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

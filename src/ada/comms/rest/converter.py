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
FEA_ARTEFACT_SOURCE_EXTS: frozenset[str] = frozenset(
    # .inp/.fem/.med are design-model FEM meshes — they bake through the same streaming path
    # (mesh + beam-solids, no result fields) so FE-mesh viewing has a single pipeline. They
    # remain legacy-convertible (ifc/xml/step/…) on the /convert page, like .sif.
    {".rmed", ".sif", ".sin", ".inp", ".fem", ".med"}
)

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


def reconvert_key_for(source_key: str, target_format: str = "glb") -> str:
    """Output key for a user-triggered *re-conversion* (gallery "Re-convert" button).

    Lives in a SEPARATE ``_reconvert/`` namespace from the ``_derived/`` convert cache, so a
    re-convert never overwrites the audit-run product in a corpus scope — the derived cache
    stays exactly as the audit produced it. One blob per (source, format): re-converting the
    same file again overwrites its own ``_reconvert/`` blob (throwaway, not accumulating).
    """
    fmt = target_format.lstrip(".").lower()
    if fmt not in TARGET_FORMATS:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    return f"_reconvert/{source_key.strip('/')}.{fmt}"


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


# Suffix for the SIF byte-offset index sidecar (see
# ada.fem.formats.sesam.results.sif_index). Built once per SIF deck; lets the
# worker range-fetch only one result step's bytes instead of the whole file.
_SIF_INDEX_SUFFIX = ".sifindex.json"


def sif_index_key_for(source_key: str) -> str:
    src = source_key.strip("/")
    return f"_derived/{src}{_SIF_INDEX_SUFFIX}"


def is_derived_key(key: str) -> bool:
    return key.lstrip("/").startswith("_derived/")


# Internal namespaces hidden from file listings / the storage explorer. ``_derived/`` is the
# admin-managed convert-output cache; ``_overlays/`` is the auto-disposed utility overlay bucket
# (merge-preview / diff GLBs). Neither is a user file. Use this for DISPLAY filtering; keep
# is_derived_key for derived-product logic (rename/cleanup/grouping), which must not treat an
# ephemeral overlay as a source's derived product.
def is_hidden_key(key: str) -> bool:
    k = key.lstrip("/")
    return k.startswith("_derived/") or k.startswith("_overlays/") or k.startswith("_reconvert/")


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


# Bumped when the pickled Assembly schema changes incompatibly, so stale cache entries from
# older code are ignored rather than mis-loaded.
_ASM_CACHE_VERSION = "1"


def _asm_cache_path(src_path: pathlib.Path, ext: str) -> pathlib.Path | None:
    """Local pickle-cache path for a parsed source, keyed by content hash — or None when the
    cache is disabled (ADA_ASSEMBLY_CACHE unset/falsy). Same content → same key, so every export
    target of one audit source reuses the first parse instead of re-reading the file."""
    import os

    if (os.environ.get("ADA_ASSEMBLY_CACHE") or "").strip().lower() in _FALSE | {""}:
        return None
    import hashlib

    h = hashlib.sha1()  # noqa: S324 - cache key, not security
    h.update(_ASM_CACHE_VERSION.encode())
    h.update(ext.encode())
    with open(src_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    cache_dir = pathlib.Path(os.environ.get("ADA_ASSEMBLY_CACHE_DIR") or (tempfile.gettempdir() + "/ada_asm_cache"))
    return cache_dir / f"{h.hexdigest()}.pkl"


def _load_with_ada(src_path: pathlib.Path, ext: str):
    import ada
    from ada.config import logger

    # Reuse a previously-parsed Assembly (read-once-export-many): the audit converts one source to
    # several targets, and re-reading/re-parsing the same file per target is pure overhead. Each
    # hit returns a fresh deep copy via from_pickle, so per-target mutation never cross-contaminates.
    cache_path = _asm_cache_path(src_path, ext)
    if cache_path is not None and cache_path.exists():
        try:
            return ada.from_pickle(cache_path)
        except Exception as exc:  # noqa: BLE001 - corrupt / version-mismatched cache → re-parse
            logger.debug("assembly cache miss (unreadable %s): %s", cache_path, exc)

    def _read():
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

    model = _read()
    if cache_path is not None:
        try:
            model.to_pickle(cache_path)
        except Exception as exc:  # noqa: BLE001 - caching is best-effort; never fail the conversion
            logger.debug("assembly cache store failed (%s): %s", cache_path, exc)
    return model


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
    # The IFC streaming writer fuses shell elements into plates one at a time
    # (Part.iter_objects_from_fem), so leave plates unbuilt — build beams only —
    # and let the writer stream them, keeping peak memory bounded. The Genie-XML
    # streaming writer does the same via the object-free vectorized face source
    # (mesh_faces). Curved-plate reconstruction (advanced faces) isn't handled by
    # either text emitter, so it still takes the full build.
    skip_plates = False
    if target_format == "ifc" and not recon:
        import os

        skip_plates = os.environ.get("ADA_IFC_STREAMING", "").strip().lower() not in _FALSE
    elif _gxml_face_streaming(source_ext, target_format, recon):
        skip_plates = True
    model.create_objects_from_fem(merge=merge, reconstruct_surfaces=recon, skip_plates=skip_plates)


def _gxml_face_streaming(source_ext: str, target_format: str, reconstruct_surfaces: bool) -> bool:
    """Whether FEM→Genie-XML streams plates from the object-free vectorized face
    source instead of materialising Plate objects.

    Gated by ``ADA_GXML_STREAMING`` (default on; only an explicit falsy value
    reverts to the full object build + DOM writer). Not used for curved-plate
    reconstruction (the parametric face emitter can't express advanced faces).
    Shared by ``_apply_fem_to_objects`` (skip the plate build) and
    ``_export_with_ada`` (use the streaming writer) so they stay consistent."""
    if target_format != "xml":
        return False
    if source_ext.lower() not in _FEM_SOURCE_EXTS:
        return False
    if reconstruct_surfaces:
        return False
    import os

    return os.environ.get("ADA_GXML_STREAMING", "").strip().lower() not in _FALSE


def _export_with_ada(
    model,
    target_format: str,
    out_path: pathlib.Path,
    on_progress: ProgressFn,
    *,
    merge_meshes: bool | None = None,
    source_ext: str | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
) -> bytes | pathlib.Path:
    """Run the matching ada exporter; return the output as bytes or a path.

    GLB tessellates into a ``BytesIO`` and is returned as bytes. The
    disk-writing targets (IFC, Genie XML) return ``out_path`` itself —
    ownership transfers to the caller, which streams the file straight to
    object storage rather than reading it back into a RAM buffer (the
    streaming writers already keep peak memory bounded; reading the whole
    result back would undo that).

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
        # FEM beam (line) elements render as line geometry by default; the solid (swept-
        # profile) representation is delivered as a separate beam_solids sidecar the viewer
        # lazy-loads when the "show beams as solid" toggle is on (mirrors the FEA-results path).
        #
        # Tessellation-engine selection (glb_tess_engine row option): to_gltf's BatchTessellator
        # reads ADA_STREAM_TESS_PIPELINE, so set it from the per-job engine for the duration of
        # the call and restore after. None/occ-builtin → force the OCC default (clear any ambient
        # override); libtess2/adacpp-* → the matching OCC-free stream pipeline.
        import os as _os

        _stream = _glb_engine_stream_value(glb_tess_engine)
        _prev_stream = _os.environ.get("ADA_STREAM_TESS_PIPELINE")
        if _stream:
            _os.environ["ADA_STREAM_TESS_PIPELINE"] = _stream
        else:
            _os.environ.pop("ADA_STREAM_TESS_PIPELINE", None)
        # Strict coverage (only meaningful alongside a non-OCC engine): make a stream→OCC
        # fallback a hard error. Set the flag for the duration of to_gltf, restored below.
        _prev_strict = _os.environ.get("ADA_STREAM_TESS_STRICT")
        if strict_tess and _stream:
            _os.environ["ADA_STREAM_TESS_STRICT"] = "1"
        else:
            _os.environ.pop("ADA_STREAM_TESS_STRICT", None)
        try:
            model.to_gltf(buf, merge_meshes=merge_meshes)
        except ValueError as exc:
            from ada.cadit.wasm_convert import _is_empty_scene

            if not _is_empty_scene(exc):
                raise
            # The source parsed to zero renderable geometry (e.g. a SAT file holding only a
            # wire/construction body, or an IFC with metadata only). Emit a valid seeded GLB so
            # the conversion succeeds with an empty scene instead of erroring — same trick the
            # step-stream and scene-based glb paths already use via _seed_empty_scene.
            import trimesh

            scene = trimesh.Scene()
            _seed_empty_scene(scene)
            buf = io.BytesIO()
            scene.export(buf, file_type="glb")
        finally:
            if _prev_stream is None:
                _os.environ.pop("ADA_STREAM_TESS_PIPELINE", None)
            else:
                _os.environ["ADA_STREAM_TESS_PIPELINE"] = _prev_stream
            if _prev_strict is None:
                _os.environ.pop("ADA_STREAM_TESS_STRICT", None)
            else:
                _os.environ["ADA_STREAM_TESS_STRICT"] = _prev_strict
        on_progress("ready", 1.0)
        return buf.getvalue()
    if target_format == "ifc":
        on_progress("writing-ifc", 0.55)
        # Memory-bounded writer is the default: it hand-authors Plate solids as
        # SPF text instead of holding the whole ifcopenshell.file, ~halving peak
        # RSS on large FEM→IFC and clearing the worker OOM cap. The admin "Stream
        # IFC write" toggle / per-job ``ifc_streaming`` sets ADA_IFC_STREAMING;
        # only an explicit falsy value reverts to the in-memory writer.
        import os

        streaming = os.environ.get("ADA_IFC_STREAMING", "").strip().lower() not in _FALSE
        # On the streaming path, fold FEM shells via the shared object-free face engine
        # instead of emitting one plate per element (matches the Genie-XML and STEP
        # streamers). Default is analytic auto-detect ("cylinder": tubular members ->
        # IfcCylindricalSurface, flat panels -> merged IfcPlane faces), same as FEM->STEP;
        # a string merge_fem_objects overrides verbatim, False opts out to 1:1.
        ms = None
        recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
        if streaming and source_ext is not None and source_ext.lower() in _FEM_SOURCE_EXTS and not recon:
            if isinstance(merge_fem_objects, str):
                ms = merge_fem_objects
            elif merge_fem_objects is False:
                ms = "none"
            else:
                ms = "cylinder"  # analytic auto-detect
        model.to_ifc(destination=str(out_path), streaming=streaming, merge_strategy=ms)
    elif target_format == "xml":
        on_progress("writing-xml", 0.55)
        recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
        if source_ext is not None and _gxml_face_streaming(source_ext, target_format, recon):
            # Object-free path: plates stream from the vectorized FEM-shell face
            # source (no Plate objects, no DOM). merge_fem_objects -> strategy.
            merge = True if merge_fem_objects is None else bool(merge_fem_objects)
            model.to_genie_xml(
                destination_xml=str(out_path),
                streaming=True,
                merge_strategy=("coplanar" if merge else "none"),
            )
        else:
            model.to_genie_xml(destination_xml=str(out_path))
    else:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    on_progress("ready", 1.0)
    return out_path


# A STEP file on disk above this size loads into one OCC compound that OOM-kills
# the worker (the 778 MB CAD assembly is fatal). Above it, STEP→GLB auto-routes
# through the memory-bounded streaming converter; below, the OCC path keeps full
# fidelity. Admin-tunable via the conversion settings (env rail below).
_STEP_STREAM_DEFAULT_THRESHOLD_MB = 200.0

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _should_stream_step(src_path: pathlib.Path, step_streamer: bool | None) -> bool:
    """Decide whether STEP→GLB goes through the streaming converter.

    Precedence: explicit per-job choice (``step_streamer`` kwarg or the
    ``ADA_STEP_STREAMER`` env the worker sets from the job option) wins; otherwise
    auto-select by file size, gated by the global ``ADA_STEP_STREAMER_AUTO`` toggle
    and ``ADA_STEP_STREAMER_THRESHOLD_MB`` (both admin settings)."""
    import os

    if step_streamer is None:
        raw = os.environ.get("ADA_STEP_STREAMER", "").strip().lower()
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
    else:
        return bool(step_streamer)

    if os.environ.get("ADA_STEP_STREAMER_AUTO", "").strip().lower() in _FALSE:
        return False  # auto-streaming disabled globally
    try:
        threshold_mb = float(os.environ.get("ADA_STEP_STREAMER_THRESHOLD_MB", "") or _STEP_STREAM_DEFAULT_THRESHOLD_MB)
    except ValueError:
        threshold_mb = _STEP_STREAM_DEFAULT_THRESHOLD_MB
    try:
        return src_path.stat().st_size > threshold_mb * 1024 * 1024
    except OSError:
        return False


# STEP→GLB engines. ``adacpp-native`` (the fully in-process C++ reader+tessellate+write, validated
# 1:1 with the Python path) is the default; it falls back to ``libtess2`` (adacpp's OCC-free boundary
# CDT, step2glb-parity geometry incl. curved surfaces the OCC stream reader drops) and then the
# ``occ-builtin`` OCC streaming reader. ``adacpp-{occ,cgal,hybrid}`` route through adacpp's linked
# OCCT / ifcopenshell-taxonomy kernels (extra options). (The external ``step2glb`` binary engine was
# removed — libtess2 reaches the same geometry in-process, so the unprovisioned binary path is gone.)
_STEP_GLB_PIPELINE_LIBTESS2 = "libtess2"
_STEP_GLB_PIPELINE_OCC = "occ-builtin"
_STEP_GLB_PIPELINE_ADACPP_OCC = "adacpp-occ"
_STEP_GLB_PIPELINE_ADACPP_CGAL = "adacpp-cgal"
_STEP_GLB_PIPELINE_ADACPP_HYBRID = "adacpp-hybrid"
# Fully-native: adacpp does the whole STEP->GLB in-process (C++ reader + thread pool + GLB writer),
# replacing the Python reader + multiprocess pool. Fastest + lowest memory, and now byte-faithful to
# the Python path — geometry, product names, per-instance picking, and the full assembly tree are
# validated 1:1 on the large reference assembly (see native_step_to_glb / validate_native_vs_python.py). This is the
# default; it degrades gracefully to libtess2 if adacpp's native entry point is missing or the
# conversion raises.
_STEP_GLB_PIPELINE_ADACPP_NATIVE = "adacpp-native"


# The four historic (adacpp track pipeline <-> `step_glb_pipeline` token) identities. Pinned in
# BOTH directions from one dict so the two mappings cannot drift; everything else is structural
# (``adacpp:X`` <-> ``adacpp-X``), which is what lets a track added later in adacpp resolve
# end-to-end without an edit here.
_HISTORIC_TRACK_PIPELINE = {
    "libtess2": _STEP_GLB_PIPELINE_LIBTESS2,
    "occ": _STEP_GLB_PIPELINE_ADACPP_OCC,
    "cgal": _STEP_GLB_PIPELINE_ADACPP_CGAL,
    "hybrid": _STEP_GLB_PIPELINE_ADACPP_HYBRID,
}
_HISTORIC_PIPELINE_TRACK = {v: f"adacpp:{k}" for k, v in _HISTORIC_TRACK_PIPELINE.items()}


def _pipeline_to_track_name(pipe: str) -> str | None:
    """`step_glb_pipeline` token -> adacpp track name, or None for the paths that aren't a track
    (occ-builtin, adacpp-native).

    VOCABULARY, not capability: this says what a token *means*, never whether this process can run
    it. Availability is checked one layer up, in :func:`_cad_config_for_pipeline`, which warns and
    degrades. Keeping the two apart is what makes the answer identical in the API, the worker and
    the test envs — see :func:`_tess_token_to_pipeline` for what the conflation cost.
    """
    if pipe in _HISTORIC_PIPELINE_TRACK:
        return _HISTORIC_PIPELINE_TRACK[pipe]
    if pipe.startswith("adacpp-") and pipe != _STEP_GLB_PIPELINE_ADACPP_NATIVE:
        return f"adacpp:{pipe[len('adacpp-'):]}"
    return None  # occ-builtin / adacpp-native / unknown → not a stream CadConfig


def _adacpp_stream_pipelines() -> tuple[str, ...]:
    """Every `step_glb_pipeline` token backed by a DISCOVERED adacpp track (adacpp-native excluded:
    it is a whole-file code path, not a tessellator)."""
    from ada.cad.registry import CadBackendName, available_tess_tracks

    out = []
    for t in available_tess_tracks():
        if t.backend is not CadBackendName.ADACPP:
            continue
        tok = _tess_token_to_pipeline(t.name)
        if tok not in out:
            out.append(tok)
    return tuple(out)


_STEP_GLB_PIPELINES_STATIC = (
    _STEP_GLB_PIPELINE_LIBTESS2,
    _STEP_GLB_PIPELINE_OCC,
    _STEP_GLB_PIPELINE_ADACPP_OCC,
    _STEP_GLB_PIPELINE_ADACPP_CGAL,
    _STEP_GLB_PIPELINE_ADACPP_HYBRID,
    _STEP_GLB_PIPELINE_ADACPP_NATIVE,
)


def _step_glb_pipelines() -> tuple[str, ...]:
    """The accepted `step_glb_pipeline` vocabulary: adapy's own tokens + every discovered adacpp
    track. Derived, so a new track is accepted without touching this module."""
    out = list(_STEP_GLB_PIPELINES_STATIC)
    for tok in _adacpp_stream_pipelines():
        if tok not in out:
            out.append(tok)
    return tuple(out)


_STEP_GLB_PIPELINE_DEFAULT = _STEP_GLB_PIPELINE_ADACPP_NATIVE
# Where the native path degrades to when adacpp is absent or a conversion raises. Kept separate from
# the default so the native branch's fallback is never circular.
_STEP_GLB_PIPELINE_FALLBACK = _STEP_GLB_PIPELINE_LIBTESS2


def available_step_glb_pipelines() -> tuple[str, ...]:
    """The STEP→GLB engines THIS process can actually run — for per-worker capability advertisement.

    A worker pool without adacpp won't advertise the adacpp engines; one without OCC won't advertise
    occ-builtin. The API unions these across pools for the engine list, and routes a job requesting an
    engine to a pool that advertises it. Detection is conservative: if nothing is detected (which
    shouldn't happen in a real worker) it returns the full set rather than an empty one.
    """
    import importlib.util

    def _have(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False

    runnable: set[str] = set()
    if _have("adacpp"):
        # adacpp-native is gated with the rest of the adacpp engines (find_spec presence) rather than an
        # import-based native_adacpp_available() check: under the deployed adacpp-overlay, import caching
        # can resolve a base adacpp (without the native entrypoint) before the overlay path is active, so
        # the import check spuriously reports False at worker-startup advert time. find_spec is resolved
        # at call time against sys.path and isn't poisoned by an earlier import; the worker's runtime
        # fallback (native → libtess2 → occ-builtin) still covers a pool that turns out not to run it.
        # Discovered, not listed: adacpp reports which tracks IT compiled, so a build without the
        # taxonomy kernels (or with a new one) advertises the truth rather than this module's guess.
        runnable.update(_adacpp_stream_pipelines())
        runnable.add(_STEP_GLB_PIPELINE_ADACPP_NATIVE)  # a whole-file code path, not a track
    if _have("OCP") or _have("OCC"):
        runnable.add(_STEP_GLB_PIPELINE_OCC)
    avail = tuple(p for p in _step_glb_pipelines() if p in runnable)
    return avail or _step_glb_pipelines()


def available_tess_tokens() -> frozenset[str]:
    """The `tessellator` tokens THIS process can actually run — the serializer×tessellator analogue
    of :func:`available_step_glb_pipelines`, for per-worker capability advertisement.

    Every discovered track name (``occ``, ``adacpp:libtess2``, ``adacpp:cdt``, ...) plus the ``cpp``
    serializer's pinned ``native`` token, which is adacpp's in-process writer and so rides on adacpp
    being importable.

    Client tokens (``wasm-native`` / ``pyodide``) are deliberately ABSENT: they run in the browser,
    so no server-side probe can answer for them and they must never be gated on what a worker has
    installed — see ``worker._gate_advertised_engines``.
    """
    from ada.cad.registry import (
        CadBackendName,
        available_tess_tracks,
        backend_available,
    )

    toks = {t.name for t in available_tess_tracks()}
    if backend_available(CadBackendName.ADACPP):
        toks.add("native")
    return frozenset(toks)


# Non-STEP →GLB engine toggle (xml / ifc / sat / fem / obj / stl → glb via to_gltf's
# BatchTessellator). Reuses the STEP option's names so the admin panel reads consistently, but maps
# to the BatchTessellator stream selector (ADA_STREAM_TESS_PIPELINE); "occ-builtin" = the default
# OCC BatchTessellator (no stream override).
_GLB_TESS_ENGINES_STATIC = (
    _STEP_GLB_PIPELINE_OCC,  # "occ-builtin"
    _STEP_GLB_PIPELINE_LIBTESS2,
    _STEP_GLB_PIPELINE_ADACPP_OCC,
    _STEP_GLB_PIPELINE_ADACPP_CGAL,
    _STEP_GLB_PIPELINE_ADACPP_HYBRID,
)


def _glb_tess_engines() -> tuple[str, ...]:
    """The `glb_tess_engine` vocabulary: adapy's own tokens + every discovered adacpp track.

    ``adacpp-native`` is excluded — it is a whole-file STEP code path, not a BatchTessellator
    kernel. Derived rather than listed for the same reason as :func:`_step_glb_pipelines`: the tuple
    this replaced could not grow a track added later in adacpp.

    Shaped like _step_glb_pipelines() — a static base that discovery only ADDS to — so the answer
    is the same in every process. Returning just what adacpp reports would make the VOCABULARY
    env-dependent (an adacpp-less API would stop knowing the word "adacpp-cgal" rather than knowing
    it and not offering it), which is the conflation _tess_token_to_pipeline exists to avoid.
    Capability is applied by ``worker._gate_advertised_engines``.
    """
    out = list(_GLB_TESS_ENGINES_STATIC)
    for tok in _adacpp_stream_pipelines():
        if tok not in out:
            out.append(tok)
    return tuple(out)


# Advertised default for the non-STEP →GLB engine option. Kept in lockstep with the RUNTIME default
# (`_default_glb_tess_engine`, which returns libtess2 whenever adacpp is importable) so the SPA/audit
# UI reflects the path actually taken. libtess2 gracefully degrades to OCC on an adacpp-less pool.
_GLB_TESS_ENGINE_DEFAULT = _STEP_GLB_PIPELINE_LIBTESS2


def _glb_engine_to_stream(engine: str) -> str | None:
    """`glb_tess_engine` token -> the ``ADA_STREAM_TESS_PIPELINE`` value, or None for the OCC
    BatchTessellator default (``occ-builtin`` / unknown).

    Derived from the engine token, not a table: the table this replaced listed exactly the four
    engines that existed when it was written, so a track discovered later (``adacpp-cdt``) missed
    it, resolved to None, and SILENTLY ran the OCC BatchTessellator — the user picked a kernel and
    got a different one, with nothing logged.
    """
    track = _pipeline_to_track_name(engine)
    return None if track is None else track.split(":", 1)[1]


# ---------------------------------------------------------------------------
# Serializer × Tessellator matrix — SINGLE SOURCE OF TRUTH for the SPA's
# reconvert dropdowns (gallery tools + ConversionRow). Two user-facing axes:
#
#   * serializer  — WHICH code path drives the →GLB conversion:
#         cpp      pure-C++ adacpp  (STEP: adacpp-native; IFC: native_ifc_to_glb)
#         python   Python-orchestrated (ada import + BatchTessellator/stream reader)
#         wasm     client-side, runs entirely in the browser (no server round-trip)
#   * tessellator — WHICH kernel meshes the geometry. It is DEPENDENT on the
#                   serializer (enum_by): the server `python` serializer exposes
#                   the full kernel choice; `cpp` pins libtess2; the client `wasm`
#                   serializer exposes its two in-browser engines (native embind
#                   module / Pyodide wheels).
#
# The frontend renders both dropdowns purely from the option schema advertised
# on the conversion matrix (``labels`` + ``enum_by``) and sends back opaque
# ``{serializer, tessellator}`` tokens — it hardcodes NONE of this vocabulary.
# The backend resolver (:func:`_apply_glb_serializer`) folds those tokens into
# the existing engine knobs (``step_glb_pipeline`` / ``glb_tess_engine``) plus
# the native-vs-python routing. The client serializer carries ``runtime="client"``
# so the SPA routes it to the in-browser pipeline instead of the worker; it
# never reaches this resolver.
_GLB_SERIALIZER_CPP = "cpp"
_GLB_SERIALIZER_PYTHON = "python"
_GLB_SERIALIZER_WASM = "wasm"

# Human labels for every serializer token (superset; each row advertises only
# the subset that applies to its source family).
_GLB_SERIALIZER_LABELS = {
    _GLB_SERIALIZER_CPP: "C++ (adacpp native)",
    _GLB_SERIALIZER_PYTHON: "Python (ada)",
    _GLB_SERIALIZER_WASM: "WASM (browser)",
}
# Serializers that execute in the browser — the SPA routes these to its
# in-browser pipeline and gates them on client capability (wasmSupport).
_GLB_CLIENT_SERIALIZERS = frozenset({_GLB_SERIALIZER_WASM})

# The two in-browser engines the `wasm` serializer offers, most-preferred first. adapy's OWN
# vocabulary, unlike the python serializer's (discovered from adacpp): these name browser pipelines
# — the adacpp embind module and the Pyodide wheel stack — that no adacpp build reports and no
# worker runs. The SPA matches "wasm-native" to pick the embind pipeline, so this tuple and
# services/conversion/index.ts share one contract; test_wasm_engine_tokens_are_the_spa_contract
# pins it.
_WASM_ENGINE_NATIVE = "wasm-native"
_WASM_ENGINE_PYODIDE = "pyodide"
_WASM_GLB_ENGINES = (_WASM_ENGINE_NATIVE, _WASM_ENGINE_PYODIDE)

# Labels adapy OWNS: its own serializer-pinned token and the two in-browser engines, none of which
# adacpp knows about. Every adacpp track's label + description comes from the track itself
# (_glb_tess_label / _glb_tess_description), so a new track arrives fully described.
_GLB_TESS_NATIVE_PINNED = "native"

_GLB_TESS_OWN_LABELS = {
    # The cpp serializer's fallback token, used only against an adacpp whose native binding can't
    # take a track (< 0.16). Newer builds advertise the discovered adacpp:* tracks instead, so this
    # stays resolvable for stored configs rather than naming a choice we still offer.
    _GLB_TESS_NATIVE_PINNED: "Native (libtess2)",
    # client-side (wasm serializer) engines:
    "wasm-native": "Native (WASM module)",
    "pyodide": "Pyodide (wasm wheels)",
    # legacy tokens, still resolvable for stored configs / older SPA builds:
    "pyocc": "PythonOCC (OCC)",
    "adacpp-occ": "adacpp OCC",
    "cgal": "adacpp CGAL",
    "ifc-hybrid": "ifcOpenShell hybrid",
}


def _glb_tess_label(tok: str) -> str:
    """Human label for a tessellator token. Discovered tracks describe themselves."""
    from ada.cad.registry import tess_track_by_name

    track = tess_track_by_name(_LEGACY_TESS_ALIASES.get(tok, tok))
    if track is not None:
        return track.label
    return _GLB_TESS_OWN_LABELS.get(tok, tok)


def _glb_tess_description(tok: str) -> str:
    """One-line tooltip for a tessellator token, from the track's own declaration ('' if it has
    none). This is why a track can explain its own cost/benefit in the UI without adapy knowing
    anything about it."""
    from ada.cad.registry import tess_track_by_name

    track = tess_track_by_name(_LEGACY_TESS_ALIASES.get(tok, tok))
    return track.description if track is not None else ""


# LEGACY tessellator tokens -> track name. The python serializer's vocabulary used to be a
# hand-written list; it is now DISCOVERED (see _python_tess_tokens), which is the only reason
# `adacpp:cdt` can reach the UI at all — a hand-written list cannot grow a track added later in
# adacpp. These aliases keep stored configs, the admin panel and older SPA builds resolving.
#
# `pyocc` and `occ` BOTH land on adapy's own OCC track, because they always were the same engine:
# the pre-discovery table mapped both to `occ-builtin` and merely labelled them differently
# ("PythonOCC (OCC)" vs "OCC streaming reader"), so the STEP dropdown listed one engine twice.
_LEGACY_TESS_ALIASES = {
    "native": "adacpp:libtess2",
    "pyocc": "occ",
    "occ": "occ",
    "adacpp-occ": "adacpp:occ",
    "cgal": "adacpp:cgal",
    "ifc-hybrid": "adacpp:hybrid",
}


def _python_tess_tokens() -> list[str]:
    """The `python` serializer's tessellator tokens — DISCOVERED from adacpp, not enumerated here.

    Adding a track in adacpp makes it appear in this dropdown with no change in adapy. The order puts
    the declared default first (the dropdown's default is enum_by[...][0]).

    Takes no source family ON PURPOSE. The pre-discovery table split step/generic, but only to list
    the duplicate `occ` alias on STEP (see _LEGACY_TESS_ALIASES) — every real kernel was offered to
    both. Each track reaches both families: STEP via `step_glb_pipeline`, everything else via
    `glb_tess_engine` -> ADA_STREAM_TESS_PIPELINE, and adapy's own OCC track is the BatchTessellator
    default on generic. So there is no family split left to honour, and a `fam` argument here would
    be a lie about a distinction that no longer exists.
    """
    from ada.cad.registry import available_tess_tracks

    tracks = available_tess_tracks()
    ranked = sorted(tracks, key=lambda t: (not t.is_default, t.backend.value != "adacpp", t.name))
    return [t.name for t in ranked]


def _cpp_tess_tokens() -> list[str]:
    """The `cpp` serializer's tessellator tokens — the same adacpp tracks the `python` serializer
    discovers, because both dispatch to the same kernel; the native path just reaches it through
    the C++ reader instead of the NGEOM wire.

    Two gates, both asked rather than assumed. The binding must accept a track at all (adacpp < 0.16
    ignores ``pipeline`` and runs libtess2), and the track must be one adacpp declares NEUTRAL — the
    taxonomy kernels need ifcopenshell geometry the C++ STEP reader never builds, and on this path
    they mesh as if nothing were selected instead of erroring. Gating the ADVERTISEMENT on the same
    facts the conversion checks is the point: an offer we can't honour reports a track we never ran.
    """
    # From the registry, NOT ada.cadit: this runs at module import to build the published
    # vocabulary, and the slim viewer image ships ada/cad but no ada/cadit — importing the latter
    # here crashlooped the API on startup. The registry answers False with no adacpp, which is the
    # right answer for the API: it runs no conversions, and the pools that do advertise their own.
    from ada.cad.registry import (
        CadBackendName,
        native_track_selection_available,
        tess_track_by_name,
    )

    if not native_track_selection_available():
        return [_GLB_TESS_NATIVE_PINNED]
    tracks = []
    for tok in _python_tess_tokens():
        t = tess_track_by_name(tok)
        if t is not None and t.backend is CadBackendName.ADACPP and t.neutral:
            tracks.append(tok)
    return tracks or [_GLB_TESS_NATIVE_PINNED]


def _glb_serializer_tess(serializer: str) -> list[str] | None:
    """Tessellator tokens for a serializer, or None if it isn't offered.

    `wasm` runs in-browser engines adacpp doesn't know about, so it stays declared here — it is
    adapy's own. `python` and `cpp` both dispatch to adacpp and so both DISCOVER their tracks;
    `cpp` degrades to its historic pinned token against an adacpp whose binding can't select one.
    """
    if serializer == _GLB_SERIALIZER_CPP:
        return _cpp_tess_tokens()
    if serializer == _GLB_SERIALIZER_WASM:
        return list(_WASM_GLB_ENGINES)
    if serializer == _GLB_SERIALIZER_PYTHON:
        return _python_tess_tokens()
    return None


# Serializer ordering per family (default is the first entry). cpp is the
# default server path for both; the client serializer comes last.
_GLB_SERIALIZER_ORDER = (
    _GLB_SERIALIZER_CPP,
    _GLB_SERIALIZER_PYTHON,
    _GLB_SERIALIZER_WASM,
)


# tessellator token → effective engine knob, reusing the existing pipeline
# identities so there is exactly one definition of each engine.
def _tess_token_to_pipeline(tok: str) -> str:
    """Tessellator token -> the `step_glb_pipeline` knob. Resolves legacy tokens and any track name,
    including ones that postdate this module (e.g. ``adacpp:cdt`` -> ``adacpp-cdt``).

    VOCABULARY, not capability — deliberately: this used to resolve the token by LOOKING THE TRACK
    UP in ``available_tess_tracks()`` and falling back to libtess2 when it wasn't found, which made
    the same stored token mean different engines in different processes (``cgal`` -> ``adacpp-cgal``
    where adacpp is importable, ``libtess2`` where it isn't). Worse, the fallback was itself an
    adacpp engine, so it could not be what an adacpp-less pool meant. Resolution is now purely
    structural and identical everywhere; whether the resolved engine can actually RUN is decided by
    ``available_step_glb_pipelines`` / ``_gate_advertised_engines`` / ``_cad_config_for_pipeline``.
    """
    name = _LEGACY_TESS_ALIASES.get(tok, tok)
    if name == "occ":
        return _STEP_GLB_PIPELINE_OCC  # adapy's own BRepMesh (no stream override)
    if not name.startswith("adacpp:"):
        return _STEP_GLB_PIPELINE_LIBTESS2  # unknown token → the OCC-free default
    return _HISTORIC_TRACK_PIPELINE.get(name.split(":", 1)[1], f"adacpp-{name.split(':', 1)[1]}")


def _tess_token_to_glb_engine(tok: str) -> str:
    """Tessellator token -> the `glb_tess_engine` knob (the non-STEP BatchTessellator selector).
    Same structural resolution as _tess_token_to_pipeline."""
    return _tess_token_to_pipeline(tok)


def _native_track_for_engine(glb_tess_engine: str | None) -> str | None:
    """The adacpp `pipeline` name for the native STEP->GLB path, or None to leave adacpp's default.

    The cpp serializer parks its chosen track on the tess-engine axis (see _apply_glb_serializer),
    so this reverses that: 'adacpp-cdt' -> 'cdt', the token adacpp's own binding takes. VOCABULARY,
    not capability — native_step_to_glb re-checks its binding and refuses rather than substituting.
    Engines that aren't an adacpp track (occ-builtin, adacpp-native itself) yield None.
    """
    if not glb_tess_engine:
        return None
    track_name = _pipeline_to_track_name(glb_tess_engine)
    if not track_name:
        return None
    return track_name.split(":", 1)[1] or None


def _glb_source_family(source_ext: str) -> str:
    """'step' for STEP/STP sources, else 'generic' — selects the serializer
    tessellator vocabulary + engine-knob axis."""
    return "step" if source_ext.lower().lstrip(".") in ("step", "stp") else "generic"


def _glb_serializer_options(source_ext: str) -> list[dict]:
    """Build the ``serializer`` + dependent ``tessellator`` enum options for a
    →GLB row. Single source: vocabulary, labels, defaults and the
    serializer→tessellator dependency all come from the module spec above, so
    the frontend can render the two dependent dropdowns without hardcoding any
    of it. ``enum_by`` maps each serializer value to its allowed tessellator
    tokens; ``runtime='client'`` flags the browser-side serializers.

    ``source_ext`` no longer selects the tessellator vocabulary (every kernel reaches every source
    family — see :func:`_python_tess_tokens`); it is kept because it names the row this schema is
    attached to and because the resolver still splits on family to pick the ENGINE KNOB.
    """
    serializers = [s for s in _GLB_SERIALIZER_ORDER if _glb_serializer_tess(s)]
    enum_by = {s: list(_glb_serializer_tess(s)) for s in serializers}
    # Union of tessellator tokens across serializers, ordered by first appearance.
    tess_tokens: list[str] = []
    for s in serializers:
        for tok in enum_by[s]:
            if tok not in tess_tokens:
                tess_tokens.append(tok)
    default_ser = serializers[0]
    return [
        {
            "name": "serializer",
            "type": "enum",
            "title": "Serializer",
            "default": default_ser,
            "enum": serializers,
            "labels": {s: _GLB_SERIALIZER_LABELS[s] for s in serializers},
            "runtime": {s: ("client" if s in _GLB_CLIENT_SERIALIZERS else "server") for s in serializers},
            "description": (
                "Conversion code path for →GLB. 'cpp' = pure-C++ adacpp (fastest, lowest memory); "
                "'python' = Python-orchestrated (lets you pick the tessellation kernel below); "
                "'wasm' / 'pyodide' run entirely in your browser (no server round-trip)."
            ),
        },
        {
            "name": "tessellator",
            "type": "enum",
            # Mesh target → this axis meshes the geometry ("Tessellator"). (For B-rep targets the
            # analogous axis is titled "Writer" — same mechanism, set on those rows.)
            "title": "Tessellator",
            "default": enum_by[default_ser][0],
            "enum": tess_tokens,
            "labels": {t: _glb_tess_label(t) for t in tess_tokens},
            # Per-token tooltips, sourced from each track's own declaration. Empty for adapy's own
            # tokens, which the frontend renders without a tooltip.
            "descriptions": {t: _glb_tess_description(t) for t in tess_tokens if _glb_tess_description(t)},
            # Dependent dropdown: the valid tessellators for each serializer. The
            # frontend repopulates + reselects when the serializer changes.
            "enum_by": enum_by,
            "depends_on": "serializer",
            "description": (
                "Tessellation kernel. Only the 'python' serializer exposes a choice; the other "
                "serializers pin their own kernel."
            ),
        },
    ]


# Option-dict keys that are per-value MAPPINGS rather than scalars: each key is an enum value (or,
# for enum_by, a value of the option it depends_on). Two pools advertising the same option each
# carry entries only for the values THEY advertise, so these merge key-by-key.
_OPTION_MAP_KEYS = ("labels", "descriptions", "runtime")


def merge_option_into(cur: dict, incoming: dict) -> None:
    """Union one worker's advertisement of an option into the accumulated one, in place.

    Lives here, next to the code that BUILDS these dicts (_glb_serializer_options), because how two
    advertisements of a schema combine is a fact about the schema.

    First-writer-wins on everything but ``enum`` — which is what the API's merge used to be —
    silently loses data as soon as two pools differ. A thin pool advertising
    ``enum_by={'python': ['occ']}`` pinned that for the whole cluster, while the adacpp pool's
    tracks, already unioned into ``enum``, reached the dropdown with no serializer offering them:
    rendered and unselectable. The per-value maps fail the same way — whichever pool registered
    first decides, and tokens only the other pool knows arrive unlabelled.

    So: union ``enum`` and each ``enum_by`` list, fill in the per-value maps, and leave scalars
    (``default``/``title``/``description``/``type``/``depends_on``) to the first writer — those
    describe the option itself rather than its values, and every pool derives them from this module.
    """
    import copy

    if isinstance(incoming.get("enum"), list) and isinstance(cur.get("enum"), list):
        cur["enum"] = cur["enum"] + [v for v in incoming["enum"] if v not in cur["enum"]]
    if isinstance(incoming.get("enum_by"), dict):
        by = cur.get("enum_by")
        if not isinstance(by, dict):
            cur["enum_by"] = copy.deepcopy(incoming["enum_by"])
        else:
            for key, vals in incoming["enum_by"].items():
                if not isinstance(vals, list):
                    continue
                have = by.get(key)
                by[key] = have + [v for v in vals if v not in have] if isinstance(have, list) else list(vals)
    for key in _OPTION_MAP_KEYS:
        if not isinstance(incoming.get(key), dict):
            continue
        have = cur.get(key)
        if isinstance(have, dict):
            # setdefault, not update: a pool contributes descriptions for the values it knows and
            # can never blank out another pool's.
            for k, v in incoming[key].items():
                have.setdefault(k, v)
        else:
            cur[key] = dict(incoming[key])


def _apply_glb_serializer(
    source_ext: str,
    serializer: str | None,
    tessellator: str | None,
    *,
    step_glb_pipeline: str | None,
    glb_tess_engine: str | None,
) -> tuple[str | None, str | None, bool]:
    """Fold ``serializer`` / ``tessellator`` tokens into the effective engine
    knobs. Returns ``(step_glb_pipeline, glb_tess_engine, force_python)``.

    ``force_python`` tells IFC→GLB to bypass the pure-native path and route
    through the ifcopenshell import + BatchTessellator (so the chosen kernel
    actually takes effect). When ``serializer`` is unset the explicit knobs
    (and their env/defaults) are returned untouched — full back-compat with the
    admin panel + ``ADAPY_*`` env selection. Client serializers never reach a
    worker, so they are treated as no-ops here."""
    if not serializer or serializer in _GLB_CLIENT_SERIALIZERS:
        return step_glb_pipeline, glb_tess_engine, False
    fam = _glb_source_family(source_ext)
    if serializer == _GLB_SERIALIZER_CPP:
        if fam == "step":
            step_glb_pipeline = _STEP_GLB_PIPELINE_ADACPP_NATIVE
            # The track rides the tess-engine axis: `step_glb_pipeline` is already spent naming the
            # native CODE PATH, and the two are independent axes (which reader vs which kernel), so
            # folding the track into that token would conflate them. `adacpp-native` resolves to no
            # CadConfig, so nothing else reads glb_tess_engine on this branch.
            if tessellator and tessellator != _GLB_TESS_NATIVE_PINNED:
                glb_tess_engine = _tess_token_to_glb_engine(tessellator)
        # generic/IFC 'cpp' = the native_ifc_to_glb default path — nothing to force.
        return step_glb_pipeline, glb_tess_engine, False
    if serializer == _GLB_SERIALIZER_PYTHON:
        tok = tessellator or _glb_serializer_tess(_GLB_SERIALIZER_PYTHON)[0]
        if fam == "step":
            step_glb_pipeline = _tess_token_to_pipeline(tok)
        else:
            glb_tess_engine = _tess_token_to_glb_engine(tok)
        return step_glb_pipeline, glb_tess_engine, True
    return step_glb_pipeline, glb_tess_engine, False


# ---------------------------------------------------------------------------
# B-rep → B-rep (step→ifc, ifc→step) path options. Same shared frontend selector
# as the →GLB rows, but the second axis is a WRITER (no tessellation happens),
# titled "Writer" via the backend. The writer mirrors the serializer 1:1 — each
# code path has exactly one B-rep emitter — so the writer dropdown is
# informational (one entry per serializer), consistent with how cpp→glb pins its
# tessellator. Client (wasm) B-rep writers run in-browser via the adacpp wasm
# writer modules.
_BREP_SERIALIZER_LABELS = {
    _GLB_SERIALIZER_CPP: "C++ (adacpp native)",
    _GLB_SERIALIZER_PYTHON: "Python (OCC)",
    _GLB_SERIALIZER_WASM: "WASM (browser)",
}
# serializer -> (writer token, writer label). One writer per path.
_BREP_SERIALIZER_WRITER = {
    _GLB_SERIALIZER_CPP: ("native", "Native (adacpp B-rep)"),
    _GLB_SERIALIZER_PYTHON: ("occ", "OCC / ifcopenshell"),
    _GLB_SERIALIZER_WASM: ("wasm-native", "Native (WASM)"),
}
_BREP_SERIALIZER_ORDER = (_GLB_SERIALIZER_CPP, _GLB_SERIALIZER_PYTHON, _GLB_SERIALIZER_WASM)


def _brep_serializer_options(source_ext: str, target: str) -> list[dict]:
    """serializer + dependent ``writer`` options for a B-rep→B-rep row (single-sourced, like
    :func:`_glb_serializer_options`). The writer axis is titled "Writer" and mirrors the serializer."""
    serializers = list(_BREP_SERIALIZER_ORDER)
    enum_by = {s: [_BREP_SERIALIZER_WRITER[s][0]] for s in serializers}
    writer_tokens: list[str] = []
    for s in serializers:
        for tok in enum_by[s]:
            if tok not in writer_tokens:
                writer_tokens.append(tok)
    writer_labels = {_BREP_SERIALIZER_WRITER[s][0]: _BREP_SERIALIZER_WRITER[s][1] for s in serializers}
    default_ser = serializers[0]
    return [
        {
            "name": "serializer",
            "type": "enum",
            "title": "Serializer",
            "default": default_ser,
            "enum": serializers,
            "labels": {s: _BREP_SERIALIZER_LABELS[s] for s in serializers},
            "runtime": {s: ("client" if s in _GLB_CLIENT_SERIALIZERS else "server") for s in serializers},
            "description": (
                f"Conversion code path for →{target.upper()}. 'cpp' = pure-C++ adacpp B-rep writer "
                "(fastest, dep-free); 'python' = the OpenCASCADE / ifcopenshell writer; 'wasm' runs "
                "the adacpp writer entirely in your browser (no server round-trip)."
            ),
        },
        {
            "name": "tessellator",  # wire key kept stable across targets; DISPLAYED as "Writer"
            "type": "enum",
            "title": "Writer",
            "default": enum_by[default_ser][0],
            "enum": writer_tokens,
            "labels": writer_labels,
            "enum_by": enum_by,
            "depends_on": "serializer",
            "description": "B-rep writer. Mirrors the serializer — each code path has one writer.",
        },
    ]


def _conversion_path_options(source_ext: str, target: str) -> list[dict]:
    """The shared serializer × (tessellator|writer) path options for a (source, target) row. Mesh
    targets (glb) get the tessellator axis; B-rep targets (ifc/step) get the writer axis. Single
    source of the vocabulary for the frontend selector."""
    t = target.lstrip(".").lower()
    if t == "glb":
        return _glb_serializer_options(source_ext)
    if t in ("ifc", "step"):
        return _brep_serializer_options(source_ext, t)
    return []


def _brep_writer_is_python(serializer: str | None) -> bool:
    """A B-rep→B-rep serializer that routes to the Python/OCC writer (vs the native adacpp emitter).
    Client (wasm) serializers never reach the worker, so they are not 'python' here."""
    return serializer == _GLB_SERIALIZER_PYTHON


def _default_glb_tess_engine() -> str:
    """Default engine for the non-STEP →GLB (scene) path: ``libtess2`` when adacpp is importable,
    else the OCC BatchTessellator. OCC's prism tessellation of curved B-spline plates is
    NON-MANIFOLD — it drops the viewer's per-plate edge outlines (hullskin elev13 plates) — so
    libtess2 (manifold; non-NGEOM-serializable geom still falls back to OCC per-object) is
    preferred wherever it can run. Evaluated at conversion time so a slim/adacpp-less pool still
    gets OCC."""
    from importlib.util import find_spec

    try:
        if find_spec("adacpp") is not None:
            return _STEP_GLB_PIPELINE_LIBTESS2
    except Exception:  # noqa: BLE001 - find_spec can raise on a broken import path
        pass
    return _STEP_GLB_PIPELINE_OCC


def _glb_engine_stream_value(engine: str | None) -> str | None:
    """Map the non-STEP →GLB engine option to a BatchTessellator stream pipeline value
    (``ADA_STREAM_TESS_PIPELINE``), or ``None`` for the default OCC BatchTessellator
    (``occ-builtin`` / unknown). Per-job ``engine`` wins, else the global ``ADAPY_GLB_TESS_ENGINE``
    env (set by the worker from the per-source-type ``tess_engine_*`` setting), else the
    adacpp-aware default (``_default_glb_tess_engine``)."""
    import os

    choice = (engine or os.environ.get("ADAPY_GLB_TESS_ENGINE", "") or _default_glb_tess_engine()).strip().lower()
    return _glb_engine_to_stream(choice)


def _resolve_step_glb_pipeline(step_glb_pipeline: str | None) -> str:
    """Pick the STEP→GLB engine.

    Precedence mirrors ``_should_stream_step``: an explicit per-job choice
    (``step_glb_pipeline`` kwarg, set by the worker from the job option) wins;
    otherwise the global ``ADAPY_STEP_GLB_PIPELINE`` env (same convention as
    ``ADAPY_CAD_BACKEND``); default ``adacpp-native``. The native path degrades
    to ``libtess2`` (then ``occ-builtin``) when adacpp is missing or a conversion
    raises, so the native default is safe everywhere.
    """
    import os

    from ada.config import logger

    choice = (
        (step_glb_pipeline or os.environ.get("ADAPY_STEP_GLB_PIPELINE", "") or _STEP_GLB_PIPELINE_DEFAULT)
        .strip()
        .lower()
    )
    if choice not in _step_glb_pipelines():
        logger.warning("unknown ADAPY_STEP_GLB_PIPELINE %r; falling back to %s", choice, _STEP_GLB_PIPELINE_DEFAULT)
        return _STEP_GLB_PIPELINE_DEFAULT
    return choice


def _step_glb_fallback_chain(pipe: str, cad_cfg):
    """Ordered (pipeline, CadConfig) attempts for an adacpp STEP→GLB pipeline that yields
    nothing. The requested pipeline first, then adacpp's own linked-OCCT kernel
    (``adacpp:occ``) — the right fallback when we started on the OCC-free libtess2 path
    (no pythonocc TopoDS to begin with) and on wasm, where pythonocc isn't available at all.
    The pythonocc ``occ-builtin`` path is tried last (native only), by the caller falling
    through. De-duplicated so we never retry the same pipeline."""
    chain = [(pipe, cad_cfg)]
    occ_cfg = _cad_config_for_pipeline(_STEP_GLB_PIPELINE_ADACPP_OCC)
    if occ_cfg is not None and pipe != _STEP_GLB_PIPELINE_ADACPP_OCC:
        chain.append((_STEP_GLB_PIPELINE_ADACPP_OCC, occ_cfg))
    return chain


def _cad_config_for_pipeline(pipe: str):
    """Map a STEP→GLB pipeline to a ``CadConfig`` for the streaming converter, or
    ``None`` for the OCC-builtin path (and as a graceful fallback when the requested
    adacpp tessellation path isn't available in this environment)."""
    from ada.cad.registry import CadConfig, tess_track_by_name
    from ada.config import logger

    name = _pipeline_to_track_name(pipe)
    if name is None:
        return None  # occ-builtin → OCC streaming default
    if tess_track_by_name(name) is None:
        logger.warning("step-glb pipeline %r unavailable in this environment; using occ-builtin", pipe)
        return None
    return CadConfig(path=name)


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
    step_streamer: bool | None = None,
    step_glb_pipeline: str | None = None,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
) -> bytes:
    """Heavy path: load with ada, export to target format. Used for any
    non-trivial source/target combination that needs the full ada-py
    stack. Source already lives on disk (worker streamed it there).

    ``merge_meshes`` is forwarded to :func:`_export_with_ada` so the
    per-job kwarg path established by the convert() options dispatch
    reaches the actual GLB writer call. Other targets ignore it.

    ``step_streamer`` (STEP→GLB only) forces the memory-bounded streaming
    converter; ``None`` auto-selects it for STEP files too large for the OCC
    loader to hold without OOM-killing the worker.
    """
    suffix = ".glb" if target_format == "glb" else f".{target_format}"
    out_path = pathlib.Path(tempfile.mkstemp(suffix=suffix)[1])
    result: bytes | pathlib.Path = b""
    try:
        if source_ext in {".step", ".stp"} and target_format == "glb":
            pipe = _resolve_step_glb_pipeline(step_glb_pipeline)

            if pipe == _STEP_GLB_PIPELINE_ADACPP_NATIVE:
                # Fully-native in-process path (C++ reader + thread pool + GLB writer). Falls through
                # to the standard pipelines below if adacpp's native entry point isn't available, so a
                # native request degrades gracefully rather than failing the job.
                from ada.cadit.step.native_step_to_glb import (
                    native_adacpp_available,
                    native_step_to_glb,
                )
                from ada.config import logger

                if native_adacpp_available():
                    try:
                        native_step_to_glb(
                            src_path,
                            out_path,
                            on_progress=on_progress,
                            pipeline=_native_track_for_engine(glb_tess_engine),
                        )
                        result = out_path
                        return result
                    except Exception as exc:
                        if bool(strict_tess):
                            raise
                        logger.warning(
                            "adacpp-native STEP->GLB failed for %s (%s); falling back to %s",
                            getattr(src_path, "name", src_path),
                            exc,
                            _STEP_GLB_PIPELINE_FALLBACK,
                        )
                else:
                    logger.warning(
                        "adacpp-native requested but unavailable; falling back to %s", _STEP_GLB_PIPELINE_FALLBACK
                    )
                pipe = _STEP_GLB_PIPELINE_FALLBACK

            cad_cfg = _cad_config_for_pipeline(pipe)
            if cad_cfg is not None:
                # Default path: adacpp tessellation (libtess2 / occ / cgal / hybrid) through the
                # memory-bounded streaming pipeline + its worker pool. libtess2 carries the curved
                # surfaces the OCC stream reader drops, at step2glb-parity geometry.
                from ada.cadit.step.stream_to_glb import stream_step_to_glb
                from ada.config import logger
                from ada.occ.tessellating import TessellationFallbackError

                # No geometry left behind: try the requested adacpp pipeline, then adacpp's own
                # OCC kernel (adacpp:occ) — staying in the adacpp ecosystem so this also works on
                # wasm, where pythonocc isn't available. Only if every adacpp attempt yields nothing
                # do we fall through to the pythonocc occ-builtin path below (native only).
                #
                # Strict coverage (strict_tess): enforce 100% on the requested non-OCC engine —
                # drop the adacpp:occ / occ-builtin fallbacks, and fail if the run skipped any solid
                # (stream_step_to_glb skips rather than OCC-falls-back per geom), instead of shipping
                # a partial GLB or completing on OCC.
                strict = bool(strict_tess)
                chain = _step_glb_fallback_chain(pipe, cad_cfg)
                if strict:
                    chain = chain[:1]
                for fb_pipe, fb_cfg in chain:
                    try:
                        on_progress(fb_pipe, 0.1)
                        stats = stream_step_to_glb(
                            src_path, out_path, tolerant=True, on_progress=on_progress, cad_config=fb_cfg
                        )
                        if strict and stats and stats.get("skipped"):
                            raise TessellationFallbackError(
                                f"strict tessellation: {fb_pipe} skipped {stats['skipped']}/"
                                f"{stats.get('total', '?')} solids ({stats.get('reasons')})"
                            )
                        on_progress("ready", 1.0)
                        result = out_path
                        return result
                    except Exception as exc:
                        if strict:
                            raise
                        logger.warning(
                            "step-glb %s produced no usable GLB for %s (%s); trying next fallback",
                            fb_pipe,
                            getattr(src_path, "name", src_path),
                            exc,
                        )

            if _should_stream_step(src_path, step_streamer):
                # occ-builtin, large file: stream solid-by-solid (bounded memory, no whole-model
                # OCC load) via pythonocc BRepMesh. Small files fall through to the full OCC load.
                from ada.cadit.step.stream_to_glb import stream_step_to_glb

                on_progress("streaming-step", 0.1)
                stream_step_to_glb(src_path, out_path, tolerant=True, on_progress=on_progress)
                on_progress("ready", 1.0)
                result = out_path
                return result

        on_progress("parsing", 0.15)
        model = _load_with_ada(src_path, source_ext)
        _apply_fem_to_objects(model, source_ext, target_format, fem_to_objects, merge_fem_objects, reconstruct_surfaces)
        result = _export_with_ada(
            model,
            target_format,
            out_path,
            on_progress,
            merge_meshes=merge_meshes,
            source_ext=source_ext,
            merge_fem_objects=merge_fem_objects,
            reconstruct_surfaces=reconstruct_surfaces,
            glb_tess_engine=glb_tess_engine,
            strict_tess=strict_tess,
        )
        return result
    finally:
        # When we hand back the path itself, ownership transfers to the
        # caller (the subprocess child moves it into the result slot), so we
        # must NOT delete it here. Bytes results — and the empty temp file
        # GLB never writes (it tessellates into a BytesIO) — get cleaned up.
        if not isinstance(result, pathlib.Path):
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
        result: bytes | pathlib.Path = b""
        try:
            result = _export_with_ada(
                model,
                target_format,
                out_path,
                on_progress,
                merge_meshes=opts.get("merge_meshes"),
                source_ext=entry_ext,
                merge_fem_objects=opts.get("merge_fem_objects"),
                reconstruct_surfaces=opts.get("reconstruct_surfaces"),
                glb_tess_engine=opts.get("glb_tess_engine"),
                strict_tess=opts.get("strict_tess"),
            )
            return result
        finally:
            # Path result → ownership transfers to the caller; only clean up
            # when the bytes are already in hand (see _via_ada).
            if not isinstance(result, pathlib.Path):
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
    source_uri: str | None = None,
) -> bytes:
    """Sesam SIF / SIN result deck → GLB tessellated visualisation.

    Routes by extension: ``.sif`` (text) → :func:`read_sif_file`;
    ``.sin`` (Norsam binary) → :func:`read_sin_file` (the pure-Python
    direct path, no SIF text intermediate). Both yield the same
    :class:`FEAResult` shape, then :meth:`FEAResult.to_gltf` writes a
    coloured/warped GLB. When the caller leaves step/field unset we
    fall back to the first available pair so an auto-convert at
    upload time still produces something viewable.

    ``source_uri`` (SIN only): a range-fetchable URI for the deck —
    ``open_sin`` reads pages of it straight from object storage, so a
    multi-GB deck is never downloaded in full and ``src_path`` may be an
    empty stub. The suffix routing still keys off ``src_path``.
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

        # ``open_sin`` routes by scheme, so a presigned URI reads pages of
        # the deck straight from object storage instead of a local copy.
        sin_src = source_uri or str(src_path)
        # When the caller didn't pick a step, use the cheap metadata
        # path to pick one — avoids materialising every step's records
        # just to throw them away (a hundreds-of-modes eigen deck
        # would blow the 4 GiB worker budget). Then load only that
        # step.
        if step is None or field is None:
            meta = read_sin_metadata(sin_src)
            if not meta.fields or not meta.steps:
                raise UnsupportedFormat(f"SIN {src_path.name} has no RV* result fields")
            if step is None:
                step = meta.steps[0]
            if field is None:
                # Map SIN type name → FEAResult field name. read_sin
                # exposes the SIN names verbatim; the downstream
                # display layer remaps them.
                field = meta.fields[0]
        result = read_sin_file(sin_src, step=int(step))
    else:
        from ada.fem.formats.sesam.results.read_sif import read_sif_file

        # Bound peak RSS to one step the way the SIN path does: load only the
        # requested step (or the first step in the file when the caller didn't
        # pick one) instead of materialising every step's RV* records. The GLB
        # render only colours one (step, field); a 20-mode eigen SIF that used
        # to hit multi-GB now stays at one step's footprint.
        result = read_sif_file(str(src_path), step=(int(step) if step is not None else "first"))

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


# STEP solid-root keywords — mirrors adacpp's StepNgeomStream root taxonomy
# (cadit/step/step_reader.h). Used only to detect a fully-empty OCC re-export so
# the IFC->STEP path can fall back to the faceted streaming writer.
_STEP_SOLID_ROOTS: tuple[bytes, ...] = (
    b"MANIFOLD_SOLID_BREP",
    b"SHELL_BASED_SURFACE_MODEL",
    b"BREP_WITH_VOIDS",
    b"EXTRUDED_AREA_SOLID",
    b"REVOLVED_AREA_SOLID",
    b"BOOLEAN_RESULT",
)


def _step_has_solids(path: pathlib.Path) -> bool:
    """True if the STEP file at ``path`` contains at least one solid root entity.
    Chunk-scanned (overlapping the longest keyword) so a large file isn't slurped
    whole."""
    overlap = max(len(k) for k in _STEP_SOLID_ROOTS) - 1
    tail = b""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    return False
                window = tail + chunk
                if any(k in window for k in _STEP_SOLID_ROOTS):
                    return True
                tail = window[-overlap:]
    except OSError:
        return False


def _via_ada_to_step(
    src_path: pathlib.Path,
    source_ext: str,
    on_progress: ProgressFn,
    *,
    fem_to_objects: bool | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
    fem_merge_strategy: str | None = None,
) -> pathlib.Path:
    """Ada-loadable source → STEP via the OCC writer.

    Primary use is the IFC → STEP interop case (no STEP writer in
    ifcopenshell itself); also exercised by .step / .stp identity
    re-exports, which can be useful for normalising a malformed STEP
    through OCC's parser.
    """

    from ada.config import logger

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    is_fem = source_ext.lower() in _FEM_SOURCE_EXTS
    if not is_fem:
        # IFC/CAD source: materialise any FEM-derived concept objects up front
        # (a no-op when there is no mesh) before the OCC writer runs.
        _apply_fem_to_objects(model, source_ext, "step", fem_to_objects, merge_fem_objects, reconstruct_surfaces)
    on_progress("writing-step", 0.55)
    out_path = pathlib.Path(tempfile.mkstemp(suffix=".step")[1])
    returned_path = False
    try:
        if is_fem:
            # A FEM mesh rebuilds into extruded plates/straight beams, which the
            # streaming AP242 writer emits one-at-a-time at constant memory. We
            # deliberately do NOT pre-build them here: the create_objects_from_fem
            # phase (not the writer) was the multi-GB peak that OOM-killed the
            # worker on large jackets/ships — the writer fuses Beam/Plate straight
            # from the mesh. The OCC XCAF writer would instead accumulate every
            # solid plus a full entity-graph copy. fem_to_objects=False opts out.
            # Fold FEM shells via the shared object-free face engine (matches the
            # Genie-XML and IFC streamers) unless curved-plate reconstruction is
            # requested. merge_fem_objects -> coplanar/none.
            recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
            ms = None
            if not recon:
                # Auto-detect analytic primitives by DEFAULT (no human guidance): tubular
                # members -> exact cylinder surfaces, flat panels -> plates. Legacy overrides:
                # a string merge_fem_objects is the strategy verbatim; merge_fem_objects=False
                # opts out of merging entirely; fem_merge_strategy 'coplanar'/'none' force those.
                strat = (fem_merge_strategy or "auto").lower()
                if isinstance(merge_fem_objects, str):
                    ms = merge_fem_objects
                elif merge_fem_objects is False:
                    ms = "none"
                elif strat == "auto":
                    ms = "cylinder"  # analytic auto-detect
                else:
                    ms = strat  # coplanar | none | cylinder
            stats = model.to_stp(
                str(out_path), writer="stream", fuse_fem=(fem_to_objects is not False), merge_strategy=ms
            )
            skipped = (stats or {}).get("skipped", 0)
            if skipped:
                logger.warning(f"streaming STEP writer skipped {skipped} non-extrudable object(s)")
        else:
            model.to_stp(str(out_path))
            if not _step_has_solids(out_path):
                # The OCC/adacpp writer emitted no solid root — e.g. an alignment
                # IfcFixedReferenceSweptAreaSolid swept over an IfcGradientCurve,
                # which has no analytic AP242 form and isn't ported to the adacpp
                # B-rep builder, so every object was skipped. Retry via the
                # kernel-free streaming writer, which tessellates such solids to a
                # faceted MANIFOLD_SOLID_BREP so no geometry is left behind.
                logger.info("OCC ifc->step produced no solids; retrying via streaming faceted writer")
                model.to_stp(str(out_path), writer="stream", fuse_fem=False)
        on_progress("ready", 1.0)
        returned_path = True
        return out_path
    finally:
        # Ownership of the STEP file transfers to the caller on success; only
        # remove it if we bailed before returning the path.
        if not returned_path:
            try:
                out_path.unlink()
            except OSError:
                pass


# NOTE: _INCLUDE_RE / _inline_abaqus_includes / _find_writer_output /
# _FEM_TARGET_TO_FORMAT below are superseded by ada.fem.formats.deck_convert
# (the single source now shared with the WASM path) and are no longer called.
# Kept temporarily; safe to delete in a follow-up.
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

    # Shared with the WASM path (ada.cadit.wasm_convert) — the deck-rewrite
    # logic (writer dispatch, output-file selection, *INCLUDE inlining) lives
    # in ada.fem.formats.deck_convert so server and browser can't diverge.
    from ada.fem.formats.deck_convert import fem_deck_to_bytes

    try:
        return fem_deck_to_bytes(src_path, target_ext, on_progress)
    except ValueError as exc:
        raise UnsupportedFormat(str(exc)) from exc


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


def _via_step_stream_to_step(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """STEP → STEP (AP242) via the per-solid NGEOM stream — **no OCC**.

    The native adacpp NGEOM reader (pure-Python stream reader as a drop-in
    fallback) yields one analytic ``ada.geom.Geometry`` per solid — full B-rep
    incl. B-spline surfaces/curves and swept surfaces, plus colour, world
    placement and name — and each is hand-authored straight to STEP Part-21 by
    the kernel-free :class:`Ap242StreamWriter`. Peak memory is O(one solid), so
    the multi-GB assemblies that OOM/timed out through ``ada.from_step`` →
    ``to_stp`` now stream through. No tessellation, no OCC anywhere in the path.
    """
    from ada.config import logger

    out_path = pathlib.Path(tempfile.mkstemp(suffix=".step")[1])
    from ada.cadit.step.native_step_to_step import (
        native_step_to_step,
        native_step_to_step_available,
    )

    if native_step_to_step_available():
        try:
            stats = native_step_to_step(src_path, out_path, on_progress=on_progress)
            logger.info("native STEP->STEP: %s", stats)
            return out_path
        except Exception as exc:  # noqa: BLE001 - degrade to the Python AP242 writer
            logger.warning("native STEP->STEP failed (%s); falling back to per-solid Python", exc)

    from ada.cadit.step.write.stream_step_to_step import stream_step_to_step

    stats = stream_step_to_step(src_path, out_path, on_progress=on_progress)
    logger.info("stream STEP->STEP (python): %s", stats)
    return out_path


def _via_ifc_to_step(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """IFC → AP242 STEP via the native adacpp IFC B-rep reader → ng:: → STEP writer — **no OCC**.

    A native IFC advanced-B-rep reader (analytic surfaces/curves + IfcMappedItem instancing) builds
    ng:: neutral geometry which the AP242 STEP emitter re-writes (instances baked). The declared length
    unit is preserved. Falls back to the OCC path (``_via_ada_to_step``) when the native reader can't
    fully cover the file — IFC with IfcExtrudedAreaSolid / CSG / tessellated geometry, or any
    product/face left behind — so no geometry is silently lost.
    """
    from ada.cadit.step.native_ifc_to_step import (
        native_ifc_to_step,
        native_ifc_to_step_available,
    )
    from ada.config import logger

    if native_ifc_to_step_available():
        try:
            out_path = pathlib.Path(tempfile.mkstemp(suffix=".step")[1])
            stats = native_ifc_to_step(src_path, out_path, on_progress=on_progress)
            logger.info("native IFC->STEP: %s", stats)
            return out_path
        except Exception as exc:  # noqa: BLE001 - native can't cover this IFC; use the OCC writer
            logger.info("native IFC->STEP unavailable/incomplete (%s); using OCC", exc)
    return _via_ada_to_step(src_path, ".ifc", on_progress)


def _via_step_stream_to_ifc(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """STEP → IFC4X3_ADD2 advanced B-rep — **no OCC, no ada Assembly**, bounded memory.

    Prefers the fully-native adacpp writer (``stream_step_to_ifc``): the same C++ reader the GLB/mesh
    paths use resolves each solid's analytic B-rep, emits it as an IfcAdvancedBrep (cones →
    IfcSurfaceOfRevolution, splines → IfcBSplineSurface, …) and places instances via IfcMappedItem,
    parallel across the cgroup-aware thread allotment. Lossless (every solid/face/edge analytic) and
    ifcopenshell-valid. Falls back to the per-solid Python writer (``stream_step_to_ifc``: hand-authors
    one ``ada.geom.Geometry`` per solid) when adacpp's native IFC entry point is absent. Either way the
    ifcopenshell.file only ever holds the spatial preamble, so the multi-GB assemblies that OOM/timed
    out through ``ada.from_step`` → ``to_ifc`` now stream through.
    """
    from ada.config import logger

    out_path = pathlib.Path(tempfile.mkstemp(suffix=".ifc")[1])
    from ada.cadit.step.native_step_to_ifc import (
        native_ifc_available,
        native_step_to_ifc,
    )

    if native_ifc_available():
        try:
            stats = native_step_to_ifc(src_path, out_path, on_progress=on_progress)
            logger.info("native STEP->IFC: %s", stats)
            return out_path
        except Exception as exc:  # noqa: BLE001 - degrade to the Python writer
            logger.warning("native STEP->IFC failed (%s); falling back to per-solid Python", exc)

    from ada.cadit.step.write.stream_step_to_ifc import stream_step_to_ifc

    stats = stream_step_to_ifc(src_path, out_path, on_progress=on_progress)
    logger.info("stream STEP->IFC (python): %s", stats)
    return out_path


def _via_step_stream_to_xml(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """STEP → Genie XML via the per-solid stream — **no OCC, no whole-model load**.

    Raw CAD B-rep has no Genie-XML concept representation, so the XML is the empty
    structural scaffold. We stream-parse the STEP to validate it reads (bounded,
    native C++ parser, no ada.geom hydrate) rather than the full ``ada.from_step``
    load that timed out the multi-GB assemblies for an empty output.
    """
    from ada.cadit.step.write.stream_step_to_xml import stream_step_to_xml
    from ada.config import logger

    out_path = pathlib.Path(tempfile.mkstemp(suffix=".xml")[1])
    stats = stream_step_to_xml(src_path, out_path, on_progress=on_progress)
    logger.info("stream STEP->XML: %s", stats)
    return out_path


def _via_step_stream_to_mesh(
    src_path: pathlib.Path,
    source_ext: str,
    target_ext: str,
    on_progress: ProgressFn,
    *,
    step_glb_pipeline: str | None = None,
) -> pathlib.Path:
    """STEP → mesh container (``.obj`` / ``.stl``) **without building an ada
    Assembly**, bounded memory, no OCC.

    Prefers the fully-native adacpp pipeline (``stream_step_to_mesh``): the same C++
    reader + parallel libtess2 as the native GLB path, baking each placement and
    streaming triangles straight to disk — ~2.5x faster than per-solid Python on
    giant-solid / FEM-export STEP. Falls back to the per-solid Python writer
    (``stream_step_to_mesh``: tessellate one solid at a time via the active backend,
    transform per triangle-batch) when adacpp's native mesh entry point is absent.
    Either way peak memory is O(one solid's mesh), never a whole-model buffer.
    ``step_glb_pipeline`` is accepted for signature compatibility but unused.
    """
    out_path = pathlib.Path(tempfile.mkstemp(suffix=target_ext)[1])
    fmt = target_ext.lstrip(".")
    from ada.cadit.step.native_step_to_mesh import (
        native_mesh_available,
        native_step_to_mesh,
    )
    from ada.config import logger

    if native_mesh_available():
        try:
            native_step_to_mesh(src_path, out_path, fmt, on_progress=on_progress)
            return out_path
        except Exception as exc:  # noqa: BLE001 - degrade to the Python writer
            logger.warning("native STEP->%s failed (%s); falling back to per-solid Python", fmt, exc)

    from ada.cadit.step.write.stream_step_to_mesh import stream_step_to_mesh

    stream_step_to_mesh(src_path, out_path, fmt, on_progress=on_progress)
    return out_path


def convert(
    src_path: pathlib.Path,
    source_key: str,
    target_format: str = "glb",
    on_progress: ProgressFn | None = None,
    *,
    step: int | None = None,
    field: str | None = None,
    options: dict | None = None,
    source_uri: str | None = None,
) -> bytes | pathlib.Path:
    """Convert a local source file to the requested target format.

    Dispatches via :class:`ConverterRegistry` — every viable (from,
    to) pair has an explicit registration at the bottom of this
    module. The only special case is multi-file bundles (``.zip``):
    we unpack first, then re-enter the registry against the inner
    entry-point's extension.

    The worker streams the source from object storage into a tempfile
    and passes its path here, so we never round-trip the full payload
    through a `bytes` buffer in memory. Output is returned as **bytes or
    a path**: GLB / mesh / FEA-result handlers build their output in RAM
    and return bytes; the disk-writing exporters (IFC, Genie XML, STEP)
    return the ``pathlib.Path`` of the file they wrote, transferring
    ownership to the caller, which streams it to object storage via
    `Storage.put_path` instead of reading it back into a buffer. Direct
    callers that want bytes should read the path themselves (see the
    `ada audit` repro path / `result_bytes` helper).

    ``step`` / ``field`` only apply to FEA result sources (.sif /
    .sin). When unset the FEA handler picks the first available pair,
    matching the behavior of the auto-convert at upload time.

    ``source_uri`` is a range-fetchable URI (presigned GET / ``s3://``)
    for the source blob. Only readers with a paged byte source honour it
    (today: ``.sin`` via ``open_sin``); when set, the handler reads pages
    straight from object storage and ``src_path`` may be an empty stub.
    Forwarded only when present so handlers without the kwarg are
    untouched.

    ``options`` is a per-job knob dict — keys match option ``name``
    fields declared at the ``@converter(options=[...])`` site for the
    selected (from, to) pair. Forwarded to the handler as kwargs; the
    handler's adapter (registered by ``@converter``) unpacks the
    options it understands and ignores the rest, so passing unknown
    keys is harmless. Legacy env-var-driven options (use_sat_pcurves
    / skip_shapefix) still flow through env vars
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
    extra: dict = {"source_uri": source_uri} if source_uri is not None else {}
    return handler(src_path, progress, step=step, field=field, **extra, **opts)


def result_bytes(result: bytes | pathlib.Path) -> bytes:
    """Materialise a :func:`convert` result as bytes.

    ``convert`` returns bytes for in-RAM handlers and a ``pathlib.Path`` for
    the disk-writing exporters (so the worker can stream the file straight to
    storage). Direct callers that genuinely need the bytes — the ``ada audit``
    repro CLI, unit tests — funnel through here. Reading a large path back into
    RAM is exactly what the worker avoids, so reserve this for the
    small/diagnostic callers, not the hot upload path.
    """
    if isinstance(result, pathlib.Path):
        return result.read_bytes()
    return bytes(result)


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
        {
            "name": "tess_linear_deflection",
            "type": "number",
            "default": 0.0,
            "description": (
                "Curved-surface tessellation quality. 0 (default) uses the lean relative "
                "mesher — smallest GLB, mobile-friendly. A positive value (in model "
                "units, e.g. mm) switches to an explicit chordal deflection: smaller = "
                "smoother curves but more triangles / larger GLB (step2glb uses ~1 mm)."
            ),
        },
        {
            "name": "tess_angular_deg",
            "type": "number",
            "default": 20.0,
            "description": (
                "Angular deflection (degrees) for the explicit-deflection mesher (only "
                "when tess_linear_deflection > 0). Drives facet count on doubly-curved "
                "surfaces (spheres / tori / B-splines); smaller = smoother."
            ),
        },
        {
            "name": "tess_relative",
            "type": "bool",
            "default": False,
            "description": (
                "Treat tess_linear_deflection as a fraction of each shape's bbox instead "
                "of absolute model units (only when tess_linear_deflection > 0)."
            ),
        },
        {
            "name": "glb_compression",
            "type": "enum",
            "default": "meshopt",
            "enum": ["off", "meshopt"],
            "description": (
                "'meshopt' applies EXT_meshopt_compression to the GLB buffers "
                "(~2.5-3x smaller download, decoded client-side). Structure-preserving: "
                "re-encodes only the vertex/index bytes losslessly and leaves the glTF JSON "
                "byte-identical, so node names, draw_ranges, id_hierarchy and ADA_EXT_data "
                "are kept and picking/hierarchy still work. On by default (gzip-at-rest applies "
                "on top); a safe no-op if meshoptimizer/adacpp isn't installed in the worker."
            ),
        },
    ]

    # STEP→GLB only: route the conversion through the memory-bounded streaming
    # reader (one solid at a time) instead of loading the whole model via OCC.
    step_streamer_option = {
        "name": "step_streamer",
        "type": "bool",
        "default": False,
        "description": (
            "Load STEP with the memory-bounded streaming reader (one solid at a "
            "time) instead of OpenCASCADE. Use for very large assemblies that "
            "OOM the normal path; skips solids using unsupported (spherical / "
            "rational B-spline) surfaces. Auto-enabled above 200 MB."
        ),
    }

    # STEP→GLB only: choose the tessellation engine. ``libtess2`` (default) is
    # adacpp's OCC-free boundary tessellator with step2glb-parity geometry incl.
    # the curved surfaces the OCC stream reader drops; ``occ-builtin`` is the prior
    # OpenCASCADE path; ``adacpp-{occ,cgal,hybrid}`` use adacpp's taxonomy kernels.
    step_glb_pipeline_option = {
        "name": "step_glb_pipeline",
        "type": "enum",
        "default": _STEP_GLB_PIPELINE_DEFAULT,
        "enum": list(_step_glb_pipelines()),
        "description": (
            "STEP→GLB tessellation engine. 'adacpp-native' (default) runs the whole STEP→GLB in "
            "adacpp C++ (reader + thread pool + GLB writer) — fastest + lowest-memory, and 1:1 with "
            "the Python path (geometry, product names, per-instance picking, full assembly tree); "
            "falls back to 'libtess2' then 'occ-builtin'. 'libtess2' is adacpp's OCC-free boundary "
            "tessellator (Python reader + worker pool) and renders the curved surfaces (rational "
            "B-spline / spherical / conical / toroidal) the OCC streaming reader silently drops. "
            "'occ-builtin' is the OpenCASCADE path. 'adacpp-occ' / 'adacpp-cgal' / 'adacpp-hybrid' "
            "use adacpp's taxonomy kernels."
        ),
    }

    # Non-STEP →GLB tessellation engine (xml / ifc / sat / fem / obj / stl → glb). Same engines
    # as STEP but driving to_gltf's BatchTessellator stream selector; default libtess2 (adacpp-aware,
    # matching the runtime `_default_glb_tess_engine`), degrading to OCC on an adacpp-less pool.
    glb_tess_engine_option = {
        "name": "glb_tess_engine",
        "type": "enum",
        "default": _GLB_TESS_ENGINE_DEFAULT,
        "enum": list(_glb_tess_engines()),
        "description": (
            "→GLB tessellation engine. 'libtess2' (default) is adacpp's OCC-free boundary "
            "tessellator — renders curved surfaces OCC "
            "drops and avoids the OCC optimal-bbox cost on curved-heavy models (per-geom fallback "
            "to OCC when a geometry isn't yet NGEOM-serializable). 'adacpp-occ' / 'adacpp-cgal' / "
            "'adacpp-hybrid' use adacpp's taxonomy kernels. Engines needing adacpp fall back to OCC "
            "when it's unavailable."
        ),
    }

    # Strict coverage: fail the conversion if any geometry falls back from the selected OCC-free
    # stream engine to OCC, instead of silently completing on OCC. Lets you enforce/measure 100%
    # libtess2 (or adacpp-*) coverage. No effect when the engine is 'occ-builtin'.
    strict_tess_option = {
        "name": "strict_tess",
        "type": "bool",
        "default": False,
        "description": (
            "Fail if the selected (non-OCC) tessellation engine can't handle a geometry and would "
            "fall back to OCC. Use to enforce/measure 100% libtess2/adacpp coverage — the "
            "conversion errors out naming the offending geometry instead of silently completing on "
            "the OCC path. No effect when the engine is 'occ-builtin'."
        ),
    }

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
        {
            "name": "fem_merge_strategy",
            "type": "enum",
            "default": "auto",
            "enum": ["auto", "coplanar", "none"],
            "description": (
                "How FEM shells fold into CAD faces (STEP target). 'auto' (default) "
                "recognises each region as an analytic primitive with NO human guidance "
                "— tubular members become exact CYLINDRICAL_SURFACE faces, flat panels "
                "merge into plates (a jacket collapses from ~100k plates to a handful of "
                "cylinders in seconds). 'coplanar' forces flat-plate merging only; 'none' "
                "keeps one face per element."
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
                step_streamer=None,
                step_glb_pipeline=None,
                glb_tess_engine=None,
                strict_tess=None,
                serializer=None,
                tessellator=None,
                **_kw,
            ):
                # The friendly serializer/tessellator dropdowns resolve into the existing engine
                # knobs (single-sourced by _apply_glb_serializer); explicit step_glb_pipeline /
                # glb_tess_engine still work when serializer is unset.
                step_glb_pipeline, glb_tess_engine, _force_python = _apply_glb_serializer(
                    _ext,
                    serializer,
                    tessellator,
                    step_glb_pipeline=step_glb_pipeline,
                    glb_tess_engine=glb_tess_engine,
                )
                return _via_ada(
                    src,
                    _ext,
                    _tgt,
                    on_progress,
                    merge_meshes=merge_meshes,
                    fem_to_objects=fem_to_objects,
                    merge_fem_objects=merge_fem_objects,
                    reconstruct_surfaces=reconstruct_surfaces,
                    step_streamer=step_streamer,
                    step_glb_pipeline=step_glb_pipeline,
                    glb_tess_engine=glb_tess_engine,
                    strict_tess=strict_tess,
                )

            if tgt == "glb":
                # STEP sources: streaming toggle + the STEP engine selector (incl. the OCC
                # streaming reader). Other →glb sources: the BatchTessellator engine toggle.
                # strict_tess (fail-on-OCC-fallback) applies to the non-STEP BatchTessellator path.
                # The serializer/tessellator dropdowns are added on every →glb row.
                if ext in {".step", ".stp"}:
                    row_options = glb_options + [step_streamer_option, step_glb_pipeline_option, strict_tess_option]
                else:
                    row_options = glb_options + [glb_tess_engine_option, strict_tess_option]
                row_options = row_options + _glb_serializer_options(ext)
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
            src,
            on_progress,
            *,
            _ext=ext,
            fem_to_objects=None,
            merge_fem_objects=None,
            reconstruct_surfaces=None,
            fem_merge_strategy=None,
            **_kw,
        ):
            return _via_ada_to_step(
                src,
                _ext,
                on_progress,
                fem_to_objects=fem_to_objects,
                merge_fem_objects=merge_fem_objects,
                reconstruct_surfaces=reconstruct_surfaces,
                fem_merge_strategy=fem_merge_strategy,
            )

        ConverterRegistry.register(
            ext,
            "step",
            _step,
            options=(fem_to_objects_options if ext in _FEM_SOURCE_EXTS else None),
        )


def _register_fea_result_to_glb() -> None:
    for ext in _FEA_RESULT_EXTS:

        def _h(src, on_progress, *, _ext=ext, step=None, field=None, source_uri=None, **_kw):
            return _via_fea_result(
                src,
                "glb",
                on_progress,
                step=step,
                field=field,
                source_uri=source_uri,
            )

        ConverterRegistry.register(ext, "glb", _h)


def _via_ifc_stream_to_glb(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
    *,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
    force_python: bool = False,
) -> bytes | pathlib.Path:
    """IFC → GLB, fully native (adacpp ``stream_ifc_to_glb``: pure-C++ IfcResolver → libtess2 →
    merge-by-colour GLB, no ifcopenshell/OCC), with a graceful fallback to the ifcopenshell
    ``from_ifc`` → GLB path. The native GLB carries geometry + per-mesh colour + the spatial tree +
    names — viewer-equivalent (IFC property sets never live in the GLB; they are fetched on selection).
    Falls back when adacpp's native entry is absent, the run raises, or it produces 0 products (e.g. an
    IFC of only tessellated face-sets the analytic resolver skips).

    ``force_python`` (serializer='python') bypasses the pure-native path entirely so the chosen
    ``glb_tess_engine`` kernel actually drives the ifcopenshell import + BatchTessellator."""
    from ada.cadit.ifc.native_ifc_to_glb import (
        native_ifc_glb_available,
        native_ifc_to_glb,
    )
    from ada.config import logger

    if not force_python and native_ifc_glb_available():
        try:
            out_path = pathlib.Path(tempfile.mkstemp(suffix=".glb")[1])
            stats = native_ifc_to_glb(src_path, out_path, on_progress=on_progress)
            if stats.get("solids", 0) > 0:
                logger.info("native IFC->GLB: %s", stats)
                return out_path
            logger.info("native IFC->GLB produced 0 products; falling back to from_ifc")
        except Exception as exc:  # noqa: BLE001
            logger.info("native IFC->GLB failed (%s); falling back to from_ifc", exc)
    return _via_ada(src_path, ".ifc", "glb", on_progress, glb_tess_engine=glb_tess_engine, strict_tess=strict_tess)


def _register_step_stream_exports() -> None:
    # STEP/STP exports that bypass the full-OCC Assembly (which OOM-kills / times
    # out on multi-GB CAD assemblies). Registered AFTER _register_ada_loadable so
    # these OVERRIDE the generic OCC registrations for STEP sources only; all other
    # ada-loadable sources keep their OCC paths.
    #
    #  • obj/stl → memory-bounded native streaming GLB, then trimesh transcode.
    #  • step    → per-solid native NGEOM stream straight into the AP242 writer
    #              (analytic B-rep incl. B-splines; no OCC, no tessellation).
    #  • ifc     → per-solid native NGEOM stream straight into the IFC4
    #              advanced-B-rep writer (analytic; no OCC, no tessellation).
    for ext in (".step", ".stp"):
        for tgt in ("stl", "obj"):

            def _h(src, on_progress, *, _ext=ext, _tgt=tgt, step_glb_pipeline=None, **_kw):
                return _via_step_stream_to_mesh(src, _ext, f".{_tgt}", on_progress, step_glb_pipeline=step_glb_pipeline)

            ConverterRegistry.register(ext, tgt, _h)

        def _h_step(src, on_progress, *, _ext=ext, **_kw):
            return _via_step_stream_to_step(src, on_progress)

        ConverterRegistry.register(ext, "step", _h_step)

        def _h_ifc(src, on_progress, *, _ext=ext, serializer=None, tessellator=None, **_kw):
            # serializer=python routes to the OCC/ifcopenshell writer; cpp (default) uses the native
            # streaming adacpp B-rep emitter. wasm serializers run client-side (never reach here).
            if _brep_writer_is_python(serializer):
                return _via_ada(src, _ext, "ifc", on_progress)
            return _via_step_stream_to_ifc(src, on_progress)

        ConverterRegistry.register(ext, "ifc", _h_ifc, options=_conversion_path_options(ext, "ifc"))

        def _h_xml(src, on_progress, *, _ext=ext, **_kw):
            return _via_step_stream_to_xml(src, on_progress)

        ConverterRegistry.register(ext, "xml", _h_xml)

    # IFC → STEP via the native adacpp IFC B-rep reader → ng:: → AP242 writer (no OCC). Overrides the
    # generic OCC ifc→step ONLY when the native verb is present, so older builds keep the OCC path.
    try:
        from ada.cadit.step.native_ifc_to_step import native_ifc_to_step_available

        _native_ifc2step = native_ifc_to_step_available()
    except Exception:  # noqa: BLE001
        _native_ifc2step = False
    if _native_ifc2step:

        def _h_ifc2step(src, on_progress, *, serializer=None, tessellator=None, **_kw):
            # serializer=python routes to the OCC ifc→step writer; cpp (default) uses the native
            # adacpp B-rep reader → AP242 writer. wasm serializers run client-side.
            if _brep_writer_is_python(serializer):
                return _via_ada(src, ".ifc", "step", on_progress)
            return _via_ifc_to_step(src, on_progress)

        ConverterRegistry.register(".ifc", "step", _h_ifc2step, options=_conversion_path_options(".ifc", "step"))

    # IFC → GLB via the native adacpp IFC pipeline (no ifcopenshell/OCC), overriding the generic
    # from_ifc → GLB. Gated on the native verb; degrades to from_ifc per _via_ifc_stream_to_glb's
    # own fallback (absent verb / error / 0 products), so this is always safe to register.
    try:
        from ada.cadit.ifc.native_ifc_to_glb import native_ifc_glb_available

        _native_ifc2glb = native_ifc_glb_available()
    except Exception:  # noqa: BLE001
        _native_ifc2glb = False
    if _native_ifc2glb:

        def _h_ifc2glb(
            src, on_progress, *, glb_tess_engine=None, strict_tess=None, serializer=None, tessellator=None, **_kw
        ):
            _step_pipe, glb_tess_engine, force_python = _apply_glb_serializer(
                ".ifc",
                serializer,
                tessellator,
                step_glb_pipeline=None,
                glb_tess_engine=glb_tess_engine,
            )
            return _via_ifc_stream_to_glb(
                src,
                on_progress,
                glb_tess_engine=glb_tess_engine,
                strict_tess=strict_tess,
                force_python=force_python,
            )

        # Preserve the serializer/tessellator + engine options declared on the generic ifc→glb row.
        ConverterRegistry.register(".ifc", "glb", _h_ifc2glb, options=ConverterRegistry.options_for(".ifc", "glb"))


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
_register_step_stream_exports()


# Allowed target formats — populated from the registry once every
# ``_register_*`` call above has fired. Same surface as before
# (frozenset of bare-name target extensions) so external imports
# (``from .converter import TARGET_FORMATS``) keep working.
TARGET_FORMATS: frozenset[str] = ConverterRegistry.all_targets()

# Union of source extensions backed by at least one registered
# converter (legacy ``/convert`` pipeline reach). Bundles are
# included because they unpack to a registered source.
LEGACY_CONVERT_EXTS: frozenset[str] = ConverterRegistry.all_sources() | _BUNDLE_EXTS

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

This module is the facade: the handler families live in :mod:`.converters` (``registry``,
``keys``, ``ada_load``, ``ada_export``, ``ada_pairs``, ``pipelines``, ``serializers``, ``fea``, ``mesh_step``,
``step_stream``) and are re-exported here, so ``ada.comms.rest.converter`` keeps its surface.
"""

from __future__ import annotations

import pathlib
from typing import Iterable

from . import converters as _converters  # noqa: F401 — registers every handler family
from .converters.ada_export import (  # noqa: F401
    _STEP_STREAM_DEFAULT_THRESHOLD_MB,
    _export_with_ada,
    _should_stream_step,
    _via_ada,
    _via_bundle,
)
from .converters.ada_load import (  # noqa: F401
    _ASM_CACHE_VERSION,
    _FEM_OBJECT_CAD_TARGETS,
    _FEM_SOURCE_EXTS,
    _GXML_TARGETS,
    _apply_fem_to_objects,
    _asm_cache_path,
    _gxml_face_streaming,
    _load_with_ada,
    _model_has_thick_curved_shells,
    _native_ngeom_mesh_route,
    _passthrough,
    _via_gltf_to_glb,
    _via_trimesh,
)
from .converters.ada_pairs import (  # noqa: F401
    _register_ada_loadable,
    _register_passthrough_glb,
    _register_trimesh_to_glb,
)
from .converters.fea import (  # noqa: F401
    _FEM_TARGET_TO_FORMAT,
    _INCLUDE_RE,
    _fea_to_fea,
    _find_writer_output,
    _inline_abaqus_includes,
    _pick_default_step_field,
    _register_fea_result_to_glb,
    _sin_to_fem,
    _via_fea_result,
    _via_fea_to_fem,
    compute_fea_meta,
)
from .converters.keys import (  # noqa: F401
    _ADA_LOADABLE_EXTS,
    _BUNDLE_EXTS,
    _FALSE,
    _FEA_ARTEFACT_SUFFIX,
    _FEA_META_SUFFIX,
    _FEA_RESULT_EXTS,
    _GXML_SOURCE_EXTS,
    _PASSTHROUGH_EXTS,
    _SIF_INDEX_SUFFIX,
    _STREAMING_FEA_EXTS,
    _TRIMESH_EXTS,
    _TRUE,
    EXPECTED_FEA_BAKE_VERSION,
    FEA_ARTEFACT_SOURCE_EXTS,
    HIDDEN_PREFIXES,
    PUBLISHED_ASSET_PREFIX,
    _ext,
    derived_key_for,
    fea_artefact_manifest_key_for,
    fea_artefact_prefix_for,
    fea_manifest_stale_reason,
    fea_meta_key_for,
    is_derived_key,
    is_fea_artefact_source,
    is_fea_result_key,
    is_hidden_key,
    is_published_asset_key,
    is_supported_source,
    is_versions_artefact_key,
    reconvert_key_for,
    sif_index_key_for,
    supported_targets_for,
)
from .converters.mesh_step import (  # noqa: F401
    _MESH_ONLY_TARGETS,
    _STEP_SOLID_ROOTS,
    _register_glb_to_mesh,
    _seed_empty_scene,
    _step_has_solids,
    _strip_unexportable_for,
    _via_ada_to_step,
    _via_ada_to_trimesh,
    _via_glb_to_trimesh,
)
from .converters.pipelines import (  # noqa: F401
    _GLB_TESS_ENGINE_DEFAULT,
    _GLB_TESS_ENGINES_STATIC,
    _HISTORIC_PIPELINE_TRACK,
    _HISTORIC_TRACK_PIPELINE,
    _LEGACY_TESS_ALIASES,
    _STEP_GLB_PIPELINE_ADACPP_CGAL,
    _STEP_GLB_PIPELINE_ADACPP_HYBRID,
    _STEP_GLB_PIPELINE_ADACPP_NATIVE,
    _STEP_GLB_PIPELINE_ADACPP_OCC,
    _STEP_GLB_PIPELINE_DEFAULT,
    _STEP_GLB_PIPELINE_FALLBACK,
    _STEP_GLB_PIPELINE_LIBTESS2,
    _STEP_GLB_PIPELINE_OCC,
    _STEP_GLB_PIPELINES_STATIC,
    _adacpp_stream_pipelines,
    _cad_config_for_pipeline,
    _default_glb_tess_engine,
    _glb_engine_stream_value,
    _glb_engine_to_stream,
    _glb_tess_engines,
    _native_track_for_engine,
    _pipeline_to_track_name,
    _resolve_step_glb_pipeline,
    _step_glb_fallback_chain,
    _step_glb_pipelines,
    _tess_token_to_glb_engine,
    _tess_token_to_pipeline,
    available_step_glb_pipelines,
    available_tess_tokens,
)
from .converters.registry import (  # noqa: F401
    ConverterFn,
    ConverterRegistry,
    ProgressFn,
    UnsupportedFormat,
    converter,
)
from .converters.serializers import (  # noqa: F401
    _BREP_SERIALIZER_LABELS,
    _BREP_SERIALIZER_ORDER,
    _BREP_SERIALIZER_WRITER,
    _GLB_CLIENT_SERIALIZERS,
    _GLB_SERIALIZER_CPP,
    _GLB_SERIALIZER_LABELS,
    _GLB_SERIALIZER_ORDER,
    _GLB_SERIALIZER_PYTHON,
    _GLB_SERIALIZER_WASM,
    _GLB_TESS_NATIVE_PINNED,
    _GLB_TESS_OWN_LABELS,
    _OPTION_LIST_KEYS,
    _OPTION_MAP_KEYS,
    _WASM_ENGINE_NATIVE,
    _WASM_ENGINE_PYODIDE,
    _WASM_GLB_ENGINES,
    _apply_glb_serializer,
    _brep_serializer_options,
    _brep_writer_is_python,
    _conversion_path_options,
    _cpp_tess_tokens,
    _face_regions_serializers,
    _glb_serializer_options,
    _glb_serializer_tess,
    _glb_source_family,
    _glb_tess_description,
    _glb_tess_label,
    _python_tess_tokens,
    merge_option_into,
)
from .converters.step_stream import (  # noqa: F401
    _register_step_stream_exports,
    _via_ifc_stream_to_glb,
    _via_ifc_to_step,
    _via_step_stream_to_ifc,
    _via_step_stream_to_mesh,
    _via_step_stream_to_step,
    _via_step_stream_to_xml,
)


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


# Allowed target formats — populated from the registry once every
# ``_register_*`` call above has fired. Same surface as before
# (frozenset of bare-name target extensions) so external imports
# (``from .converter import TARGET_FORMATS``) keep working.
TARGET_FORMATS: frozenset[str] = ConverterRegistry.all_targets()


# Union of source extensions backed by at least one registered
# converter (legacy ``/convert`` pipeline reach). Bundles are
# included because they unpack to a registered source.
LEGACY_CONVERT_EXTS: frozenset[str] = ConverterRegistry.all_sources() | _BUNDLE_EXTS

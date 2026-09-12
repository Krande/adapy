"""Source/derived key conventions: extension families, derived / reconvert / FEA artefact / SIF index
keys, the hidden and published prefixes, and the key predicates the routes gate on.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from .registry import ConverterRegistry, UnsupportedFormat

if TYPE_CHECKING:
    pass


# Extensions we hand to trimesh directly. trimesh will infer the type
# and emit GLB without ada-py needing to be involved at all.
_TRIMESH_EXTS: frozenset[str] = frozenset({".obj", ".stl", ".ply", ".dae", ".off"})


# Extensions we just pass through unchanged (only meaningful for GLB target).
_PASSTHROUGH_EXTS: frozenset[str] = frozenset({".glb"})


# Source formats that ada-py can load. Required for any non-GLB target.
_ADA_LOADABLE_EXTS: frozenset[str] = frozenset(
    {".ifc", ".step", ".stp", ".xml", ".gnx", ".inp", ".fem", ".sat", ".acis"}
)


# Genie concept-model sources: the XML and the workspace it is zipped into.
# Loaded by the same reader, so every ".xml"-source fast path applies to both.
_GXML_SOURCE_EXTS: frozenset[str] = frozenset({".xml", ".gnx"})


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
    if fmt not in _target_formats():
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
    if fmt not in _target_formats():
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


# The minimum ``bake_version`` a cached streaming-FEA manifest must carry to
# be served as-is; older (or unstamped) bakes are re-baked so a deck opened
# after an upgrade gains what the newer bake produces (property fields, node
# labels, ...) instead of serving its old artefacts forever. A pinned copy of
# ``ada.fem.results.artefacts.FEA_BAKE_VERSION`` — the slim API container
# cannot import ada.fem — kept equal by a test.
EXPECTED_FEA_BAKE_VERSION = 3


def fea_manifest_stale_reason(
    manifest: dict,
    source_head: dict | None,
    manifest_head: dict | None,
) -> str | None:
    """Why a cached streaming-FEA manifest should be re-baked, or ``None``.

    Two independent signals:

    * the bake predates the current bake output (``bake_version`` below
      :data:`EXPECTED_FEA_BAKE_VERSION`; an unstamped manifest counts as 0);
    * the SOURCE object is newer than the manifest — a deck re-solved and
      re-uploaded under the same name, which the key-only cache would
      otherwise serve stale results for indefinitely.

    ``source_head`` / ``manifest_head`` are ``storage.head()`` dicts
    (``last_modified`` as ISO-8601, possibly None). Unknown or unparsable
    timestamps make only that signal inconclusive — a backend without
    timestamps must not churn every open into a re-bake.
    """

    try:
        baked = int(manifest.get("bake_version") or 0)
    except (TypeError, ValueError):
        baked = 0
    if baked < EXPECTED_FEA_BAKE_VERSION:
        return f"bake_version {baked} < {EXPECTED_FEA_BAKE_VERSION}"

    def _ts(head: dict | None):
        raw = (head or {}).get("last_modified")
        if not raw:
            return None
        from datetime import datetime

        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    src_ts = _ts(source_head)
    man_ts = _ts(manifest_head)
    if src_ts is not None and man_ts is not None:
        try:
            if src_ts > man_ts:
                return "source newer than bake"
        except TypeError:
            # Mixed naive/aware timestamps from different backends —
            # inconclusive, same posture as a missing timestamp.
            pass
    return None


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
# Internal namespaces that are not user files. ONE definition: is_hidden_key answers "is this key
# hidden?", and storage.list(skip_prefixes=...) uses the same tuple to avoid ENUMERATING them —
# a listing that skipped a different set than it filtered would be a silent correctness bug (a real
# file vanishing from the browser), so the two must not drift.
#
# DO NOT add PUBLISHED_ASSET_PREFIX ("assets/") to this tuple. It looks like it belongs — those
# blobs are machine-published rather than hand-uploaded, and a scope can hold thousands of them —
# but they are the *only* record of what has been published: clients project a browsable hierarchy
# out of the key listing returned by GET /api/scopes/{scope}/files, because that route is the sole
# index of the prefix (it returns no per-prefix filter and no pagination, so one listing is the
# whole answer). Hiding them would not degrade such a client, it would silently blank it — every
# published dataset would read as "never published" with no error anywhere. If the listing really
# has to shrink, add a prefix filter to the route rather than making the keys invisible.
HIDDEN_PREFIXES: tuple[str, ...] = (
    "_derived/",
    "_overlays/",
    "_reconvert/",
    "_procedural/",
    "_equipment/",
    "_engines/",
)


def is_hidden_key(key: str) -> bool:
    return key.lstrip("/").startswith(HIDDEN_PREFIXES)


def is_versions_artefact_key(key: str) -> bool:
    """``versions/<branch>/<commit>/<file>`` blobs are pre-built outputs
    pushed by CI rather than conversion sources, so the supported-source
    extension whitelist doesn't apply to them. The ``_derived/`` guard
    still does.
    """
    return key.lstrip("/").startswith("versions/")


# Prefix for published dataset blobs, written under an opaque
# ``assets/<collection>/<subject>/<revision>/<file>`` convention. Core attaches no meaning to any
# segment: they are opaque tokens owned by whoever publishes them.
PUBLISHED_ASSET_PREFIX = "assets/"


def is_published_asset_key(key: str) -> bool:
    """``assets/<collection>/<subject>/<revision>/<file>`` blobs are published
    datasets — index/manifest JSON and packed data files — rather than
    conversion sources, so the supported-source extension whitelist doesn't
    apply to them. The ``_derived/`` guard still does.

    Deliberately a **separate** predicate from :func:`is_versions_artefact_key`
    rather than one more prefix inside it, because the two differ on
    *deletability*, not just on the write gate:

    * ``versions/`` blobs are pushed by CI and are admin-managed for their
      whole life — a user must not be able to remove a build output.
    * These are published by a user, so whoever published them must be able to
      remove them, including (especially) when they published the wrong bytes.

    ``_reject_protected_key`` in :mod:`ada.comms.rest.app` reads
    ``is_versions_artefact_key``, so folding ``assets/`` into it would buy the
    write exemption at the cost of making every published dataset permanently
    undeletable by its own publisher — a one-way door on every byte written.
    """
    return key.lstrip("/").startswith(PUBLISHED_ASSET_PREFIX)


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


_TRUE = {"1", "true", "yes", "on"}


_FALSE = {"0", "false", "no", "off"}


def is_fea_result_key(key: str) -> bool:
    return _ext(key) in _FEA_RESULT_EXTS


def _target_formats() -> frozenset[str]:
    """The facade's ``TARGET_FORMATS`` snapshot (taken once every built-in family has registered),
    read at call time so this module carries no import-time dependency on the facade."""
    from ..converter import TARGET_FORMATS

    return TARGET_FORMATS

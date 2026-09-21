"""Core asset store: a tree-shaped, provider-fed, leaf-addressable asset system.

This package owns the STORAGE half and the provider registry. It deliberately knows nothing
about any provider's source format -- the layering gate in CI greps this package for the
fixture provider's file names and expects nothing.

See ``plan/v4/notes_core_asset_browser.md`` in the planning repo for the design and the
decisions behind each contract.
"""

from __future__ import annotations

from ada.assets.keys import (
    ASSET_KEY_SEGMENTS,
    ASSET_PREFIX,
    STAGING_SEGMENT,
    AssetKey,
    AssetKeyError,
    asset_key,
    parse_asset_key,
    revision_from_instant,
    staging_prefix,
)
from ada.assets.manifest import (
    CORE_ARTEFACT_ROLES,
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA,
    Actor,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
    ChangeRecord,
    ManifestError,
    parse_manifest,
)

__all__ = [
    "ASSET_KEY_SEGMENTS",
    "ASSET_PREFIX",
    "STAGING_SEGMENT",
    "Actor",
    "ArtefactEntry",
    "AssetKey",
    "AssetKeyError",
    "AssetManifest",
    "BuildSpec",
    "CORE_ARTEFACT_ROLES",
    "ChangeRecord",
    "HIERARCHY_FILENAME",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA",
    "ManifestError",
    "asset_key",
    "parse_asset_key",
    "parse_manifest",
    "revision_from_instant",
    "staging_prefix",
]

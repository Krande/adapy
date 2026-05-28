"""Resolve the latest component-preview manifest published by an
ada-build entrypoint.

The downstream connection-library project publishes preview GLBs +
``manifest.json`` to its project scope under the key convention
ada-build enforces::

    versions/<branch>/<commit>/<artefact>

This module finds the newest manifest on a given branch (by storage
mtime) and re-exposes its entries with browser-fetchable
``preview_url`` paths that resolve to the sibling GLB blobs via the
standard ``GET /api/scopes/{scope}/blobs/{key:path}`` route.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage


_VERSIONS_PREFIX = "versions/"


@dataclass(frozen=True)
class ResolvedManifest:
    branch: str
    commit: str
    manifest_key: str
    body: dict


async def resolve_latest_manifest(
    storage: Storage,
    scope: Scope,
    branch: str,
) -> ResolvedManifest | None:
    """Return the newest ``manifest.json`` published on ``branch``.

    Scans every blob under ``versions/<branch>/`` and picks the most
    recently modified ``manifest.json``. Returns ``None`` when nothing
    has been published on the given branch yet.
    """
    branch_prefix = f"{_VERSIONS_PREFIX}{branch}/"
    files = await storage.list(scope)

    candidates = [
        f
        for f in files
        if f.key.startswith(branch_prefix) and f.key.endswith("/manifest.json")
    ]
    if not candidates:
        return None

    # Pick most recently modified; fall back to lexicographic key (commit
    # SHA contents) when mtime is unavailable.
    candidates.sort(key=lambda f: (f.last_modified or "", f.key))
    latest = candidates[-1]

    # versions/<branch>/<commit>/manifest.json -> commit
    parts = latest.key.split("/")
    if len(parts) < 4:
        return None
    commit = parts[2]

    body_bytes = await storage.get_bytes(scope, latest.key)
    body = json.loads(body_bytes.decode("utf-8"))
    return ResolvedManifest(
        branch=branch,
        commit=commit,
        manifest_key=latest.key,
        body=body,
    )


def _scope_url_segment(scope: Scope) -> str:
    """Inverse of `_parse_scope` — the segment we put into a viewer URL."""
    if scope.kind == "shared":
        return "shared"
    if scope.kind in ("project", "corpus"):
        return f"{scope.kind}:{scope.id}"
    if scope.kind == "user":
        return f"user:{scope.id}"
    raise ValueError(f"unknown scope kind {scope.kind!r}")


def expose_manifest(
    resolved: ResolvedManifest,
    scope: Scope,
) -> dict:
    """Augment manifest entries with viewer-resolvable preview URLs.

    The bake's manifest carries ``preview_glb`` as a bare filename
    (sibling of manifest.json). Convert that to a viewer-side URL the
    frontend can ``<img>``/``fetch`` directly.
    """
    base = (
        f"/api/scopes/{_scope_url_segment(scope)}/blobs/"
        f"versions/{resolved.branch}/{resolved.commit}/"
    )
    out_specs: dict[str, dict] = {}
    for name, entry in (resolved.body.get("specs") or {}).items():
        spec_entry = dict(entry)
        preview_filename = spec_entry.get("preview_glb")
        if preview_filename:
            spec_entry["preview_url"] = base + preview_filename
        out_specs[name] = spec_entry

    return {
        "branch": resolved.branch,
        "commit": resolved.commit,
        "manifest_key": resolved.manifest_key,
        "specs": out_specs,
    }

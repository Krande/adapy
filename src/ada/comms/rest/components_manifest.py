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
    branch: str | None,
) -> ResolvedManifest | None:
    """Return the newest ``manifest.json`` published on ``branch``.

    Scans every blob under ``versions/<branch>/`` and picks the most
    recently modified ``manifest.json``. When ``branch`` is None, scans
    every branch under ``versions/`` instead — the newest manifest
    anywhere wins. Returns ``None`` when nothing matches.
    """
    if branch is None:
        prefix = _VERSIONS_PREFIX
    else:
        prefix = f"{_VERSIONS_PREFIX}{branch}/"
    files = await storage.list(scope)

    candidates = [f for f in files if f.key.startswith(prefix) and f.key.endswith("/manifest.json")]
    if not candidates:
        return None

    # Pick most recently modified; fall back to lexicographic key (commit
    # SHA contents) when mtime is unavailable.
    candidates.sort(key=lambda f: (f.last_modified or "", f.key))
    latest = candidates[-1]

    # versions/<branch>/<commit>/manifest.json -> branch, commit
    parts = latest.key.split("/")
    if len(parts) < 4:
        return None
    found_branch = parts[1]
    commit = parts[2]

    body_bytes = await storage.get_bytes(scope, latest.key)
    body = json.loads(body_bytes.decode("utf-8"))
    return ResolvedManifest(
        branch=found_branch,
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

    The bake's top-level ``capability`` (the worker pool that knows
    how to build these specs) is forwarded onto each spec entry so the
    frontend / build endpoint can route the produce-build job to the
    right worker. Falls back to None when the manifest doesn't declare
    one (legacy bakes) — the build endpoint treats that as "let the
    default-pool router pick".
    """
    base = f"/api/scopes/{_scope_url_segment(scope)}/blobs/" f"versions/{resolved.branch}/{resolved.commit}/"
    manifest_capability = resolved.body.get("capability")
    out_specs: dict[str, dict] = {}
    for name, entry in (resolved.body.get("specs") or {}).items():
        spec_entry = dict(entry)
        preview_filename = spec_entry.get("preview_glb")
        if preview_filename:
            spec_entry["preview_url"] = base + preview_filename
        # Per-spec override wins (lets a single project ship specs
        # handled by different worker pools), top-level fallback
        # applies otherwise.
        if "capability" not in spec_entry and manifest_capability is not None:
            spec_entry["capability"] = manifest_capability
        out_specs[name] = spec_entry

    return {
        "branch": resolved.branch,
        "commit": resolved.commit,
        "manifest_key": resolved.manifest_key,
        "capability": manifest_capability,
        "specs": out_specs,
    }

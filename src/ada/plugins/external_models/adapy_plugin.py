"""The backend surface: one job entrypoint fronting every registered provider.

WHY A JOB AND NOT A ROUTE. adapy mounts exactly two routers, both hardcoded;
there is no entry-point, directory-scan or env-var mechanism by which a plugin
contributes FastAPI routes. ``POST /api/plugins/{id}/jobs`` is the whole
server-side surface a plugin gets, so every catalogue read is
enqueue -> poll -> read the derived blob.

That is cheaper than it sounds: adapy hashes ``options`` into the job's
synthetic source key, so an identical repeat request cache-hits a finished job.
A dropdown pays one round-trip per distinct query. Pass a ``refresh`` token to
deliberately miss the cache.

THE PROVIDER OPTION IS THE POINT. Every action but ``list_providers`` takes a
``provider`` id and routes to whatever registered under it — the built-in
``demo`` catalogue, an out-of-tree vendor plugin, anything. A consumer that
hardcodes a provider has thrown away the abstraction; one that lists providers
and passes the chosen id keeps working when the deployment swaps them.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Callable

from ada.plugins.external_models.catalog import ExternalModelCatalog
from ada.plugins.external_models.providers import (
    external_model_providers,
    get_external_model_provider,
)

logger = logging.getLogger(__name__)

__all__ = ["run_job", "ACTIONS", "DEFAULT_PROVIDER"]

ACTIONS = (
    "list_providers",
    "list_collections",
    "list_models",
    "list_model_revisions",
    "model_url",
    "model_upload_url",
)

# Kept so a single-provider deployment need not thread an id through every call.
# Multi-provider deployments should always pass one explicitly.
DEFAULT_PROVIDER = "demo"


def _can_upload(cat: ExternalModelCatalog) -> bool:
    """Does this provider accept models?

    Presence of `model_upload_url` IS the declaration -- see the note in
    catalog.py. Reported alongside every listing so a UI can decide whether to
    offer upload at all, rather than offering it everywhere and letting the
    unlucky provider raise.
    """
    return callable(getattr(cat, "model_upload_url", None))


def _has_revisions(cat: ExternalModelCatalog) -> bool:
    """Does this provider keep more than one version of a model?

    Presence of `list_model_revisions` IS the declaration, exactly as for upload.
    Reported alongside every listing so a UI knows whether to offer a version
    picker before it has asked about any particular model.
    """
    return callable(getattr(cat, "list_model_revisions", None))


def run_job(
    options: dict[str, Any],
    *,
    storage: Any = None,
    scope: Any = None,
    on_progress: Callable[[str, float], None] | None = None,
    derived_prefix: str | None = None,
    cancel_event: Any = None,
    catalog: ExternalModelCatalog | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Serve one external-model action against one provider.

    ``storage`` and ``scope`` are accepted because the worker always passes them,
    but deliberately unused: they are bound to the VIEWER's bucket, and the point
    of this API is to reach storage adapy does not manage. Each provider builds
    its own client.

    ``catalog`` is a test seam that bypasses provider resolution — production
    never passes it.
    """
    options = options or {}
    action = options.get("action")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r} (expected one of {', '.join(ACTIONS)})")

    def _progress(stage: str, frac: float) -> None:
        if on_progress is not None:
            try:
                on_progress(stage, frac)
            except Exception:  # noqa: BLE001 - progress must never sink the job
                logger.debug("external-models: progress callback failed", exc_info=True)

    _progress(action, 0.1)

    if action == "list_providers":
        providers = external_model_providers()
        _progress(action, 0.9)
        return {"action": action, "providers": providers}

    provider = (options.get("provider") or DEFAULT_PROVIDER).strip()
    # KeyError from an unregistered provider carries the registered ids, which is
    # the fastest way to see that a module simply was not preloaded.
    cat = catalog if catalog is not None else get_external_model_provider(provider)

    if action == "list_collections":
        collections = cat.list_collections()
        _progress(action, 0.9)
        return {
            "action": action,
            "provider": provider,
            "can_upload": _can_upload(cat),
            "has_revisions": _has_revisions(cat),
            "collections": [asdict(c) for c in collections],
        }

    collection = (options.get("collection") or "").strip()
    if not collection:
        raise ValueError(f"action {action!r} requires a 'collection' option")

    if action == "list_models":
        models = cat.list_models(collection)
        _progress(action, 0.9)
        return {
            "action": action,
            "provider": provider,
            "collection": collection,
            "can_upload": _can_upload(cat),
            "has_revisions": _has_revisions(cat),
            "models": [asdict(m) for m in models],
        }

    model_id = (options.get("model_id") or "").strip()
    if not model_id:
        raise ValueError(f"action {action!r} requires a 'model_id' option")

    if action == "list_model_revisions":
        lister = getattr(cat, "list_model_revisions", None)
        if not callable(lister):
            # Not a fault in the request: this catalogue keeps one version of a
            # model. An empty list says "nothing to pick from" in the same shape
            # a versioned provider uses for an unversioned model, so a caller
            # needs no branch.
            _progress(action, 0.9)
            return {
                "action": action,
                "provider": provider,
                "collection": collection,
                "model_id": model_id,
                "has_revisions": False,
                "revisions": [],
            }
        revisions = lister(collection, model_id)
        _progress(action, 0.9)
        return {
            "action": action,
            "provider": provider,
            "collection": collection,
            "model_id": model_id,
            "has_revisions": True,
            "revisions": [asdict(r) for r in revisions],
        }

    if action == "model_upload_url":
        signer = getattr(cat, "model_upload_url", None)
        if not callable(signer):
            # Not a fault in the request. This provider does not accept models,
            # and saying which one refuses is more use than "unsupported".
            raise ValueError(
                f"provider {provider!r} does not accept uploads; it publishes through its own "
                "pipeline, so there is nothing for the viewer to upload to"
            )
        url = signer(
            collection,
            model_id,
            expires_in_seconds=int(options.get("expires_in_seconds") or 900),
            content_type=(options.get("content_type") or None),
        )
        # How the provider wants it stored. The uploader obeys these rather
        # than choosing for itself -- see the note in catalog.py on why the
        # content type and the encoding have to travel together.
        put_headers: dict[str, str] = {}
        header_getter = getattr(cat, "model_upload_headers", None)
        if callable(header_getter):
            put_headers = dict(header_getter(collection, model_id) or {})

        _progress(action, 0.9)
        # Short-lived write credential. The payload, and never logged.
        return {
            "action": action,
            "provider": provider,
            "collection": collection,
            "model_id": model_id,
            "url": url,
            "method": "PUT",
            "headers": put_headers,
        }

    expires = int(options.get("expires_in_seconds") or 900)
    revision = (options.get("revision") or "").strip()
    if revision:
        # Passed ONLY when asked for, so a provider that predates revisions never
        # sees the keyword. A provider that lists revisions but does not accept
        # this raises TypeError here rather than silently serving the current
        # version, which is the failure worth being loud about.
        url = cat.model_download_url(collection, model_id, expires_in_seconds=expires, revision=revision)
    else:
        url = cat.model_download_url(collection, model_id, expires_in_seconds=expires)

    # A provider whose URL carries its own signature needs no headers; one whose
    # fetch must be authenticated returns them. getattr rather than a required
    # Protocol method, so a provider written before this keeps working.
    headers: dict[str, str] = {}
    getter = getattr(cat, "model_download_headers", None)
    if callable(getter):
        headers = dict(getter(collection, model_id) or {})

    _progress(action, 0.9)
    # URL and headers are short-lived credentials; they are the payload, so they
    # necessarily reach the browser. Never logged.
    return {
        "action": action,
        "provider": provider,
        "collection": collection,
        "model_id": model_id,
        "revision": revision or None,
        "url": url,
        "headers": headers,
    }

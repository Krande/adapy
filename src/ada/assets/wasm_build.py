"""Build one asset node in the browser -- the Pyodide twin of the worker's ``asset_build`` handler.

The viewer's Pyodide worker installs the plugin wheel that registers the builder for a capability
(its ``register`` hook, ``plugin-wheels-manifest.json``), then calls :func:`run` with the build
block ``POST /assets/build`` answered for a browser build (``execution: "browser"``) and a
synchronous storage facade that reads through the API and keeps what the builder writes.

It does what ``ada.comms.rest.formats.asset_build`` does on a worker, minus the queue:

1. the builder registered for the CAPABILITY (never for a provider id),
2. called with the derived prefix CORE composed, so it writes where it was told,
3. its summary validated against the request -- a summary that answers another request never
   leaves the browser,
4. a builder that finds nothing to draw (``NothingToBuild``) answers an ``empty`` summary, as the
   worker stores one.

The answer is the summary document; the files the builder wrote are the facade's. The viewer
uploads both (``POST /assets/build/result``), and the server validates them again before they land
in shared storage: this side is a convenience that catches a builder bug early, the server's
check is the one that counts.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from ada.assets.build import (
    BuildError,
    BuildProvenance,
    BuildSummary,
    NothingToBuild,
    parse_build_summary,
    validate_build_summary,
)
from ada.assets.builders import (
    BuildRequest,
    asset_builder,
    invoke_builder,
    unhonoured_exclusions_warning,
)

__all__ = ["BROWSER_SCOPE", "BROWSER_ENTRYPOINT", "empty_build_summary", "request_of", "run", "run_job"]

#: The entrypoint the viewer runs a browser build through (``run-plugin-job``).
BROWSER_ENTRYPOINT = "ada.assets.wasm_build:run_job"

#: The fields of the build block the request is made of (``POST /assets/build``'s ``build``).
_REQUEST_FIELDS = ("provider", "collection", "subject", "revision", "fingerprint", "hierarchy_source")


@dataclasses.dataclass(frozen=True)
class _BrowserScope:
    """Stands in for the worker's ``Scope``: builders take one and the shipped ones ignore it."""

    wire: str

    def prefix(self) -> str:
        return self.wire


#: What a builder gets as ``scope`` when no scope is named.
BROWSER_SCOPE = _BrowserScope("browser")


def request_of(build: Mapping[str, Any]) -> BuildRequest:
    """The :class:`BuildRequest` of a build block; raises :class:`BuildError` naming what is missing."""
    missing = [f for f in (*_REQUEST_FIELDS, "capability", "derived_prefix") if not build.get(f)]
    if missing:
        raise BuildError(f"the build block lacks {', '.join(missing)}")
    return BuildRequest(
        provider=str(build["provider"]),
        collection=str(build["collection"]),
        subject=str(build["subject"]),
        revision=str(build["revision"]),
        node=build.get("node"),
        fingerprint=str(build["fingerprint"]),
        hierarchy_source=str(build["hierarchy_source"]),
    )


def empty_build_summary(request: BuildRequest, reason: str, *, provider_version: str | None = None) -> BuildSummary:
    """The ``empty`` summary of a build that found nothing to draw (the worker's ``_store_empty``)."""
    return BuildSummary(
        ok=False,
        glb_key="",
        provenance=BuildProvenance(
            provider=request.provider,
            collection=request.collection,
            subject=request.subject,
            revision=request.revision,
            node=request.node,
            fingerprint=request.fingerprint,
            built_at=datetime.now(timezone.utc).isoformat(),
            hierarchy_source=request.hierarchy_source,
            provider_version=provider_version,
        ),
        error=reason,
        empty=True,
    )


def run(
    build: Mapping[str, Any] | str,
    *,
    storage: Any,
    scope: Any = None,
    on_progress: Callable[[str, float], None] | None = None,
    cancel_event: Any | None = None,
) -> dict:
    """Run the build block's builder against ``storage`` and answer its validated summary document."""
    if isinstance(build, str):
        build = json.loads(build)
    request = request_of(build)
    derived_prefix = str(build["derived_prefix"]).rstrip("/")
    builder = asset_builder(str(build["capability"]))
    exclude_nodes = sorted({str(n) for n in build.get("exclude_nodes") or ()})
    try:
        result, honoured = invoke_builder(
            builder,
            dict(build.get("options") or {}),
            exclude_nodes=exclude_nodes,
            request=request,
            storage=storage,
            scope=scope if scope is not None else BROWSER_SCOPE,
            derived_prefix=derived_prefix,
            on_progress=on_progress,
            cancel_event=cancel_event if cancel_event is not None else threading.Event(),
        )
    except NothingToBuild as exc:
        return empty_build_summary(request, str(exc), provider_version=build.get("provider_version")).to_dict()
    summary = result if isinstance(result, BuildSummary) else parse_build_summary(result)
    validate_build_summary(
        summary,
        provider=request.provider,
        collection=request.collection,
        subject=request.subject,
        revision=request.revision,
        node=request.node,
        fingerprint=request.fingerprint,
        derived_prefix=derived_prefix,
    )
    if not honoured:
        summary = dataclasses.replace(
            summary, warnings=(*summary.warnings, unhonoured_exclusions_warning(exclude_nodes))
        )
    return summary.to_dict()


def run_job(
    options: Mapping[str, Any],
    *,
    storage: Any,
    scope: Any = None,
    on_progress: Callable[[str, float], None] | None = None,
    derived_prefix: str | None = None,
    cancel_event: Any | None = None,
) -> dict:
    """:func:`run` in the shape of a plugin job entrypoint, so the viewer's Pyodide worker runs a
    build as it runs a plugin job (``run-plugin-job`` with this as the entrypoint, the build
    capability as the plugin whose wheel it installs): ``options`` is the build block. The prefix
    is the block's, never the caller's ``derived_prefix``."""
    del derived_prefix
    return run(options, storage=storage, scope=scope, on_progress=on_progress, cancel_event=cancel_event)

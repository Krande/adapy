"""Which package can read a provider's format into ``ada`` objects, and the node -> ``Part`` path.

THE GAP THIS CLOSES. A clash check needs members and a detail hand-off needs the real
``Beam``/``Plate`` objects (``ada.clash.identify_joints``; ``formats/clash_detail.py``'s "REAL
MEMBERS, NOT DESCRIPTIONS"). Both get a model by downloading a source file and dispatching on its
EXTENSION -- IFC, STEP, Genie-XML, FEM, ACIS. A provider with a private format has no extension
core knows, and teaching core the format is the one thing the layering forbids. So the provider
reads it and hands back core's objects.

REGISTERED BY PROVIDER ID, unlike a builder. A build is a JOB and routes on a capability token,
which is why the licensed export host can advertise one and refuse the other. Reading a node into
objects is not a job: it happens inside whatever process already needs the model, and the only
question is whether that process has the package installed. In practice one worker image carries
every provider, so this is a lookup rather than a routing decision -- and where a process does NOT
have it, `asset_concepts` says so by name instead of returning something empty.

THE OPTIONS ARE THE NODE'S OWN BUILD OPTIONS. Nothing new is recorded at publish time: a node that
claims ``build`` already carries the opaque mapping its provider needs to find its source and its
spine, and this passes that mapping straight through. A provider whose build scopes to the
published spine scopes this identically, because it is the same code without the tessellation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from ada.assets.provider import AssetConcepts
from ada.config import logger

__all__ = [
    "AssetConceptsError",
    "asset_concepts",
    "clear_asset_concepts",
    "concept_providers",
    "part_for_manifest",
    "register_asset_concepts",
]


class AssetConceptsError(LookupError):
    """No concepts reader for a provider id, or two different ones claiming it."""


@dataclass(frozen=True)
class _Entry:
    factory: Callable[[], AssetConcepts]
    origin: str
    label: str | None
    available: Callable[[], bool] | None


_LOCK = threading.RLock()
_READERS: dict[str, _Entry] = {}


def _origin_of(factory: Callable[[], Any]) -> str:
    module = getattr(factory, "__module__", "?")
    qualname = getattr(factory, "__qualname__", getattr(factory, "__name__", "?"))
    return f"{module}:{qualname}"


def register_asset_concepts(
    provider_id: str,
    factory: Callable[[], AssetConcepts],
    *,
    label: str | None = None,
    available: Callable[[], bool] | None = None,
) -> None:
    """Register (or re-register) the concepts reader for ``provider_id``.

    Idempotent for the same origin, and a conflict is judged on ``module:qualname`` rather than on
    function identity -- the same rule the other three registries use, and for the same reason: a
    ``register()`` that closes over anything returns a new function object per call, so identity
    would refuse exactly the case this tolerates (an entry point loaded twice, by discovery and by
    an explicit preload).
    """
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise AssetConceptsError("a provider id must be a non-empty string")
    origin = _origin_of(factory)
    with _LOCK:
        existing = _READERS.get(provider_id)
        if existing is not None and existing.origin != origin:
            raise AssetConceptsError(
                f"provider {provider_id!r} already has a concepts reader registered by "
                f"{existing.origin}; {origin} may not take it over -- which one answered would "
                f"depend on import order"
            )
        _READERS[provider_id] = _Entry(factory=factory, origin=origin, label=label, available=available)


def asset_concepts(provider_id: str) -> AssetConcepts:
    """The reader for ``provider_id``, or an error naming what this process does have."""
    with _LOCK:
        entry = _READERS.get(provider_id)
        known = ", ".join(sorted(_READERS)) or "none"
    if entry is None:
        raise AssetConceptsError(
            f"provider {provider_id!r} cannot be read into objects in this process (known: {known}). "
            f"A clash check over its nodes has to run where that provider is installed."
        )
    if entry.available is not None and not entry.available():
        raise AssetConceptsError(
            f"provider {provider_id!r} has a concepts reader registered, and it reports itself "
            f"unavailable here -- a dependency it needs to read the format is missing"
        )
    return entry.factory()


def concept_providers() -> list[dict]:
    """What this process can read, for a diagnostics surface. Availability is asked, not assumed."""
    out: list[dict] = []
    with _LOCK:
        items = sorted(_READERS.items())
    for provider_id, entry in items:
        available = True
        error = None
        if entry.available is not None:
            try:
                available = bool(entry.available())
            except Exception as exc:  # noqa: BLE001 - a broken probe must be visible, not fatal
                available, error = False, f"{type(exc).__name__}: {exc}"
        row = {"id": provider_id, "label": entry.label or provider_id, "available": available}
        if error:
            row["error"] = error
        out.append(row)
    return out


def clear_asset_concepts() -> None:
    """Test hook. Registration is process-global, so a test that registers must clean up."""
    with _LOCK:
        _READERS.clear()


def part_for_manifest(manifest: Any, *, storage: Any, scope: Any = None, node: str | None = None):
    """The node's concept objects, from the manifest that speaks for it.

    ``manifest`` is a parsed :class:`ada.assets.manifest.AssetManifest`. Its ``provider`` says who
    can read the source and its ``build.options`` say where the source is, so nothing else has to
    be passed and nothing extra has to have been published.

    A subject that claims no build has no options to read with, and that is refused rather than
    guessed at: a provider asked to produce objects with no source named would have to invent the
    scope, and a clash check over an invented scope is worse than one that did not run.
    """
    spec = getattr(manifest, "build", None)
    if spec is None:
        raise AssetConceptsError(
            f"subject {getattr(manifest, 'subject', '?')!r} claims delivery "
            f"{getattr(manifest, 'delivery', '?')!r} and carries no build options -- there is "
            f"nothing here naming the source to read, so there is no scope to read it at"
        )
    reader = asset_concepts(manifest.provider)
    part = reader.concepts(
        dict(spec.options),
        storage=storage,
        scope=scope,
        node=node or getattr(manifest, "node", None) or manifest.subject,
    )
    if part is None:
        raise AssetConceptsError(f"provider {manifest.provider!r} returned no objects for subject {manifest.subject!r}")
    logger.debug("assets: read %s/%s as concepts via %s", manifest.collection, manifest.subject, manifest.provider)
    return part

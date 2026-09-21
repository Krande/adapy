"""Provider registry -- factory-registered, idempotent by id.

Deliberately the same shape as the existing external-model provider registry, so a provider
registers from its ``ada.plugins`` entry-point ``register()`` with ZERO core edits. Factories
rather than instances: registration happens at import time, in a worker that may never serve a
single asset request, and constructing a provider can mean opening a catalogue client.

Idempotent by id because registration runs from an entry point that can legitimately be imported
twice (the plugin loader and an explicit ``ADA_WORKER_PRELOAD``). Re-registering the SAME factory
is a no-op; re-registering a DIFFERENT one for a live id is an error, because silently keeping
either one makes which provider answered depend on import order.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from ada.assets.provider import AssetTreeProvider, provider_capabilities

__all__ = [
    "AssetProviderError",
    "asset_provider",
    "asset_providers",
    "clear_asset_providers",
    "register_asset_provider",
    "registered_provider_ids",
]


class AssetProviderError(LookupError):
    """No such provider, or a conflicting registration."""


@dataclass(frozen=True)
class _Registration:
    id: str
    factory: Callable[[], AssetTreeProvider]
    label: str | None


_LOCK = threading.RLock()
_REGISTRY: dict[str, _Registration] = {}
_INSTANCES: dict[str, Any] = {}


def register_asset_provider(
    provider_id: str, factory: Callable[[], AssetTreeProvider], *, label: str | None = None
) -> None:
    if not provider_id or "/" in provider_id:
        raise AssetProviderError(
            f"invalid provider id {provider_id!r}: non-empty, and no '/' (it rides in a route path)"
        )
    with _LOCK:
        existing = _REGISTRY.get(provider_id)
        if existing is not None and existing.factory is not factory:
            raise AssetProviderError(
                f"provider id {provider_id!r} is already registered with a different factory "
                f"({existing.factory!r} vs {factory!r}). Which one answered would depend on import "
                f"order, so this is refused rather than resolved silently."
            )
        _REGISTRY[provider_id] = _Registration(id=provider_id, factory=factory, label=label)


def asset_provider(provider_id: str) -> AssetTreeProvider:
    """Get (and memoise) the provider instance for an id."""
    with _LOCK:
        if provider_id in _INSTANCES:
            return _INSTANCES[provider_id]
        reg = _REGISTRY.get(provider_id)
        if reg is None:
            known = ", ".join(sorted(_REGISTRY)) or "<none registered>"
            raise AssetProviderError(f"no asset provider {provider_id!r}; registered: {known}")
        instance = reg.factory()
        _INSTANCES[provider_id] = instance
        return instance


def registered_provider_ids() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_REGISTRY))


def asset_providers() -> list[dict]:
    """The tab's provider filter: id, label, and what each one can actually do.

    Instantiates each provider, because capabilities are read off the object. A provider whose
    factory raises is reported with its error rather than omitted -- a silently missing provider
    looks identical to one that was never installed.
    """
    out: list[dict] = []
    for provider_id in registered_provider_ids():
        with _LOCK:
            reg = _REGISTRY[provider_id]
        entry: dict[str, Any] = {"id": provider_id, "label": reg.label or provider_id}
        try:
            caps = provider_capabilities(asset_provider(provider_id))
        except Exception as exc:  # noqa: BLE001 - a broken provider must be visible, not absent
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["delivery"] = []
            out.append(entry)
            continue
        entry["live"] = caps["tree"]
        # Which delivery kinds this provider can CLAIM is the provider's to declare, not something
        # core can infer: `mesh` vs `build` is a per-node claim, and a provider may serve both. An
        # undeclared provider reports [] rather than a guess -- the tab shows no filter instead of
        # a wrong one.
        entry["delivery"] = _declared_delivery(provider_id)
        entry["capabilities"] = sorted(k for k, v in caps.items() if v)
        out.append(entry)
    return out


_DELIVERY_KINDS = ("mesh", "build")


def _declared_delivery(provider_id: str) -> list[str]:
    declared = getattr(asset_provider(provider_id), "delivery_kinds", ())
    unknown = [k for k in declared if k not in _DELIVERY_KINDS]
    if unknown:
        raise AssetProviderError(
            f"provider {provider_id!r} declares delivery kind(s) {unknown} -- core knows only {list(_DELIVERY_KINDS)}"
        )
    return [k for k in _DELIVERY_KINDS if k in declared]


def clear_asset_providers() -> None:
    """Test hook. Registration is process-global, so a test that registers must clean up."""
    with _LOCK:
        _REGISTRY.clear()
        _INSTANCES.clear()

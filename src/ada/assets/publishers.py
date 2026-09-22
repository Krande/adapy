"""Which publisher accepts a publish for a provider id.

Same shape and the same conflict rule as ``ada.assets.builders`` (Decision 20: origin, not
identity). Separate from the TREE provider registry on purpose: publishing is an optional
capability (Decision 1 -- "presence IS the declaration"), and a provider that only publishes
never needs to answer a hierarchy request, because it rides ``PublishedAssetProvider``.

Unlike a builder, a publisher is resolved by PROVIDER ID rather than by a capability token: a
publish names the provider whose format the staged bytes are in, and that provider's derivation
is the one thing no other provider can do.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable

from ada.assets.provider import AssetPublisher
from ada.config import logger

__all__ = [
    "AssetPublisherError",
    "asset_publisher",
    "clear_asset_publishers",
    "ensure_core_publishers",
    "register_asset_publisher",
    "registered_publisher_ids",
]


class AssetPublisherError(LookupError):
    """No publisher for a provider id, or two different ones claiming it."""


@dataclass(frozen=True)
class _Entry:
    factory: Callable[[], AssetPublisher]
    origin: str
    label: str | None


_PUBLISHERS: dict[str, _Entry] = {}
_CORE_LOADED = False


def _origin_of(factory: Callable[[], AssetPublisher]) -> str:
    module = getattr(factory, "__module__", "?")
    qualname = getattr(factory, "__qualname__", getattr(factory, "__name__", "?"))
    return f"{module}:{qualname}"


def register_asset_publisher(
    provider_id: str,
    factory: Callable[[], AssetPublisher],
    *,
    label: str | None = None,
) -> None:
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise AssetPublisherError("a provider id must be a non-empty string")
    origin = _origin_of(factory)
    existing = _PUBLISHERS.get(provider_id)
    if existing is not None and existing.origin != origin:
        raise AssetPublisherError(
            f"provider {provider_id!r} already has a publisher registered by {existing.origin}; "
            f"{origin} may not take it over"
        )
    _PUBLISHERS[provider_id] = _Entry(factory=factory, origin=origin, label=label)


def asset_publisher(provider_id: str) -> AssetPublisher:
    ensure_core_publishers()
    entry = _PUBLISHERS.get(provider_id)
    if entry is None:
        known = ", ".join(sorted(_PUBLISHERS)) or "none"
        raise AssetPublisherError(
            f"provider {provider_id!r} accepts no publish in this process (known: {known}). "
            f"A publish needs the provider that understands the staged format."
        )
    return entry.factory()


def registered_publisher_ids() -> list[str]:
    ensure_core_publishers()
    return sorted(_PUBLISHERS)


def clear_asset_publishers() -> None:
    """Test hook. Core publishers re-register on the next call that needs them."""
    global _CORE_LOADED
    _PUBLISHERS.clear()
    _CORE_LOADED = False


def ensure_core_publishers() -> None:
    """Register the publishers core ships. Lazy for the same reason as the builders: the IFC one
    pulls ifcopenshell, and a process that never publishes IFC must not pay for it."""
    global _CORE_LOADED
    if _CORE_LOADED:
        return
    _CORE_LOADED = True  # set first: a failing import must not retry on every call
    try:
        importlib.import_module("ada.assets.ifc")
    except Exception as exc:  # pragma: no cover - a broken core import is a bug, not a config
        logger.warning("core asset publishers unavailable: %s", exc)

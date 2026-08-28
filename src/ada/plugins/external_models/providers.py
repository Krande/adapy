"""The provider registry — the wrapper half of the external-model API.

Core owns the *interface* for "list and retrieve models that live somewhere
adapy does not manage"; it owns no opinion about where that is. The built-in
``demo`` provider — an object-store catalogue — ships in this package. Any
out-of-tree plugin wrapping a third-party asset API registers the same way and
gets no special treatment.

A consumer (a models dropdown, or another plugin's panel) asks core for a
provider by id and calls the same three methods either way. That is the whole
point: swapping one catalogue for another must not require the consumer to
change, and must not require both to be present in the same bundle.

Registration takes a FACTORY, not an instance, so importing a provider module
never constructs an S3 client or reads credentials. Nothing touches the network
until someone actually asks for that provider.
"""

from __future__ import annotations

import logging
from typing import Callable

from ada.plugins.external_models.catalog import ExternalModelCatalog

logger = logging.getLogger(__name__)

__all__ = [
    "register_external_model_provider",
    "unregister_external_model_provider",
    "external_model_provider_ids",
    "external_model_providers",
    "get_external_model_provider",
    "reset_providers",
]

# provider id -> (label, factory). Insertion-ordered; idempotent by id, so a
# re-imported preload module replaces its own entry rather than duplicating it
# (the same contract ada.plugins.register_plugin_backend offers).
_PROVIDERS: dict[str, tuple[str, Callable[[], ExternalModelCatalog]]] = {}

# Resolved instances, so a provider's client is built once per process.
_INSTANCES: dict[str, ExternalModelCatalog] = {}


def register_external_model_provider(
    provider_id: str,
    factory: Callable[[], ExternalModelCatalog],
    *,
    label: str | None = None,
) -> None:
    """Register (or replace) an external-model provider.

    ``factory`` is called at most once, the first time the provider is used.
    """
    if not provider_id or not isinstance(provider_id, str):
        raise ValueError("provider_id must be a non-empty string")
    if not callable(factory):
        raise TypeError("factory must be callable")
    _PROVIDERS[provider_id] = (label or provider_id, factory)
    # A replaced provider must not keep serving its old instance.
    _INSTANCES.pop(provider_id, None)


def unregister_external_model_provider(provider_id: str) -> None:
    _PROVIDERS.pop(provider_id, None)
    _INSTANCES.pop(provider_id, None)


def external_model_provider_ids() -> list[str]:
    return list(_PROVIDERS)


def external_model_providers() -> list[dict]:
    """``[{"id", "label"}]`` — what a UI needs to offer a provider picker."""
    return [{"id": pid, "label": label} for pid, (label, _) in _PROVIDERS.items()]


def get_external_model_provider(provider_id: str) -> ExternalModelCatalog:
    """Resolve a provider, constructing it on first use.

    Raises ``KeyError`` naming the registered ids, because the common failure is
    a deployment that simply did not preload the provider's module — and the
    list is the fastest way to see that.
    """
    if provider_id in _INSTANCES:
        return _INSTANCES[provider_id]
    entry = _PROVIDERS.get(provider_id)
    if entry is None:
        known = ", ".join(_PROVIDERS) or "<none registered>"
        raise KeyError(f"unknown external-model provider {provider_id!r} (registered: {known})")
    _, factory = entry
    instance = factory()
    _INSTANCES[provider_id] = instance
    return instance


def reset_providers() -> None:
    """Test hook — drops every registration and cached instance."""
    _PROVIDERS.clear()
    _INSTANCES.clear()

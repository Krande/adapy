"""The simulation providers this process registered.

A provider is registered like any other plugin backend: its module is listed in
``ADA_WORKER_PRELOAD`` or reached through an ``ada.plugins`` entry point, and registering is an
import side effect of that module. Registering also asks the worker running this process to
serve the provider's capability (``sim-<provider id>``), through the same request a plugin makes
for any pool it discovers after boot, so the capability goes through the worker's qualification
and its disabled-capabilities switch like every other one.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .interfaces import (
    PROVIDER_ID_PATTERN,
    SimulationProvider,
    provider_capability,
    simulation_domain,
)

logger = logging.getLogger(__name__)

__all__ = [
    "declare_storage_read_prefixes",
    "get_simulation_provider",
    "register_simulation_provider",
    "reset_simulation_providers",
    "simulation_provider_specs",
    "simulation_providers",
    "storage_read_prefixes",
    "unregister_simulation_provider",
]

# provider id -> provider. Insertion-ordered; a second registration of an id replaces the first,
# as a re-imported preload module replaces its own plugin spec. Locked: a provider may be
# registered from a job thread while the worker's heartbeat reads the list.
_PROVIDERS: dict[str, SimulationProvider] = {}
_LOCK = threading.Lock()


def register_simulation_provider(provider: SimulationProvider) -> None:
    """Register (or replace) a provider and ask this process's worker to serve its runs.

    Idempotent by ``provider.id``. ``provider.domain`` must be one of
    ``ada.simulation.SIMULATION_DOMAINS``; anything else is a ValueError. In a process with no worker loop (the API of a viewer
    without a queue, a test) the capability request is simply never read, and the provider runs
    in-process.
    """
    for attr in ("id", "domain", "engine"):
        value = getattr(provider, attr, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"a simulation provider needs a non-empty string {attr!r}")
    if not PROVIDER_ID_PATTERN.match(provider.id):
        raise ValueError(
            f"simulation provider id {provider.id!r} must be lower-case letters, digits, '-' or '_' "
            "(at most 40 characters): it becomes part of a queue subject"
        )
    # A closed set: ValueError naming the known domains for anything else ("robot", a typo).
    simulation_domain(provider.domain)
    for method in ("describe", "run"):
        if not callable(getattr(provider, method, None)):
            raise TypeError(f"a simulation provider needs a {method}() method")
    with _LOCK:
        _PROVIDERS[provider.id] = provider
    # Imported here: ada.plugins re-exports this function, so the import cannot be at module level.
    from ada.plugins import request_worker_capabilities

    request_worker_capabilities([provider_capability(provider.id)])


def unregister_simulation_provider(provider_id: str) -> None:
    """Forget a provider. The worker keeps the pool it already serves: a run that still arrives
    for it fails by name instead of waiting on a pool nobody pulls from."""
    with _LOCK:
        _PROVIDERS.pop(provider_id, None)


def get_simulation_provider(provider_id: str) -> SimulationProvider | None:
    with _LOCK:
        return _PROVIDERS.get(provider_id)


def simulation_providers() -> list[SimulationProvider]:
    """The registered providers, in registration order."""
    with _LOCK:
        return list(_PROVIDERS.values())


def simulation_provider_specs() -> list[dict[str, Any]]:
    """Each registered provider as the plain dict a worker advertises and the API lists:
    ``id``, ``domain``, ``engine``, ``capability`` and what ``describe()`` returned.

    A provider whose ``describe()`` raises is logged and left out: one broken provider must not
    take the others off the list.
    """
    out: list[dict[str, Any]] = []
    for provider in simulation_providers():
        try:
            description = provider.describe().to_dict()
        except Exception:
            logger.exception("ada.simulation: describe() of provider %s failed (not advertised)", provider.id)
            continue
        spec = {
            "slug": provider.id,
            "id": provider.id,
            "domain": provider.domain,
            "engine": provider.engine,
            "capability": provider_capability(provider.id),
            **description,
        }
        gate = getattr(provider, "access_gate", None)
        if isinstance(gate, str) and gate.strip():
            # The scope-gated plugin the provider belongs to: its runs are refused wherever an admin
            # has not turned that plugin on (``ada.comms.rest.plugin_access``).
            spec["access_gate"] = gate.strip().lower()
        out.append(spec)
    return out


#: provider id -> storage key prefixes its runs read beside their own inputs (declared in code).
_READ_PREFIXES: dict[str, tuple[str, ...]] = {}


def declare_storage_read_prefixes(provider_id: str, prefixes) -> None:
    """Say which storage prefixes of a run's scope a provider's runs read beside the run's own
    inputs (shared assets such as ``assets/robots/``).

    It matters to a remote worker that holds no storage keys: its job grant lets it read the run's
    record, inputs and cancel marker, and these prefixes, nothing else (``ada.comms.rest.job_grants``).
    The API computes that, so the declaration has to be made in the API process too: in the
    provider's module (which the API imports when the plugin is installed there), or in a small
    module of its own the API preloads. A deployment can add prefixes without code
    (``ADA_VIEWER_WORKER_GRANT_READ_PREFIXES``). A provider object may instead carry a
    ``storage_read_prefixes`` attribute; both are read. Replaces an earlier declaration."""
    if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.match(provider_id):
        raise ValueError(f"not a simulation provider id: {provider_id!r}")
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    cleaned = tuple(str(p) for p in prefixes if isinstance(p, str) and p.strip())
    with _LOCK:
        _READ_PREFIXES[provider_id] = cleaned


def storage_read_prefixes(provider_id: str) -> tuple[str, ...]:
    """The read prefixes declared for ``provider_id`` (``declare_storage_read_prefixes``), and those
    of a registered provider's ``storage_read_prefixes`` attribute. Unchecked: the caller that
    grants by them checks each."""
    with _LOCK:
        declared = _READ_PREFIXES.get(provider_id, ())
        provider = _PROVIDERS.get(provider_id)
    own = getattr(provider, "storage_read_prefixes", ()) if provider is not None else ()
    if isinstance(own, str):
        own = (own,)
    return tuple(dict.fromkeys([*declared, *(p for p in (own or ()) if isinstance(p, str))]))


def reset_simulation_providers() -> None:
    """Clear the registry -- for tests only."""
    with _LOCK:
        _PROVIDERS.clear()
        _READ_PREFIXES.clear()

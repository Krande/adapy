"""One door to the in-process backend-plugin registry (``ada.plugins``).

The API and the worker both need the plugins THIS process registered — via
``ADA_WORKER_PRELOAD`` or the ``ada.plugins`` entry-point group — for two
things: discovery at boot, and reading a plugin's advertised spec (the worker
puts it on its heartbeat; a queue-less viewer runs the plugin's jobs itself and
lists/gates them from the registry, since no worker ever advertises them).

Every access goes through here so the ``ada`` import stays defensive in ONE
place: the slim API image may not carry ``ada.plugins``, and an unavailable
registry means "nothing registered here", never a crash. The module is looked
up (not its functions) so a test that monkeypatches ``ada.plugins`` attributes
sees the patch.
"""

from __future__ import annotations

from types import ModuleType
from typing import Literal

from ada.config import logger


def local_plugin_registry() -> ModuleType | None:
    """The ``ada.plugins`` module when it is importable in this process, else
    ``None`` (the slim runtime without the registry)."""
    try:
        from ada import plugins
    except Exception:
        return None
    return plugins


def discover_local_plugins(role: Literal["api", "worker"]) -> None:
    """Run the ``ada.plugins`` entry-point discovery, logging (not raising) on
    failure. Isolated per plugin inside ``discover_plugins`` itself; this wraps
    the registry being absent or discovery blowing up as a whole. ``role``
    prefixes the log line so the API's and the worker's boots stay tellable
    apart."""
    try:
        registry = local_plugin_registry()
        if registry is None:
            raise ImportError("ada.plugins is not importable in this process")
        registry.discover_plugins()
    except Exception:
        logger.exception("%s: ada.plugins discovery failed (non-fatal)", role)


def locally_registered_specs() -> list[dict]:
    """Every backend-plugin spec this process registered itself; ``[]`` when the
    registry is unavailable or the listing fails (logged)."""
    registry = local_plugin_registry()
    if registry is None:
        return []
    try:
        return registry.plugin_backend_specs()
    except Exception:
        logger.exception("failed to list locally registered backend plugins (non-fatal)")
        return []


def locally_registered_spec(plugin_id: str) -> dict | None:
    """The spec this process registered for ``plugin_id``, or ``None`` — both
    for "not registered" and for "no registry here", which read the same to a
    caller: no declaration seen."""
    registry = local_plugin_registry()
    if registry is None:
        return None
    try:
        return registry.plugin_backend_spec(plugin_id)
    except Exception:
        return None

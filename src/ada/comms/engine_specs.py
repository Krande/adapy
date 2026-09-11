"""Re-export of the engine-capability-spec predicate for the REST layer.

The canonical definition lives in :mod:`ada.core.engine_specs` — a module
below both ``ada.comms`` and ``ada.topo_model`` in the dependency graph, so
neither side has to import the other to share it. This module exists so
existing importers of ``ada.comms.engine_specs.is_offerable`` (the REST API,
which runs in a slim runtime that cannot import ``ada.topo_model``) keep
working unchanged.
"""

from __future__ import annotations

from ada.core.engine_specs import is_offerable

__all__ = ["is_offerable"]

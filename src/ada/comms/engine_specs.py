"""Predicates over the engine capability specs workers announce in their heartbeat.

This lives under ``ada.comms`` rather than next to the registry that writes the
specs (``ada.topo_model.engine_catalog``) for one reason: the REST API runs in a
*slim* runtime that ships only a subset of the package -- ``ada.comms``,
``ada.cad``, ``ada.config`` and part of ``ada.sections`` (see
``deploy/Dockerfile.viewer``). ``ada.topo_model`` is not in that subset, and
cannot be: importing any of its submodules executes its ``__init__``, which pulls
in the whole modelling stack.

So a spec predicate the API needs must be reachable without ``ada.topo_model``.
``engine_catalog`` re-exports it, keeping one definition for both sides.

Keep this module dependency-free (stdlib only) -- that is what makes it safe to
ship into the slim runtime.
"""

from __future__ import annotations

__all__ = ["is_offerable"]


def is_offerable(spec: dict) -> bool:
    """Whether a heartbeat spec describes an engine the viewer can offer on its own.

    A spec is offerable exactly when
    :func:`ada.topo_model.engine_catalog.register_procedural_engine_capabilities`
    was given both a name and an entrypoint. Older workers advertise capability
    flags alone; offering one of those would present an engine the viewer has no
    way to dispatch to.
    """
    return bool(spec.get("name")) and bool(spec.get("entrypoint"))

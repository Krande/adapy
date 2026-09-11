"""Predicates over the engine capability specs workers announce in their heartbeat.

This lives under ``ada.core`` — below both packages that need it — rather than
next to the registry that writes the specs (``ada.topo_model.engine_catalog``)
or the REST API that consumes them (``ada.comms.rest``): the REST API runs in a
*slim* runtime that ships only a subset of the package (``ada.comms``,
``ada.cad``, ``ada.config`` and part of ``ada.sections`` — see
``deploy/Dockerfile.viewer``) and cannot depend on ``ada.topo_model`` (importing
any of its submodules executes its ``__init__``, which pulls in the whole
modelling stack), while ``ada.topo_model`` must not depend on ``ada.comms`` in
the other direction either. A predicate both sides need has to sit somewhere
neither depends on the other to reach.

``ada.comms.engine_specs`` and ``ada.topo_model.engine_catalog`` both re-export
this module's :func:`is_offerable`, keeping one definition for every side.

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

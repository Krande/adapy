"""Simulation providers: one interface for anything that runs a simulation as a job.

A provider is an object with an ``id``, a ``domain`` (the kind of simulation: ``fea``,
``robotics`` or ``cfd``), a free-text ``engine``, a ``describe()`` and a ``run(case, ctx)``. Core
never learns which simulator or solver is behind it: it validates the request
against the provider's parameter schema, routes the run to a worker that registered the provider,
hands the provider a context for progress, logging, cancellation and artefacts, and keeps a
record of the run. A robotics simulator, a second one and an FEA solver are three providers and
no new plumbing.

* :mod:`.interfaces` -- ``SimulationProvider``, ``SimulationCase``, ``SimulationContext``,
  ``SimulationResult`` and the small value types around them.
* :mod:`.registry` -- ``register_simulation_provider`` (also re-exported from ``ada.plugins``).
* :mod:`.schema` -- the parameter check run before a job is queued.
* :mod:`.runs` -- the run record and where a run keeps its files.
* :mod:`.runner` -- ``execute_run``, shared by the worker's job and the in-process one.
* :mod:`.activity` -- the activity feed of a run: what each of its actors is doing and planning,
  over time, for the viewer's console of the run.
* :mod:`.testing` -- a fake provider for tests; never registered by core.

Stdlib only, so the API process can import it without the modelling stack.
"""

from __future__ import annotations

from .interfaces import (
    COMMON_ARTEFACT_ROLES,
    SIMULATION_DOMAINS,
    ArtefactRole,
    ProviderDescription,
    RunKind,
    SimulationArtefact,
    SimulationCancelled,
    SimulationCase,
    SimulationContext,
    SimulationDomain,
    SimulationProvider,
    SimulationResult,
    allowed_artefact_roles,
    provider_capability,
    simulation_domain,
)
from .registry import (
    get_simulation_provider,
    register_simulation_provider,
    reset_simulation_providers,
    simulation_provider_specs,
    simulation_providers,
    unregister_simulation_provider,
)
from .schema import ParameterError, validate_parameters

__all__ = [
    "COMMON_ARTEFACT_ROLES",
    "SIMULATION_DOMAINS",
    "ArtefactRole",
    "SimulationDomain",
    "allowed_artefact_roles",
    "simulation_domain",
    "ParameterError",
    "ProviderDescription",
    "RunKind",
    "SimulationArtefact",
    "SimulationCancelled",
    "SimulationCase",
    "SimulationContext",
    "SimulationProvider",
    "SimulationResult",
    "get_simulation_provider",
    "provider_capability",
    "register_simulation_provider",
    "reset_simulation_providers",
    "simulation_provider_specs",
    "simulation_providers",
    "unregister_simulation_provider",
    "validate_parameters",
]

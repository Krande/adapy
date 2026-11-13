"""The simulation seam's vocabulary: the kinds of simulation there are, what a provider is, what
it is handed and what it returns.

Stdlib only, and engine-agnostic by construction: nothing here knows one simulator or solver from
another. A provider states its ``domain`` -- one of a small closed set (:data:`SIMULATION_DOMAINS`:
``fea``, ``robotics``, ``cfd``) -- and a free-text ``engine``. A domain carries only what is
common to every engine of that kind: the artefact roles a viewer can rely on. Core routes and
stores by the provider's ``id`` alone.
"""

from __future__ import annotations

import os
import pathlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Union, runtime_checkable

__all__ = [
    "COMMON_ARTEFACT_ROLES",
    "SIMULATION_DOMAINS",
    "PROVIDER_ID_PATTERN",
    "ArtefactData",
    "ArtefactRole",
    "SimulationDomain",
    "allowed_artefact_roles",
    "simulation_domain",
    "ProviderDescription",
    "RunKind",
    "SimulationArtefact",
    "SimulationCancelled",
    "SimulationCase",
    "SimulationContext",
    "SimulationProvider",
    "SimulationResult",
    "provider_capability",
]


@dataclass(frozen=True)
class ArtefactRole:
    """One role an artefact of a run can have, and whether a finished run must have one."""

    id: str
    description: str
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description, "required": self.required}


@dataclass(frozen=True)
class SimulationDomain:
    """One kind of simulation. ``artefact_roles`` are the roles every engine of the kind uses for
    the same thing, so a viewer can open any provider's result of that kind the same way."""

    id: str
    title: str
    description: str
    artefact_roles: tuple[ArtefactRole, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "artefact_roles": [r.to_dict() for r in (*self.artefact_roles, *COMMON_ARTEFACT_ROLES)],
        }


#: Roles a run of any domain may use beside its domain's own.
COMMON_ARTEFACT_ROLES: tuple[ArtefactRole, ...] = (
    ArtefactRole("log", "The run's log, as text."),
    ArtefactRole(
        "activity",
        "What each actor of the run (a robot, a solver stage, the mission itself) was doing and planning over "
        "time: an activity feed (ada.simulation.activity), on the run's own clock. The viewer shows it as the "
        "run's console, beside the result and while the run is still going.",
    ),
    ArtefactRole("report", "A summary for people to read."),
    ArtefactRole("data", "Anything else; only the provider's own result handler knows what it is."),
)

#: The kinds of simulation, by id. A closed set: a provider's ``domain`` must be one of these, and
#: adding a kind is a change here (and to the viewer, which groups and opens results by it).
#:
#: A finished run must have stored an artefact for each ``required`` role of its domain, and may
#: only use its domain's roles and the common ones. Both are checked while the run is going
#: (``ada.simulation.runner``), so a provider that breaks the convention fails by name instead of
#: producing a result no viewer can open.
SIMULATION_DOMAINS: dict[str, SimulationDomain] = {
    d.id: d
    for d in (
        SimulationDomain(
            "fea",
            "FEA",
            "Finite element analysis of a structure.",
            (
                ArtefactRole(
                    "result",
                    "The solver's result file, in a format the FEA bake reads; it is viewed like any other FEA result.",
                    required=True,
                ),
                ArtefactRole("model", "The analysed model as it was sent to the solver (the input deck)."),
            ),
        ),
        SimulationDomain(
            "robotics",
            "Robotics",
            "A robot moving in a simulated world.",
            (
                ArtefactRole("model", "The animated model: the robot and its world, as a GLB.", required=True),
                ArtefactRole(
                    "timeline",
                    "The topics recorded during the run, over time, in the recording format its media type names.",
                    required=True,
                ),
            ),
        ),
        SimulationDomain(
            "cfd",
            "CFD",
            "Fluid flow through or around a geometry.",
            (
                ArtefactRole("mesh", "The result mesh the fields are defined on.", required=True),
                ArtefactRole("fields", "The field values on that mesh, over time.", required=True),
            ),
        ),
    )
}


def simulation_domain(domain_id: str) -> SimulationDomain:
    """The domain with this id. ValueError, naming the known ones, for anything else."""
    try:
        return SIMULATION_DOMAINS[domain_id]
    except (KeyError, TypeError):
        raise ValueError(f"unknown simulation domain {domain_id!r} (known: {', '.join(SIMULATION_DOMAINS)})") from None


def allowed_artefact_roles(domain_id: str) -> tuple[str, ...]:
    """Every role an artefact of a run in this domain may have: the domain's, then the common ones."""
    return tuple(r.id for r in (*simulation_domain(domain_id).artefact_roles, *COMMON_ARTEFACT_ROLES))


#: What ``SimulationContext.store_artefact`` accepts: the bytes themselves, or a file on local
#: disk that is streamed to storage without being read whole.
ArtefactData = Union[bytes, bytearray, memoryview, str, "os.PathLike[str]"]


class SimulationCancelled(Exception):
    """The run was cancelled. Raised by ``SimulationContext.raise_if_cancelled``; a provider lets
    it propagate (after releasing what it holds) and the run ends as ``cancelled``, not ``error``."""


#: What a provider id may be: lower-case letters, digits, ``-`` and ``_``, at most 40 characters.
#: Exactly the characters a queue subject token keeps, so the capability below is the same string
#: wherever it is written -- the worker's capability list, a requirement keyed by it, the subject
#: a run is published on -- and two provider ids can never share a pool.
PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def provider_capability(provider_id: str) -> str:
    """The worker capability a provider's runs are routed to: ``sim-<provider id>``.

    That is ``sim.<provider id>`` as a queue subject token (``capability_token`` turns the dot
    into a dash), spelled in that final form already so nothing downstream has to normalise it.
    """
    return f"sim-{provider_id}"


@dataclass(frozen=True)
class RunKind:
    """One kind of run a provider offers (a provider with a single kind may list none)."""

    id: str
    title: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title or self.id, "description": self.description}


@dataclass(frozen=True)
class ProviderDescription:
    """What a provider says about itself: shown in the viewer and used to validate a request.

    ``parameters_schema`` is a JSON Schema for the run's parameters (an ``object`` schema). It is
    checked before a run is queued (:func:`ada.simulation.schema.validate_parameters`) and is what
    the viewer builds its start form from when no plugin supplies one.
    """

    title: str
    version: str = "0.0.0"
    description: str = ""
    kinds: tuple[RunKind, ...] = ()
    parameters_schema: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "kinds": [k.to_dict() for k in self.kinds],
            "parameters_schema": dict(self.parameters_schema),
        }


@dataclass(frozen=True)
class SimulationCase:
    """One run's inputs.

    ``scope`` names the storage scope the run belongs to; every key the context reads or writes
    is relative to it, so a provider never builds a scope path itself. ``parameters`` have been
    validated against the provider's schema, with its defaults filled in. ``inputs`` are storage
    keys of files in that scope, read through the context.
    """

    run_id: str
    scope: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    kind: str | None = None


@dataclass(frozen=True)
class SimulationArtefact:
    """One file a run produced. ``key`` is where it is in storage (scope-relative)."""

    name: str
    key: str
    media_type: str = "application/octet-stream"
    role: str = "data"
    size: int | None = None
    #: Still being written: a later ``store_artefact`` of the same name replaces it.
    partial: bool = False
    #: How many times this name has been stored in the run, so a viewer following the run knows
    #: when to fetch it again.
    revision: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "key": self.key,
            "media_type": self.media_type,
            "role": self.role,
            "size": self.size,
            "partial": self.partial,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class SimulationResult:
    """What ``run`` returns: the artefacts that make up the result and a small summary.

    ``artefacts`` are the ones the provider calls final, as ``store_artefact`` returned them. The
    summary is a small JSON-serialisable dict shown beside the run (a step count, a verdict); it
    is not the place for data, which belongs in an artefact.
    """

    artefacts: tuple[SimulationArtefact, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)


class SimulationContext(ABC):
    """What a running provider talks to. One per run, handed to ``SimulationProvider.run``.

    Every method may be called from the thread ``run`` was called on, and from threads the
    provider starts itself.
    """

    #: A directory on local disk for the run's scratch files, removed when the run ends.
    workdir: pathlib.Path

    @abstractmethod
    def progress(self, fraction: float, stage: str = "") -> None:
        """Report how far the run has got: ``fraction`` in 0..1 and a short stage name."""

    @abstractmethod
    def log(self, line: str) -> None:
        """Add a line to the run's log (kept as the ``log.txt`` artefact, role ``log``)."""

    @abstractmethod
    def cancelled(self) -> bool:
        """Whether the run has been asked to stop. Cheap; call it between units of work."""

    def raise_if_cancelled(self) -> None:
        """Raise :class:`SimulationCancelled` when the run has been asked to stop."""
        if self.cancelled():
            raise SimulationCancelled("the run was cancelled")

    @abstractmethod
    def store_artefact(
        self,
        name: str,
        data_or_path: ArtefactData,
        *,
        partial: bool = False,
        role: str = "data",
        media_type: str | None = None,
    ) -> SimulationArtefact:
        """Put an artefact in the run's directory and list it on the run at once.

        ``name`` is a relative file name (``frames/0001.bin`` is fine). ``role`` says what the
        artefact is, in the words of the provider's domain (:data:`SIMULATION_DOMAINS`) or one of
        the common roles; any other role raises ValueError. ``partial=True`` says more is coming
        under the same name: a viewer following the run fetches it again each time it is
        stored. Storing a name again replaces it.
        """

    @abstractmethod
    def read_input(self, key: str) -> bytes:
        """The bytes of one of the case's input files."""

    @abstractmethod
    def fetch_input(self, key: str, dest: "str | os.PathLike[str] | None" = None) -> pathlib.Path:
        """Stream one of the case's input files to local disk and return where it is
        (``dest``, or a file in :attr:`workdir`)."""


@runtime_checkable
class SimulationProvider(Protocol):
    """Something that runs simulations. Registered with
    :func:`ada.simulation.register_simulation_provider`.

    ``id`` is unique and stable: runs are routed and recorded by it (lower-case letters, digits,
    ``-`` and ``_``; :data:`PROVIDER_ID_PATTERN`). ``domain`` is the kind of
    simulation, one of :data:`SIMULATION_DOMAINS` (``"fea"``, ``"robotics"``, ``"cfd"``); the
    viewer groups providers by it and opens results by its artefact roles. ``engine`` is free
    text: the simulator's or solver's name.
    """

    id: str
    domain: str
    engine: str

    def describe(self) -> ProviderDescription:
        """Title, version, the kinds of run on offer and the parameters' JSON Schema."""

    def run(self, case: SimulationCase, ctx: SimulationContext) -> SimulationResult:
        """Run ``case`` to the end, reporting through ``ctx``. Blocking; called on a worker
        thread. Raise to fail the run; let :class:`SimulationCancelled` out to end it as
        cancelled."""

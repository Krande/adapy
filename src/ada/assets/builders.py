"""Which builder serves a ``build`` capability -- the worker-side half of Decision 1.

CAPABILITY, NEVER PROVIDER ID. A ``build`` claim carries its own capability token
(``BuildDelivery.capability``); core routes the job to the pool advertising it and, on that pool,
resolves the builder registered under it. Core never learns which package registered what, which
is what lets one collection be MIXED -- an IFC-developed branch and a branch fed by a
private-format provider, each with its own capability, inside one tree (Decision 18).

Same shape as ``ada.assets.registry``: factory-registered, idempotent by id, conflicts judged on
``module:qualname`` rather than function identity (Decision 20 -- a ``register()`` that closes over
a client returns a new function object per call, so identity would refuse the exact case the
registry exists to tolerate).

AVAILABILITY IS DECLARED, NOT ASSUMED. A capability is advertised only where its builder can
actually run: the core IFC builder needs ifcopenshell and adacpp, which the slim viewer image does
not carry. A pool that advertised a capability it cannot serve would take the job and time out --
strictly worse than never claiming it.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from ada.config import logger

__all__ = [
    "AssetBuilder",
    "BuildRequest",
    "AssetBuilderError",
    "asset_builder",
    "available_build_capabilities",
    "builder_accepts_exclude_nodes",
    "clear_asset_builders",
    "ensure_core_builders",
    "invoke_builder",
    "register_asset_builder",
    "registered_build_capabilities",
    "unhonoured_exclusions_warning",
]


class AssetBuilderError(LookupError):
    """No builder for a capability, or two different builders claiming one."""


@runtime_checkable
class AssetBuilder(Protocol):
    """The worker-side half of a ``build`` claim.

    ``options`` is the provider's own dict, forwarded verbatim; ``derived_prefix`` is the prefix
    CORE composed, and the builder writes its GLB under it and returns a summary naming that key.

    ``exclude_nodes`` (OPTIONAL, keyword): node ids whose whole subtrees the build must SKIP -- the
    descendants of the requested node that have their own newer publish, or that a hierarchy patch
    deleted or moved out since the covering revision (docs/design/apply-changes.md section 4). So
    an ancestor's mesh never shows an element's old geometry beside its new one, nor a deleted
    element. Core passes it ONLY when it is non-empty and ONLY to a builder that declares it: a
    ``build`` with an ``exclude_nodes`` parameter (or ``**kwargs``), or a builder with
    ``accepts_exclude_nodes = True``. An older builder is called exactly as before -- its output
    then still draws the excluded subtrees, and the stored summary says so in ``warnings``. The
    list is part of the build's fingerprint, so an excluding build never shares a key with a full
    one.
    """

    def build(
        self,
        options: Mapping[str, Any],
        *,
        request: "BuildRequest",
        storage: Any,
        scope: Any,
        derived_prefix: str,
        on_progress: Callable[[str, float], None] | None = None,
        cancel_event: Any | None = None,
    ) -> Any: ...


def builder_accepts_exclude_nodes(builder: Any) -> bool:
    """Whether ``builder`` takes ``exclude_nodes`` (see :class:`AssetBuilder`): an explicit
    ``accepts_exclude_nodes`` attribute wins, else its ``build`` signature is read."""
    flag = getattr(builder, "accepts_exclude_nodes", None)
    if flag is not None:
        return bool(flag)
    try:
        params = inspect.signature(builder.build).parameters.values()
    except (TypeError, ValueError, AttributeError):
        return False
    return any(p.name == "exclude_nodes" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def invoke_builder(builder: Any, options: Mapping[str, Any], *, exclude_nodes=(), **kwargs) -> tuple[Any, bool]:
    """Call ``builder.build`` the way its contract allows; ``(result, honoured)``.

    ``exclude_nodes`` travels only when non-empty and only to a builder that declares it, so a
    provider written before the parameter existed keeps working. ``honoured`` is False when there
    was something to exclude and the builder could not be told -- the caller records that."""
    excluded = sorted({str(n) for n in exclude_nodes or ()})
    if excluded and builder_accepts_exclude_nodes(builder):
        return builder.build(options, exclude_nodes=excluded, **kwargs), True
    if excluded:
        logger.warning(
            "asset builder %s does not accept exclude_nodes; %d excluded subtree(s) will be drawn anyway",
            type(builder).__name__,
            len(excluded),
        )
    return builder.build(options, **kwargs), not excluded


def unhonoured_exclusions_warning(exclude_nodes) -> str:
    """The summary warning for a build whose builder could not skip ``exclude_nodes``."""
    return (
        f"the builder does not accept exclude_nodes: {len(exclude_nodes)} subtree(s) with a newer own publish or "
        f"removed by a hierarchy patch are drawn as of the covering revision"
    )


@dataclass(frozen=True)
class BuildRequest:
    """The identity core composed the derived key from, handed to the builder so its provenance
    block can restate it. A builder that invented any of these would fail validation."""

    provider: str
    collection: str
    subject: str
    revision: str
    node: str | None
    fingerprint: str
    hierarchy_source: str


@dataclass(frozen=True)
class _Entry:
    factory: Callable[[], AssetBuilder]
    origin: str
    label: str | None
    available: Callable[[], bool] | None


_BUILDERS: dict[str, _Entry] = {}


def _origin_of(factory: Callable[[], AssetBuilder]) -> str:
    module = getattr(factory, "__module__", "?")
    qualname = getattr(factory, "__qualname__", getattr(factory, "__name__", "?"))
    return f"{module}:{qualname}"


def register_asset_builder(
    capability: str,
    factory: Callable[[], AssetBuilder],
    *,
    label: str | None = None,
    available: Callable[[], bool] | None = None,
) -> None:
    """Register (or re-register) the builder for ``capability``.

    Idempotent for the same origin -- an entry point loaded twice, by discovery AND an explicit
    ``ADA_WORKER_PRELOAD``, is the normal case and must not be an error. Two genuinely different
    builders claiming one capability still differ by origin and are refused, because silently
    taking one of them would make which code ran depend on import order.
    """
    if not isinstance(capability, str) or not capability.strip():
        raise AssetBuilderError("a build capability must be a non-empty string")
    origin = _origin_of(factory)
    existing = _BUILDERS.get(capability)
    if existing is not None and existing.origin != origin:
        raise AssetBuilderError(
            f"build capability {capability!r} is already registered by {existing.origin}; "
            f"{origin} may not take it over"
        )
    _BUILDERS[capability] = _Entry(factory=factory, origin=origin, label=label, available=available)


def asset_builder(capability: str) -> AssetBuilder:
    ensure_core_builders()
    entry = _BUILDERS.get(capability)
    if entry is None:
        known = ", ".join(sorted(_BUILDERS)) or "none"
        raise AssetBuilderError(
            f"no builder registered for capability {capability!r} in this process (known: {known}). "
            f"A build claim routes to the pool advertising its capability; this pool does not serve it."
        )
    return entry.factory()


def registered_build_capabilities() -> list[str]:
    ensure_core_builders()
    return sorted(_BUILDERS)


def available_build_capabilities() -> list[str]:
    """The capabilities this process can actually serve -- what a pool may advertise."""
    ensure_core_builders()
    out = []
    for capability, entry in sorted(_BUILDERS.items()):
        if entry.available is None:
            out.append(capability)
            continue
        try:
            if entry.available():
                out.append(capability)
        except Exception as exc:  # a probe that throws is a "no", and worth saying so once
            logger.warning("asset builder %s: availability probe failed: %s", capability, exc)
    return out


def clear_asset_builders() -> None:
    """Test hook. Core builders re-register on the next call that needs them."""
    global _CORE_LOADED
    _BUILDERS.clear()
    _CORE_LOADED = False


_CORE_LOADED = False


def ensure_core_builders() -> None:
    """Register the builders core ships. Import is LAZY: the IFC builder pulls ifcopenshell and
    adacpp, and a process that only serves other capabilities must not pay for them."""
    global _CORE_LOADED
    if _CORE_LOADED:
        return
    _CORE_LOADED = True  # set first: a failing import must not retry on every call
    try:
        importlib.import_module("ada.assets.ifc")
    except Exception as exc:  # pragma: no cover - a broken core import is a bug, not a config
        logger.warning("core asset builders unavailable: %s", exc)


def module_available(*modules: str) -> Callable[[], bool]:
    """An availability probe that checks imports without performing them."""

    def _probe() -> bool:
        return all(importlib.util.find_spec(m) is not None for m in modules)

    return _probe

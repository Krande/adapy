"""Backend half of the adapy viewer plugin system — the Python twin of the
frontend plugin registry (``src/frontend/src/plugins/registry.ts``).

Core adapy carries NO hardcoded knowledge of any feature plugin. A plugin's
backend self-describes via one of two discovery mechanisms — both already proven
by the procedural/detailing engine catalogs:

1. **Worker preload** — the plugin's dotted module is listed in
   ``ADA_WORKER_PRELOAD`` (``comms/rest/worker.py``); importing it runs
   :func:`register_plugin_backend` as an import side effect (idempotent-by-id,
   the exact ``register_procedural_engine_capabilities`` pattern). The worker
   then advertises the registry on its heartbeat under ``plugin_specs`` and the
   REST layer unions it into ``GET /api/plugins`` (with the static
   ``builtin_plugin_specs`` fallback in ``comms/rest/catalog.py``).
2. **Entry-point discovery** — a plugin dist declares an ``ada.plugins`` entry
   point whose value is a ``register()`` callable; :func:`discover_plugins`
   imports and runs them. This covers an in-process discovery not tied to a
   worker pool.

Two additional conventions this module owns:

* **Reserved sidecar prefix** — a plugin owns the blob keyspace
  ``{sidecar_prefix}.*`` next to a result manifest; core never writes into
  another plugin's prefix (see :func:`reserved_sidecar_prefix`).
* **Artefact contributors** — the core FEA bake must not import a plugin. A
  plugin instead registers an *artefact contributor* via
  :func:`register_plugin_artefact_contributor`; the bake calls
  :func:`plugin_artefact_contributors` and folds each return under the reserved
  ``manifest["plugins"][id]`` map (see ``ada/fem/results/artefacts.py``).

Phase 1 ships this contract with NO built-in backend plugins registered — every
registry below is empty until a plugin registers, so behaviour is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "PLUGIN_ENTRY_POINT_GROUP",
    "register_plugin_backend",
    "plugin_backend_specs",
    "plugin_backend_spec",
    "register_plugin_artefact_contributor",
    "plugin_artefact_contributors",
    "reserved_sidecar_prefix",
    "discover_plugins",
    "reset_registry",
]

# importlib.metadata entry-point group a plugin dist declares to be discovered:
#   [project.entry-points."ada.plugins"]
#   my-plugin = "my_plugin:register"
PLUGIN_ENTRY_POINT_GROUP = "ada.plugins"

# plugin id -> spec dict. Insertion-ordered; idempotent-by-id (last writer per id
# wins — a re-imported preload module just replaces its own entry). The spec is a
# plain dict (not a dataclass), matching the engine catalogs, and always carries a
# ``slug`` key equal to the id so the REST union primitive
# ``_live_worker_specs("plugin_specs")`` (which keys by ``slug``) folds it.
_PLUGIN_REGISTRY: dict[str, dict] = {}

# plugin id -> artefact-contributor callable ``(bake_ctx: dict) -> Any``.
_ARTEFACT_CONTRIBUTORS: dict[str, Callable[[dict], Any]] = {}


def register_plugin_backend(
    plugin_id: str,
    *,
    name: str | None = None,
    description: str = "",
    version: str = "0.0.0",
    worker_capability: str | None = None,
    sidecar_prefix: str | None = None,
    schema_version: int | None = None,
    **extra: Any,
) -> None:
    """Register (or replace) a backend plugin's advertised spec. ``plugin_id`` is
    the globally-unique kebab-case id that namespaces everything (routes, sidecar
    prefix, manifest map). Idempotent by ``plugin_id``. ``**extra`` is folded into
    the spec verbatim so a plugin can advertise its own flags without a core
    change (mirrors the engine catalogs).

    Three ``extra`` keys core *does* act on, all opt-in and all absent by
    default:

    ``capability_option: str``
        Names one of the plugin's own options. When a job request supplies it,
        ``POST /api/plugins/{id}/jobs`` routes to ``<worker_capability>-<value>``
        instead of ``<worker_capability>``, letting one plugin address several
        pools whose workers are *not* interchangeable — each holding a different
        licence, dataset or device. Core never learns what the option means; it
        only normalises the value into a subject token. Workers opt in by
        listing the sharded token in ``ADA_WORKER_CAPABILITIES``, and a worker
        listing both the bare and the sharded form serves unqualified requests
        too. An absent or unusable value falls back to the bare capability.

    ``union_fields: list[str]``
        Names spec keys that should be COMBINED across every worker advertising
        this plugin rather than taken from one of them. Without it, several
        workers advertising one slug means one worker's copy wins and the others
        are invisible — so a sharded pool cannot say what it collectively
        covers. Only list-valued keys are merged, and only the named ones.

    ``requires_admin: bool``
        Declares that enqueuing this plugin's job needs an admin, not merely a
        user who can reach the scope. Use it for a job that costs something a
        normal user should not be able to spend — one that drives a licensed
        workstation, a device, or a pool of one.

        **This declaration is a floor, not the whole gate.** A spec is
        advertised BY A WORKER, so a worker on an older build advertises no flag
        at all, and a gate that trusted only this would silently vanish exactly
        when the deployment is least uniform. Core therefore OR-s it with an
        admin-only setting (``admin.plugin_jobs.require_admin``, a list of
        plugin ids) that no worker can influence. Either source restricts; a
        source can only ever tighten. Declare it here so the intent travels with
        the plugin, and set the setting so the deployment does not depend on
        every worker being current.
    """
    if not plugin_id or not isinstance(plugin_id, str):
        raise ValueError("plugin_id must be a non-empty string")
    spec: dict = {
        "slug": plugin_id,
        "id": plugin_id,
        "name": name or plugin_id,
        "description": description,
        "version": version,
        "worker_capability": worker_capability,
        "sidecar_prefix": sidecar_prefix or plugin_id,
        "schema_version": schema_version,
    }
    spec.update(extra)
    _PLUGIN_REGISTRY[plugin_id] = spec


def plugin_backend_specs() -> list[dict]:
    """The registered backend plugin specs (a fresh copy so callers can't mutate
    the registry) — the shape a worker advertises on its heartbeat under
    ``plugin_specs``."""
    return [dict(v) for v in _PLUGIN_REGISTRY.values()]


def plugin_backend_spec(plugin_id: str) -> dict | None:
    """The registered spec for ``plugin_id`` (a fresh copy), or ``None`` if not
    registered. Used by the worker's generic plugin-job dispatch to resolve a
    plugin's ``job_entrypoint`` (advertised via ``**extra`` on
    :func:`register_plugin_backend`) without core ever naming the plugin."""
    spec = _PLUGIN_REGISTRY.get(plugin_id)
    return dict(spec) if spec else None


def reserved_sidecar_prefix(plugin_id: str) -> str:
    """The blob-key prefix a plugin owns next to a result manifest
    (``{prefix}.*``). Defaults to the plugin id; a plugin may advertise an alias
    via ``sidecar_prefix`` on :func:`register_plugin_backend`."""
    spec = _PLUGIN_REGISTRY.get(plugin_id)
    return (spec or {}).get("sidecar_prefix", plugin_id) if spec else plugin_id


def register_plugin_artefact_contributor(
    plugin_id: str,
    contribute: Callable[[dict], Any],
) -> None:
    """Register a contributor the core result bake calls to populate
    ``manifest["plugins"][plugin_id]``. ``contribute(bake_ctx)`` returns the
    plugin's opaque manifest sub-object (any JSON-serialisable value), or ``None``
    to contribute nothing. Idempotent by ``plugin_id``."""
    if not callable(contribute):
        raise TypeError("contribute must be callable")
    _ARTEFACT_CONTRIBUTORS[plugin_id] = contribute


def plugin_artefact_contributors() -> list[tuple[str, Callable[[dict], Any]]]:
    """Registered ``(plugin_id, contribute)`` pairs, in registration order."""
    return list(_ARTEFACT_CONTRIBUTORS.items())


def discover_plugins() -> list[str]:
    """Import and run every ``ada.plugins`` entry-point ``register()``. Each entry
    is isolated: a failing plugin is logged and skipped, never aborting discovery
    (unlike ``ADA_WORKER_PRELOAD``, which is deliberately fatal for a worker that
    exists *because* of its preload). Returns the ids of entry points run."""
    from importlib.metadata import entry_points

    run: list[str] = []
    try:
        eps = entry_points(group=PLUGIN_ENTRY_POINT_GROUP)
    except TypeError:
        # Python <3.10 compat: entry_points() takes no kwargs there.
        eps = entry_points().get(PLUGIN_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            register = ep.load()
            register()
            run.append(ep.name)
            logger.info("ada.plugins: registered backend plugin %s", ep.name)
        except Exception:
            logger.exception("ada.plugins: failed to register plugin %s (skipped)", ep.name)
    return run


def reset_registry() -> None:
    """Clear both registries — for tests only."""
    _PLUGIN_REGISTRY.clear()
    _ARTEFACT_CONTRIBUTORS.clear()

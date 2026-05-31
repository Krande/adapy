"""Worker-defined "utilities" for the hosted viewer.

A *utility* is a worker-side function that takes the **currently loaded scene**
(streamed to a local GLB tempfile, exactly like a converter source) plus a set of
typed keyword arguments, and returns a **viewer-operations** payload — a small
JSON document the frontend applies to the live scene (e.g. recolour elements).
Unlike a converter it does not emit a new downloadable file; unlike a procedure
it runs in the NATS worker pool and is auto-announced to the frontend.

The mechanism mirrors :mod:`ada.comms.rest.converter`:

* ``@utility(...)`` registers a handler + its spec in :class:`UtilityRegistry`
  at import time.
* The worker publishes :meth:`UtilityRegistry.specs` to NATS KV in its
  registration info; the API merges every live worker's specs and surfaces them
  through ``GET /api/config`` so the SPA can list utilities and render their
  input forms without hardcoding anything.
* A utility runs as a job with ``target_format == "utility"``; the worker
  resolves the handler by ``conversion_options["utility_name"]``, calls it, and
  stores the returned dict as the derived blob
  ``_derived/<source_key>.<utility>.viewops.json``. The frontend fetches it on
  job completion and applies the ops.

Handler signature::

    @utility(
        name="diff",
        description="...",
        kwargs=[{"name": "compare_ref", "type": "string", "description": "..."}],
        inputs=("scene_glb",),
        affects=("scene.element_colors",),
        returns="viewer_ops",
    )
    def diff(scene_glb_path, *, storage, scope, on_progress, **kwargs) -> dict:
        ...

The handler receives the local path to the scene GLB, a ``storage`` handle (to
fetch other blobs such as a compare-ref build), the ``scope`` the job runs in,
an ``on_progress(stage, frac)`` callback, and the declared kwargs by name.

Viewer-operations contract (the dict a utility returns)::

    {
        "version": 1,
        "ops": [
            {"op": "color_elements",
             "elements": [{"key": "<element name or guid>", "color": "#rrggbb"}]},
            {"op": "add_overlay_geometry",
             "blob_key": "_derived/<src>.diff.removed.glb",
             "label": "removed", "color": "#ff0000"},
        ],
        "legend": [{"label": "added", "color": "#00ff00", "count": 12}, ...],
        "summary": {...},          # free-form, shown in the panel
    }

Two op kinds:

* ``color_elements`` — recolour elements already in the loaded scene. ``key`` is
  the element identity the frontend resolves against the loaded GLB: the element
  *name* (which is the GLB draw-range / rangeId) with the *guid* as a fallback.
* ``add_overlay_geometry`` — add geometry that is NOT in the loaded scene (e.g.
  elements present in a compare-ref but removed from the current model) as a
  distinctly-coloured, clearable overlay layer. ``blob_key`` points at a derived
  GLB the utility wrote (the worker uploads it alongside the viewops JSON); the
  frontend fetches and adds it to the scene, tagging it so "Clear" removes it.

Colours are ``#rrggbb`` hex.
"""

from __future__ import annotations

from typing import Callable

# Progress contract shared with the converter: stage name (str), fraction (0..1).
ProgressFn = Callable[[str, float], None]

# A utility handler. Positional: the local scene GLB path. Keyword-only:
# storage/scope/on_progress + the declared kwargs. Returns a viewer-ops dict.
UtilityFn = Callable[..., dict]

VIEWOPS_VERSION = 1

# Suffix for the derived viewer-ops blob a utility job produces. Lives in the
# same ``_derived/`` namespace converters use, so it stays out of the user file
# list while remaining scoped to the source key.
VIEWOPS_SUFFIX = ".viewops.json"


class UnknownUtility(ValueError):
    pass


class UtilityRegistry:
    """Module-level ``name -> (handler, spec)`` table.

    Populated at import time by the :func:`utility` decorator. The spec is the
    wire-shape dict the worker publishes and the SPA renders from; the handler
    is what the worker invokes. Plain dicts — the worker looks up once per job.
    """

    _entries: dict[str, UtilityFn] = {}
    _specs: dict[str, dict] = {}

    @classmethod
    def register(cls, name: str, fn: UtilityFn, spec: dict) -> None:
        if name in cls._entries:
            raise ValueError(f"utility {name!r} already registered")
        cls._entries[name] = fn
        cls._specs[name] = spec

    @classmethod
    def lookup(cls, name: str) -> UtilityFn:
        fn = cls._entries.get(name)
        if fn is None:
            raise UnknownUtility(
                f"no utility registered for {name!r}; "
                f"available: {sorted(cls._entries) or 'none'}"
            )
        return fn

    @classmethod
    def specs(cls) -> list[dict]:
        """JSON-serialisable list of utility specs (one per registration).

        Wire shape::

            [{
                "name": "diff",
                "description": "...",
                "kwargs": [{"name", "type", "default", "description", "enum"?}, ...],
                "inputs": ["scene_glb"],
                "affects": ["scene.element_colors"],
                "returns": "viewer_ops",
            }, ...]
        """
        return [dict(cls._specs[n]) for n in sorted(cls._specs)]

    @classmethod
    def names(cls) -> frozenset[str]:
        return frozenset(cls._entries)


def utility(
    *,
    name: str,
    description: str,
    kwargs: list[dict] | None = None,
    inputs: tuple[str, ...] = ("scene_glb",),
    affects: tuple[str, ...] = ("scene.element_colors",),
    returns: str = "viewer_ops",
):
    """Register a worker utility + its frontend-facing spec.

    ``kwargs`` is a list of schema dicts (same shape converters use for their
    ``options``) so the SPA renders the right widget per argument::

        {"name": "diff_type", "type": "enum", "default": "byCentroid",
         "description": "...", "enum": ["byCentroid", "byName", ...]}

    Supported ``type`` values: ``string`` | ``int`` | ``float`` | ``bool`` |
    ``enum``. ``inputs`` declares what the utility consumes (``"scene_glb"`` =
    the currently loaded model), ``affects`` declares what it changes in the
    viewer, and ``returns`` declares the result kind the frontend should expect.
    """

    def deco(fn: UtilityFn) -> UtilityFn:
        spec = {
            "name": name,
            "description": description,
            "kwargs": [dict(k) for k in (kwargs or [])],
            "inputs": list(inputs),
            "affects": list(affects),
            "returns": returns,
        }
        UtilityRegistry.register(name, fn, spec)
        return fn

    return deco


def run_utility(
    name: str,
    scene_glb_path,
    *,
    storage,
    scope,
    on_progress: ProgressFn | None = None,
    kwargs: dict | None = None,
) -> dict:
    """Dispatch a utility by name and return its viewer-ops payload.

    Thin wrapper the worker calls; keeps the lookup + invocation contract in one
    place so the worker's job loop stays a one-liner.
    """
    progress = on_progress or (lambda _stage, _frac: None)
    handler = UtilityRegistry.lookup(name)
    payload = handler(
        scene_glb_path,
        storage=storage,
        scope=scope,
        on_progress=progress,
        **(kwargs or {}),
    )
    if not isinstance(payload, dict) or "ops" not in payload:
        raise ValueError(
            f"utility {name!r} returned a malformed viewer-ops payload "
            f"(expected a dict with an 'ops' key)"
        )
    payload.setdefault("version", VIEWOPS_VERSION)
    return payload


def viewops_key_for(source_key: str, utility_name: str) -> str:
    """Derived blob key for a utility's viewer-ops result.

    Mirrors :func:`ada.comms.rest.converter.derived_key_for` so the result lives
    under ``_derived/`` scoped to the source key, one blob per utility.
    """
    src = source_key.strip("/")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in utility_name)
    return f"_derived/{src}.{safe}{VIEWOPS_SUFFIX}"

"""What this worker advertises on its registration: engine gating of the conversion matrix,
catalog spec lists and the installed-package manifest.
"""

from __future__ import annotations

import os

from ada.config import logger


def _gate_enum_option(opt: dict | None, allowed: set[str] | frozenset[str]) -> None:
    """Filter one enum option's values to ``allowed`` and repair its default in place.

    An option that would filter to EMPTY is left untouched: a stale-but-selectable dropdown beats an
    empty one, and an empty enum reads to the API's union as "this pool advertises nothing" rather
    than "this pool couldn't tell".
    """
    if not opt or not isinstance(opt.get("enum"), list):
        return
    kept = [e for e in opt["enum"] if e in allowed]
    if not kept:
        return
    opt["enum"] = kept
    if opt.get("default") not in kept:
        opt["default"] = kept[0]


def _gate_serializer_axis(ser: dict | None, tess: dict | None, allowed: frozenset[str]) -> None:
    """Gate the serializer × tessellator pair to the kernels this worker has, in place.

    CLIENT serializers pass through ungated. ``wasm`` executes in the user's browser, so this
    worker lacking adacpp says nothing about whether the browser can run it; filtering it here
    would make the SPA lose the in-browser option because a *server* pool was thin. That is why the
    runtime map — not a name check — decides: :func:`converter.available_tess_tokens` deliberately
    omits the client tokens, so gating them against it would drop every one of them.

    A server serializer whose kernels all vanish is dropped entirely (with its label/runtime entry),
    and the tessellator enum is rebuilt as the union of what survived. B-rep rows ride the same
    path: their second axis is a WRITER, but "can this process run it" is the same question about
    the same backends, so the same token set answers it.
    """
    from ..converter import _GLB_CLIENT_SERIALIZERS

    if not ser or not tess or not isinstance(tess.get("enum_by"), dict):
        return
    enum_by: dict[str, list[str]] = {}
    for s, toks in tess["enum_by"].items():
        if not isinstance(toks, list):
            continue
        if s in _GLB_CLIENT_SERIALIZERS:
            enum_by[s] = list(toks)
            continue
        kept = [t for t in toks if t in allowed]
        if kept:
            enum_by[s] = kept
    if not enum_by:
        return
    tess["enum_by"] = enum_by

    if isinstance(ser.get("enum"), list):
        ser["enum"] = [s for s in ser["enum"] if s in enum_by]
        if ser.get("default") not in ser["enum"] and ser["enum"]:
            ser["default"] = ser["enum"][0]
        for key in ("labels", "runtime"):
            if isinstance(ser.get(key), dict):
                ser[key] = {s: v for s, v in ser[key].items() if s in enum_by}

    order = [s for s in (ser.get("enum") or []) if s in enum_by] or list(enum_by)
    toks: list[str] = []
    for s in order:
        for t in enum_by[s]:
            if t not in toks:
                toks.append(t)
    tess["enum"] = toks
    # Mirror _glb_serializer_options' own rule (the default tessellator is the default serializer's
    # first) so a serializer dropped above takes its default with it.
    default_ser = ser.get("default")
    if default_ser in enum_by and enum_by[default_ser]:
        tess["default"] = enum_by[default_ser][0]
    elif toks and tess.get("default") not in toks:
        tess["default"] = toks[0]
    for key in ("labels", "descriptions"):
        if isinstance(tess.get(key), dict):
            tess[key] = {t: v for t, v in tess[key].items() if t in toks}


def _gate_advertised_engines(conversions: list[dict]) -> list[dict]:
    """Restrict every advertised engine/kernel enum to what this worker can actually run, so the
    API's per-worker union and its engine routing reflect real capability.
    Deep-copies so the shared ConverterRegistry option dicts aren't mutated.

    Gates THREE axes, not one. Gating only ``step_glb_pipeline`` (as this did) left the
    ``serializer``/``tessellator`` dropdowns — the ones the SPA actually renders — advertising every
    kernel the registry knows, including ones this pool has no build of. A job then routed here on
    the strength of that advert and silently fell back, so the user picked a track and got a
    different one. ``glb_tess_engine`` carries the same engine vocabulary for non-STEP sources and
    was equally ungated.

    The runnable sets come from ``available_step_glb_pipelines()`` / ``available_tess_tokens()``,
    which gate the adacpp engines on find_spec presence (overlay-robust) rather than an
    import-based native probe — see the note there.
    """
    import copy

    from ..converter import available_step_glb_pipelines, available_tess_tokens

    engines = set(available_step_glb_pipelines())
    tokens = available_tess_tokens()
    gated = copy.deepcopy(conversions)
    for row in gated:
        for opts in (row.get("options") or {}).values():
            if not isinstance(opts, list):
                continue
            by_name = {o.get("name"): o for o in opts if isinstance(o, dict)}
            for name in ("step_glb_pipeline", "glb_tess_engine"):
                _gate_enum_option(by_name.get(name), engines)
            _gate_serializer_axis(by_name.get("serializer"), by_name.get("tessellator"), tokens)
    return gated


def _capture_worker_packages() -> list[dict]:
    """Snapshot the worker env's installed packages — the conda-meta manifest
    (authoritative for occt / pythonocc-core / ada-cpp / ifcopenshell / numpy …)
    plus any pip-only dists. Best-effort; a parse failure just drops that entry."""
    import glob
    import importlib.metadata as _im
    import json as _json
    import sys

    pkgs: dict[str, dict] = {}
    try:
        for f in glob.glob(os.path.join(sys.prefix, "conda-meta", "*.json")):
            try:
                with open(f) as fh:
                    d = _json.load(fh)
                name = d.get("name")
                if name:
                    pkgs[name.lower()] = {
                        "name": name,
                        "version": d.get("version"),
                        "build": d.get("build"),
                        "channel": d.get("channel"),
                    }
            except Exception:
                continue
    except Exception:
        pass
    try:
        for dist in _im.distributions():
            name = (dist.metadata.get("Name") or "").strip()
            if name and name.lower() not in pkgs:
                pkgs[name.lower()] = {"name": name, "version": dist.version, "build": None, "channel": "pypi"}
    except Exception:
        pass
    return sorted(pkgs.values(), key=lambda p: (p.get("name") or "").lower())


def _advertised_specs(label: str, module: str, attr: str) -> list:
    """One catalog this worker advertises on its heartbeat: ``module.attr()``
    (a spec-list function such as ``ada.topo_model.procedural_blueprint_specs``),
    imported lazily so a worker image without the modelling stack still boots.
    Any failure is logged and advertises ``[]`` (non-fatal) — the base image's
    built-ins are still offered by the API's static fallbacks. Each registry is
    read AFTER ``ADA_WORKER_PRELOAD`` / ``ada.plugins`` discovery, so a
    capability worker's own registrations ride along."""
    import importlib

    try:
        return getattr(importlib.import_module(module), attr)()
    except Exception:
        logger.exception("worker: failed to list %s (non-fatal)", label)
        return []

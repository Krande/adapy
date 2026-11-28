"""Core jobs the BROWSER runs: the claim, the outputs it may store, and how a claim ends.

A request a route turns into a core job (a clash check, a detail, a component build, a selection
export, an equipment bounding box, a scene utility) carries ``X-Ada-Run-On: browser`` when the
viewer's execution policy runs that job family in the browser first (``core_job`` in
:mod:`.execution_policy`). The route composes the job exactly as for a worker -- the same derived
key, the same options, the same audit row -- and :class:`~.job_transport.QueueJobTransport` keeps
the job record WITHOUT publishing it to a pool: a CLAIM (``Job.run_on == "browser"``). The route
answers ``{"job_id": null, "browser_job": <claim>}`` instead of a job id, because no worker job
was queued.

The browser then:

1. ``POST .../browser-jobs/{id}/start`` -- takes the claim (queued -> running, once);
2. runs it in Pyodide (``ada.comms.rest.wasm_jobs.run``: the worker handler's own work, on the
   same request, through a synchronous storage facade);
3. ``PUT .../browser-jobs/{id}/outputs/{name}`` -- each output the job produced. The server, not
   the browser, decides where an output goes (:func:`output_target`): only the keys the route
   composed for this job, validated as the worker's own output would be;
4. ``POST .../browser-jobs/{id}/complete`` -- done (its main output is stored) or error.

Or, when the browser cannot run it and the policy lists the worker after it,
``POST .../browser-jobs/{id}/to-worker`` publishes the SAME job record to its pool, judged as an
explicit worker choice: the job id, keys and audit row stay what the route made.

Light: stdlib and ``ada.clash`` parsers only, because the slim API imports it.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Mapping
from typing import Any

RUN_ON_BROWSER = "browser"
RUN_ON_WORKER = "worker"
#: The stage a claimed job shows until the browser takes it.
BROWSER_STAGE = "browser"

_browser_choice: contextvars.ContextVar[bool] = contextvars.ContextVar("ada_browser_choice", default=False)


def requests_browser(headers) -> bool:
    """The request asked to run its job in the browser (``X-Ada-Run-On: browser``)."""
    from .worker_access import RUN_ON_HEADER

    return str(headers.get(RUN_ON_HEADER, "")).strip().lower() == RUN_ON_BROWSER


def set_browser_choice(value: bool) -> contextvars.Token:
    return _browser_choice.set(bool(value))


def reset_browser_choice(token: contextvars.Token) -> None:
    _browser_choice.reset(token)


def browser_choice() -> bool:
    """Whether the current request asked for the browser (see :func:`requests_browser`)."""
    return _browser_choice.get()


#: The job kinds (target formats) the browser can run, by their ``core_job`` family. Left out:
#: the selection export of a published node or a group (``export_selection_asset`` /
#: ``export_selection_group``), whose IFC carries the attributes and relations a worker reads from
#: the database, which the browser cannot reach.
BROWSER_KINDS: dict[str, str] = {
    "clash_check": "clash_check",
    "clash_check_asset": "clash_check",
    "clash_check_group": "clash_check",
    "clash_contacts": "clash_check",
    "clash_detail": "clash_detail",
    "clash_detail_group": "clash_detail",
    "component_build": "component_build",
    "export_selection": "export_selection",
    "equipment_bbox": "equipment_bbox",
    "utility": "utility",
}

#: The transport feature a family is submitted (and judged) under.
FAMILY_FEATURE: dict[str, str] = {
    "clash_check": "clash_check",
    "clash_detail": "clash_detail",
    "component_build": "component_build",
    "export_selection": "export_selection",
    "equipment_bbox": "bbox_inference",
    "utility": "utilities",
}


def family_of(kind: str, conversion_options: Mapping | None = None) -> str | None:
    """The ``core_job`` family of a job kind, or None when the browser does not run it. A
    ``clash_contacts`` job is a check (``check`` / ``annotate``) or a detail (``detail`` /
    ``subjects``) by its mode."""
    family = BROWSER_KINDS.get(kind)
    if kind == "clash_contacts":
        mode = str((conversion_options or {}).get("mode") or "")
        return "clash_detail" if mode in ("detail", "subjects") else "clash_check"
    return family


def claimable(kind: str, conversion_options: Mapping | None = None) -> bool:
    return family_of(kind, conversion_options) is not None


def claim_document(payload: Mapping[str, Any]) -> dict:
    """What the browser needs to run a claimed job: a queue record's fields (``asdict(job)``)."""
    kind = str(payload.get("target_format") or "")
    opts = dict(payload.get("conversion_options") or {})
    scope_kind = str(payload.get("scope_kind") or "shared")
    scope_id = payload.get("scope_id")
    return {
        "id": payload.get("job_id"),
        "kind": kind,
        "family": family_of(kind, opts),
        "source_key": payload.get("source_key"),
        "derived_key": payload.get("derived_key"),
        "conversion_options": opts,
        "target_capability": payload.get("target_capability"),
        # The scope as the API's URLs name it: a personal scope is the caller's own ("user:me").
        "scope": "user:me" if scope_kind == "user" else (f"{scope_kind}:{scope_id}" if scope_id else scope_kind),
    }


def job_fields(submitted) -> dict:
    """The ``job_id`` part of a route's answer: the queued job's id, or -- for a claim the browser
    runs -- no job id and the claim (``browser_job``)."""
    if getattr(submitted, "run_on", None) == RUN_ON_BROWSER:
        return {"job_id": None, "browser_job": claim_document(submitted.payload)}
    return {"job_id": submitted.job_id}


# --- outputs -----------------------------------------------------------------------------------

#: Beside a component build's derived key (``formats/component.py``).
COMPONENT_REPORT_SUFFIX = ".report.json"
COMPONENT_ERROR_SUFFIX = ".error.json"
#: Beside a selection export's file (``selection_export.EXPORT_LOG_SUFFIX``).
EXPORT_LOG_SUFFIX = ".log.json"
#: Where a utility may write the overlay GLBs its viewer-ops point at.
UTILITY_OVERLAY_PREFIX = "_overlays/"
UTILITY_OVERLAY_OUTPUT = "overlay:"


class OutputError(ValueError):
    """An output this claim may not store (its message says why)."""


#: What :func:`output_target` answers: the key to write, its content encoding, and how the
#: storing side treats it -- ``"blob"`` (write the bytes), ``"run"`` (a clash run: written with its
#: target's index), ``"states"`` / ``"subjects"`` (filed, not written), ``"bbox"`` (merged into the
#: equipment type).
OutputTarget = tuple[str | None, str | None, str]


def output_target(claim: Mapping[str, Any], name: str) -> OutputTarget:
    """Where output ``name`` of ``claim`` goes. Raises :class:`OutputError` for a name the job
    kind does not produce. The keys are the ones the route composed (the claim's derived key and
    the options it set), never one the browser names -- except a utility's overlay, which must be
    a ``.glb`` under :data:`UTILITY_OVERLAY_PREFIX`."""
    kind = str(claim.get("kind") or "")
    opts = dict(claim.get("conversion_options") or {})
    derived = str(claim.get("derived_key") or "")
    mode = str(opts.get("mode") or "")
    if kind in ("clash_check", "clash_check_asset", "clash_check_group"):
        if name == "result":
            return derived, "gzip", "blob"
    elif kind in ("clash_detail", "clash_detail_group"):
        if name == "glb" and opts.get("glb_key"):
            return str(opts["glb_key"]), "gzip", "blob"
        if name == "stats":
            return derived, "gzip", "blob"
    elif kind == "clash_contacts":
        if mode in ("check", "annotate"):
            if name == "run":
                return derived, None, "run"
            if name == "states":
                return None, None, "states"
        elif mode == "detail":
            if name == "glb" and opts.get("glb_key"):
                return str(opts["glb_key"]), "gzip", "blob"
            if name == "stats":
                return derived, "gzip", "blob"
            if name == "subjects":
                return None, None, "subjects"
        elif mode == "subjects":
            if name == "subjects":
                return derived, "gzip", "subjects"
    elif kind == "component_build":
        if name in ("glb", "data"):
            return derived, "gzip", "blob"
        if name == "report" and mode in ("report", "subject"):
            return derived + COMPONENT_REPORT_SUFFIX, None, "blob"
        if name == "error" and mode in ("report", "subject"):
            return derived + COMPONENT_ERROR_SUFFIX, None, "blob"
    elif kind == "export_selection":
        if name == "file":
            return derived, None, "blob"
        if name == "log":
            return derived + EXPORT_LOG_SUFFIX, None, "blob"
    elif kind == "equipment_bbox":
        if name == "preview":
            return derived, "gzip", "blob"
        if name == "bbox":
            return None, None, "bbox"
    elif kind == "utility":
        if name == "payload":
            return derived, None, "blob"
        if name.startswith(UTILITY_OVERLAY_OUTPUT):
            key = name[len(UTILITY_OVERLAY_OUTPUT) :]
            if key.startswith(UTILITY_OVERLAY_PREFIX) and key.endswith(".glb") and ".." not in key and "//" not in key:
                return key, None, "blob"
            raise OutputError(f"a utility overlay must be a .glb under {UTILITY_OVERLAY_PREFIX!r} (got {key!r})")
    raise OutputError(f"a {kind} job{f' ({mode})' if mode else ''} produces no output {name!r}")


def _json_object(data: bytes, what: str) -> dict:
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OutputError(f"{what} is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise OutputError(f"{what} is not a JSON object")
    return doc


def validate_output(claim: Mapping[str, Any], name: str, data: bytes) -> None:
    """Check an output as the worker's own would be: a document parses, a GLB is a GLB, a clash
    result is for the claim's source and a run for its target. Raises :class:`OutputError`."""
    if not data:
        raise OutputError(f"output {name!r} is empty")
    kind = str(claim.get("kind") or "")
    opts = dict(claim.get("conversion_options") or {})
    _key, _encoding, how = output_target(claim, name)
    glb = name in ("glb", "preview") or name.startswith(UTILITY_OVERLAY_OUTPUT)
    if kind == "component_build" and name == "data":
        # A report build writes the GLB, a subject build the weld assembly's JSON.
        glb = str(opts.get("mode") or "") == "report"
    if glb and data[:4] != b"glTF":
        raise OutputError(f"output {name!r} is not a binary glTF")
    if kind == "component_build" and name == "data" and not glb:
        _json_object(data, "output 'data'")
    if name == "result":
        from ada.clash.result import ClashResultError, parse_clash_result

        try:
            result = parse_clash_result(data)
        except ClashResultError as exc:
            raise OutputError(f"not a clash result: {exc}") from exc
        expected = None
        if kind == "clash_check":
            expected = str(opts.get("source_key") or claim.get("source_key") or "")
        elif kind == "clash_check_group":
            expected = str(claim.get("source_key") or "")
        if expected and result.source_key != expected:
            raise OutputError(f"the clash result is for {result.source_key!r}, not {expected!r}")
    elif how == "run":
        from ada.clash.sidecar import SidecarError, parse_run, run_key

        try:
            run = parse_run(data)
        except SidecarError as exc:
            raise OutputError(f"not a clash run: {exc}") from exc
        if run_key(dict(run.target), run.run_id) != str(claim.get("derived_key") or ""):
            raise OutputError("the clash run is not the one this job writes")
    elif how in ("states", "subjects", "bbox") or name in ("stats", "report", "error", "payload", "log"):
        doc = json.loads(data) if how == "states" else _json_object(data, f"output {name!r}")
        if how == "states" and not isinstance(doc, list):
            raise OutputError("output 'states' is not a JSON list")
        if how == "bbox":
            for k in ("lx", "ly", "lz"):
                if not isinstance(doc.get(k), (int, float)):
                    raise OutputError(f"output 'bbox' needs a number {k!r}")

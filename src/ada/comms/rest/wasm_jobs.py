"""Core worker jobs run in the browser (Pyodide): the worker handler's own work, on the same job.

The viewer claims a core job (:mod:`.browser_jobs`): the route composed it exactly as for a worker
-- derived key, options, the source to read -- and the browser runs it here, in its Pyodide
worker, with a synchronous storage facade that reads the scope's blobs over the API. What a handler
uploads, :func:`run` returns instead, by output name; the API stores each output where the route
said it goes (``browser_jobs.output_target``).

THE SAME CODE, NOT A PORT. Every kind calls the function its worker handler calls:

* ``clash_check`` / ``clash_check_asset`` / ``clash_check_group``: ``ada.clash.run_clash_check``
  on the source's members (``native_members.load_members_or_model``), or
  ``clash_check_from_asset_node`` / ``clash_check_group``;
* ``clash_detail`` / ``clash_detail_group``: ``ada.clash.detail.joints_by_id`` + ``build_detail``;
* ``clash_contacts``: :func:`.clash_contacts_work.contacts_work` (check, annotate, detail,
  subjects) -- the convex-contact pass is adacpp's coal C++, which the Pyodide wheel carries;
* ``component_build``: ``ada.api.connections.build_component`` / ``library.build_with_report`` /
  ``build_subject``;
* ``export_selection``: ``selection_export.export_source_selection``;
* ``equipment_bbox``: :func:`.equipment_geometry.infer_equipment_geometry`;
* ``utility``: :func:`.utility.run_utility` (the bundled ``@utility`` handlers).

Light at import (stdlib only): the API imports :data:`KINDS` from here; the work imports run
lazily, in the browser.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
from collections.abc import Callable, Mapping
from typing import Any

#: The kinds :func:`run` runs (``browser_jobs.BROWSER_KINDS`` claims only these).
KINDS: tuple[str, ...] = (
    "clash_check",
    "clash_check_asset",
    "clash_check_group",
    "clash_contacts",
    "clash_detail",
    "clash_detail_group",
    "component_build",
    "export_selection",
    "equipment_bbox",
    "utility",
)

Progress = Callable[[str, float], None]


class _Recording:
    """The facade a job reads through, recording what it writes (a utility's overlay GLB) rather
    than storing it: the API stores outputs, the browser does not write the scope."""

    def __init__(self, storage):
        self._storage = storage
        self.written: dict[str, bytes] = {}

    def get_bytes(self, key: str) -> bytes:
        if key in self.written:
            return self.written[key]
        return self._storage.get_bytes(key)

    def list_keys(self, prefix: str = "") -> list[str]:
        return sorted({*self._storage.list_keys(prefix), *(k for k in self.written if k.startswith(prefix))})

    def get_many(self, keys) -> dict[str, bytes | None]:
        out: dict[str, bytes | None] = {}
        for key in keys:
            try:
                out[key] = self.get_bytes(key)
            except (FileNotFoundError, KeyError, OSError):
                out[key] = None
        return out

    def fetch_to_path(self, key: str, dest):
        p = pathlib.Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.get_bytes(key))
        return dest

    def provider_denial(self, key: str):
        fn = getattr(self._storage, "provider_denial", None)
        return fn(key) if fn else None

    def put_bytes(self, key: str, data, content_encoding: str | None = None, pre_compressed: bool = False) -> None:
        self.written[str(key)] = bytes(data)

    def put_path(self, key: str, src, content_encoding: str | None = None, pre_compressed: bool = False) -> dict:
        raw = pathlib.Path(src).read_bytes()
        self.put_bytes(key, raw)
        return {"key": key, "size": len(raw)}


def _ensure_backend() -> None:
    import ada.cad

    try:
        ada.cad.select_backend(prefer="adacpp")
    except Exception:  # noqa: BLE001 - the job names what it could not do
        pass


def _adapy_version() -> str:
    try:
        import ada

        return getattr(ada, "__version__", "unknown")
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return "unknown"


def _capability_of(reg) -> str | None:
    """Which pool serves a spec, as a worker's heartbeat would answer: None for a built-in, else
    the capability its registration names (a spec a baked plugin wheel registered here)."""
    from ada.clash.builtin_specs import BUILTIN_SPEC_NAMES

    if reg.spec.name in BUILTIN_SPEC_NAMES:
        return None
    return getattr(reg.spec, "capability", None)


def _source_file(storage, key: str, tmp: pathlib.Path) -> pathlib.Path:
    """The blob at ``key`` as a local file with the key's own extension (the readers dispatch on
    it, as the worker's pre-download keeps it)."""
    suffix = "".join(pathlib.PurePosixPath(key).suffixes[-1:]) or ""
    path = tmp / f"source{suffix}"
    path.write_bytes(storage.get_bytes(key))
    return path


def _clash_check(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from ada.cadit.ifc.read.native_members import load_members_or_model
    from ada.clash import ClashOptions, run_clash_check
    from ada.clash.builtin_specs import register_builtin_specs

    from .converters.ada_load import _load_with_ada

    opts = dict(claim.get("conversion_options") or {})
    source_key = str(opts.get("source_key") or claim.get("source_key") or "")
    options = ClashOptions.from_dict(opts.get("options") or {})
    register_builtin_specs()
    with tempfile.TemporaryDirectory(prefix="ada_clash_") as tmp:
        progress("loading", 0.15)
        src = _source_file(storage, str(claim.get("source_key") or source_key), pathlib.Path(tmp))
        model = load_members_or_model(src, src.suffix.lower(), _load_with_ada)
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        progress("clash", 0.55)
        result = run_clash_check(
            model,
            source_key=source_key,
            options=options,
            capability_of=_capability_of,
            source_sha256=sha,
            provenance={"adapy_version": _adapy_version(), "executed_in": "browser"},
        )
    doc = result.to_json()
    return {"result": doc.encode("utf-8") if isinstance(doc, str) else doc}


def _clash_check_asset(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from ada.clash import ClashOptions
    from ada.clash.from_asset import clash_check_from_asset_node

    opts = dict(claim.get("conversion_options") or {})
    progress("clash", 0.4)
    document = clash_check_from_asset_node(
        collection=str(opts.get("collection") or ""),
        subject=str(opts.get("subject") or ""),
        storage=storage,
        revision=opts.get("revision") or None,
        node=opts.get("node") or None,
        options=ClashOptions.from_dict(opts.get("options") or {}),
    )
    return {"result": json.dumps(document).encode("utf-8")}


def _clash_check_group(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from ada.clash import ClashOptions
    from ada.clash.group_model import clash_check_group

    opts = dict(claim.get("conversion_options") or {})
    progress("clash", 0.4)
    document = clash_check_group(
        dict(opts.get("group") or {}),
        token=str(opts.get("group_token") or ""),
        options=ClashOptions.from_dict(opts.get("options") or {}),
        storage=storage,
        capability_of=_capability_of,
        provenance={"adapy_version": _adapy_version(), "executed_in": "browser"},
    )
    return {"result": json.dumps(document).encode("utf-8")}


def _clash_detail(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from ada.api.connections.spec import get_registered
    from ada.clash import ClashOptions, ClashResultError, parse_clash_result
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.detail import build_detail, detail_progress, joints_by_id

    from .converters.ada_load import _load_with_ada

    opts = dict(claim.get("conversion_options") or {})
    joint_ids = [str(j) for j in (opts.get("joint_ids") or [])]
    spec_name = str(opts.get("spec") or "")
    cached = parse_clash_result(storage.get_bytes(str(opts["result_key"])))
    options = ClashOptions.from_dict(cached.options)
    register_builtin_specs()
    try:
        registered = get_registered(spec_name)
    except KeyError as exc:
        raise LookupError(f"spec {spec_name!r} is not registered in this browser") from exc
    progress("loading", 0.2)
    with tempfile.TemporaryDirectory(prefix="ada_detail_") as tmp:
        if claim.get("kind") == "clash_detail_group":
            from ada.clash.group_model import build_group_model

            group = cached.provenance.get("group")
            if not isinstance(group, dict):
                raise ClashResultError("this clash result was not checked over a group (no provenance.group)")
            model = build_group_model(group, storage=storage).model
        else:
            src = _source_file(storage, str(claim.get("source_key") or cached.source_key), pathlib.Path(tmp))
            model = _load_with_ada(src, src.suffix.lower())
        by_id = joints_by_id(model, options)
    absent = [jid for jid in joint_ids if jid not in by_id]
    if absent:
        raise ValueError(
            "joint id(s) not reproducible from this source at these options: "
            f"{', '.join(absent)} -- the cached result and the source may have drifted apart"
        )
    progress("detail", 0.55)
    glb, stats = build_detail(
        registered,
        spec_name=spec_name,
        joint_ids=joint_ids,
        by_id=by_id,
        gen_options=dict(opts.get("options") or {}),
        on_progress=detail_progress(progress),
    )
    out = {"stats": json.dumps(stats).encode("utf-8")}
    if opts.get("glb_key"):
        out["glb"] = glb
    return out


def _clash_contacts(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from .clash_contacts_work import contacts_work

    return contacts_work(dict(claim.get("conversion_options") or {}), storage, progress, _capability_of)


def _component_build(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    opts = dict(claim.get("conversion_options") or {})
    spec_name = opts.get("spec_name")
    if not spec_name:
        raise ValueError("conversion_options.spec_name is required for component_build")
    inputs = opts.get("inputs") or {}
    mode = opts.get("mode")
    progress("build", 0.4)
    with tempfile.TemporaryDirectory(prefix="ada_component_") as tmp:
        if mode in ("report", "subject"):
            from ada.api.connections.library import (
                build_subject,
                build_with_report,
                format_user_traceback,
                is_declined,
            )

            out = pathlib.Path(tmp) / ("out.glb" if mode == "report" else "out.json")
            try:
                report = (build_with_report if mode == "report" else build_subject)(spec_name, inputs, out)
            except Exception as exc:  # noqa: BLE001 - every failure is reported to the studio
                failure = {
                    "error": str(exc) or type(exc).__name__,
                    "traceback": format_user_traceback(exc),
                    "declined": is_declined(exc),
                }
                raise _Failed(failure["error"], {"error": json.dumps(failure).encode("utf-8")}) from exc
            return {"data": out.read_bytes(), "report": json.dumps(report, default=str).encode("utf-8")}

        from ada.api.connections import build_component

        conn = build_component(
            spec_name=spec_name, inputs=inputs, name=opts.get("name"), **(opts.get("extra_handler_kwargs") or {})
        )
        glb = pathlib.Path(tmp) / "out.glb"
        conn.to_gltf(glb)
        return {"glb": glb.read_bytes()}


def _export_selection(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from .selection_export import (
        SELECTION_EXPORT_FORMATS,
        SelectionExportError,
        export_source_selection,
        failure_log,
    )

    opts = dict(claim.get("conversion_options") or {})
    fmt = str(opts.get("format") or "")
    if fmt not in SELECTION_EXPORT_FORMATS:
        raise ValueError(f"unsupported export format {fmt!r} (expected one of {sorted(SELECTION_EXPORT_FORMATS)})")
    with tempfile.TemporaryDirectory(prefix="ada_export_") as tmp:
        src = _source_file(storage, str(claim.get("source_key") or opts.get("source_key") or ""), pathlib.Path(tmp))
        out = pathlib.Path(tmp) / f"out{SELECTION_EXPORT_FORMATS[fmt]}"
        progress("export", 0.3)
        try:
            export_source_selection(
                src,
                fmt,
                out,
                element=opts.get("element") or None,
                path=opts.get("path") or (),
                export_options=opts.get("export_options"),
            )
        except SelectionExportError as exc:
            doc = failure_log(None, exc)
            raise _Failed(str(exc), {"log": json.dumps(doc).encode("utf-8")} if doc is not None else {}) from exc
        return {"file": out.read_bytes()}


def _equipment_bbox(claim: Mapping, storage, progress: Progress) -> dict[str, bytes]:
    from .equipment_geometry import infer_equipment_geometry

    opts = dict(claim.get("conversion_options") or {})
    cad_key = str(opts.get("cad_key") or "")
    if not opts.get("type_id") or not cad_key:
        raise ValueError("conversion_options.type_id and cad_key are required for equipment_bbox")
    progress("build", 0.4)
    bbox, preview = infer_equipment_geometry(
        storage.get_bytes(cad_key),
        pathlib.PurePosixPath(cad_key).suffix.lower(),
        z_up=bool(opts.get("cad_z_up", True)),
    )
    return {"preview": bytes(preview), "bbox": json.dumps(bbox).encode("utf-8")}


def _utility(claim: Mapping, storage: _Recording, progress: Progress) -> dict[str, bytes]:
    from . import utilities  # noqa: F401 - registers the bundled @utility handlers
    from .utility import run_utility

    opts = dict(claim.get("conversion_options") or {})
    name = opts.get("utility_name")
    if not name:
        raise ValueError("conversion_options.utility_name is required for a utility job")
    source_key = str(claim.get("source_key") or "")
    progress("utility", 0.3)
    with tempfile.TemporaryDirectory(prefix="ada_utility_") as tmp:
        src = _source_file(storage, source_key, pathlib.Path(tmp))
        payload = run_utility(
            name,
            src,
            storage=storage,
            scope=claim.get("scope"),
            on_progress=progress,
            source_key=source_key,
            kwargs=opts.get("kwargs") or {},
        )
    return {"payload": json.dumps(payload).encode("utf-8")}


class _Failed(Exception):
    """A job that failed with outputs the user still needs (a builder's report of why, an
    export's machine-readable reason): stored, then the job ends as an error."""

    def __init__(self, message: str, outputs: dict[str, bytes]):
        super().__init__(message)
        self.outputs = outputs


_WORK: dict[str, Callable[[Mapping, Any, Progress], dict[str, bytes]]] = {
    "clash_check": _clash_check,
    "clash_check_asset": _clash_check_asset,
    "clash_check_group": _clash_check_group,
    "clash_contacts": _clash_contacts,
    "clash_detail": _clash_detail,
    "clash_detail_group": _clash_detail,
    "component_build": _component_build,
    "export_selection": _export_selection,
    "equipment_bbox": _equipment_bbox,
    "utility": _utility,
}


def run(claim: Mapping[str, Any] | str, storage, progress: Progress | None = None) -> dict[str, Any]:
    """Run one claimed job (``browser_jobs.claim_document``) and return
    ``{"status": "done" | "error", "error": str | None, "outputs": {name: bytes}}``.

    ``storage`` is the synchronous facade the worker hands a handler (``get_bytes``,
    ``list_keys``, ...); what the job writes through it comes back as outputs too (a utility's
    overlay as ``overlay:<key>``). A failure is an ``error`` with the outputs it still produced,
    never an exception, so the caller always has something to report."""
    if isinstance(claim, (str, bytes)):
        claim = json.loads(claim)
    kind = str(claim.get("kind") or "")
    work = _WORK.get(kind)
    prog = progress or (lambda _stage, _frac: None)
    if work is None:
        return {"status": "error", "error": f"the browser does not run {kind!r} jobs", "outputs": {}}
    recording = _Recording(storage)
    _ensure_backend()
    try:
        outputs = dict(work(claim, recording, prog))
    except _Failed as exc:
        return {"status": "error", "error": str(exc), "outputs": dict(exc.outputs)}
    except Exception as exc:  # noqa: BLE001 - reported to the API as the job's error
        import traceback

        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
            "outputs": {},
        }
    for key, data in recording.written.items():
        outputs[f"overlay:{key}"] = data
    return {"status": "done", "error": None, "outputs": outputs}


def run_json(claim_json: str, storage, progress: Progress | None = None) -> tuple[str, dict[str, bytes]]:
    """:func:`run` for the Pyodide worker: the status document as JSON, the outputs beside it."""
    res = run(claim_json, storage, progress)
    outputs = res.pop("outputs")
    return json.dumps(res), outputs


__all__ = ["KINDS", "run", "run_json"]

"""The work of a ``clash_contacts`` job (``formats/clash_contacts.py``), as a plain function.

``contacts_work(opts, storage, progress, capability_of)`` reads what it needs through a synchronous
storage facade and returns the job's outputs by name. Nothing here touches the queue or a database,
so the worker, a queue-less server (``local_jobs.start_clash_contacts``) and the browser
(``ada.comms.rest.wasm_jobs``) run the same code and cannot drift.
"""

from __future__ import annotations

import json
from typing import Callable

from ada.clash.rebuild import (
    RebuildError,
    check_geometry_unchanged,
    rebuild_run_members,
)
from ada.config import logger

CONTACT_MODES = ("check", "annotate", "detail", "subjects")


class ContactsJobError(RebuildError):
    """A request the job cannot answer; its message is the whole story."""


# Moved to ``ada.clash.rebuild`` (public there); the old names stay for callers that import them here.
_rebuild_members = rebuild_run_members
_check_geometry_unchanged = check_geometry_unchanged

#: Provenance one annotate writes about where its members were read from; dropped by the next.
_GEOMETRY_PROVENANCE = ("geometry_provider", "geometry_remap", "geometry_unmatched", "geometry_warnings")


def _no_geometry_detail(exc: BaseException) -> dict | None:
    """The machine-readable form of a failure that is "the provider has no geometry for these
    members" (``NoProviderGeometryError``), found through the ``ContactsJobError`` a group run wraps
    it in; None for any other failure."""
    from ada.clash.group_model import NoProviderGeometryError

    if isinstance(exc, NoProviderGeometryError):
        d = exc.detail()
        return {k: d[k] for k in ("code", "geometry_provider", "collections", "unmatched")}
    for part in getattr(exc, "parts", ()):  # a group run: the first part's cause stands for all
        found = _no_geometry_detail(part)
        if found:
            return found
    return None


def _annotate(run, storage, capability_of, progress, geometry_provider: str | None = None):
    """The run with its specs and roles resolved. A target that does not rebuild still gets ROLES --
    from the members' meshes in the GLB the run measured -- and says why it has no specs.

    ``geometry_provider`` reads the members from that provider's publish of the same elements
    (matched by name, ``ada.clash.geometry_source``) instead of each member's own provider -- the
    way a mesh-only provider's run still gets member-bound specs and roles. A file has no provider,
    so a file run ignores it."""
    from dataclasses import replace

    from ada.clash import contacts as cc

    progress("members", 0.5)
    error = None
    error_detail: dict | None = None
    geometry: dict = {}
    if run.target.get("kind") == "file":
        geometry_provider = None
    try:
        model, geometry = rebuild_run_members(run, storage, geometry_provider)
    except Exception as exc:  # noqa: BLE001 - a run without specs is still a run
        logger.warning(f"clash contacts: specs not resolved, the target did not rebuild: {exc}")
        model, error = None, f"{type(exc).__name__}: {exc}"
        error_detail = _no_geometry_detail(exc)
        if geometry_provider:
            geometry = {"geometry_provider": geometry_provider}
    annotated, unbound = cc.annotate_applicable(
        run, model, capability_of, vertices=lambda: cc.member_vertices(run, storage.get_bytes)
    )
    provenance = {k: v for k, v in annotated.provenance.items() if k not in _GEOMETRY_PROVENANCE}
    provenance.update(geometry)
    if error is not None:
        # `code`/`geometry_provider`/`unmatched` (the ``no_provider_geometry`` shape) when the provider
        # only has a tree here: what a client needs to offer "request the geometry".
        provenance["applicability"] = {"error": error, **(error_detail or {})}
    elif unbound:
        logger.info(f"clash contacts: {unbound} clash(es) of run {run.run_id} did not bind to rebuilt members")
    return replace(annotated, provenance=provenance)


#: The most elements one annotate fingerprints for the element state (``ada.assoc``): beyond it the
#: links are filed without fingerprints, and none of them reads as stale or orphaned.
MAX_FINGERPRINTED = 250_000


def _identified(run, storage, glb: bytes | None = None):
    """``(run, states)``: the run with each member's element key, ancestors and fingerprint in
    ``provenance["elements"]`` (``ada.assoc.resolve``), and one element-state snapshot per GLB it
    measured. Best effort: a run whose GLB cannot be read keeps what it had, and no state."""
    from dataclasses import replace

    from ada.assoc.fingerprints import element_fingerprints
    from ada.assoc.resolve import (
        ELEMENTS_PROVENANCE,
        glb_json,
        member_identities,
        resolver_for,
    )
    from ada.assoc.store import utc_now
    from ada.clash.sidecar import target_token

    group = run.target.get("kind") == "group"
    parts = list(run.parts) if group else [{"key": run.geometry.get("key"), "source": dict(run.target)}]
    jsons, fps, states = [], [], []
    seen_at = utc_now()
    for k, part in enumerate(parts):
        try:
            data = glb if (glb is not None and not group) else storage.get_bytes(str(part.get("key") or ""))
            js = glb_json(data)
            by_id = element_fingerprints(data) if js else {}
        except Exception as exc:  # noqa: BLE001 - a run is still a run without its element keys
            logger.info(f"clash contacts: part {k} of run {run.run_id} not fingerprinted: {exc}")
            jsons.append(None)
            fps.append(None)
            continue
        jsons.append(js)
        fps.append(by_id)
        if not by_id or len(by_id) > MAX_FINGERPRINTED:
            continue
        target = dict(part["source"])
        resolver = resolver_for(target, js)
        keyed: dict[str, str] = {}
        for eid, fp in by_id.items():
            try:
                keyed[resolver.identity(eid)[0]] = fp
            except (KeyError, ValueError):
                continue
        states.append(
            {
                "source": target_token(target),
                "seen_at": seen_at,
                "revision": target.get("revision"),
                "geometry_sha256": part.get("sha256") or run.geometry.get("sha256"),
                "fps": keyed,
            }
        )
    identities = member_identities(run, jsons if group else jsons[0], fps if group else fps[0])
    provenance = {**dict(run.provenance), ELEMENTS_PROVENANCE: identities}
    return replace(run, provenance=provenance), states


def _run_outputs(run, storage, glb: bytes | None = None) -> dict[str, bytes]:
    run, states = _identified(run, storage, glb)
    out = {"run": run.to_json().encode("utf-8")}
    if states:
        out["states"] = json.dumps(states).encode("utf-8")
    return out


def _subjects_work(opts: dict, storage, progress) -> dict[str, bytes]:
    """``subjects``: write the simulation subject of every built connection of a detail model (or of
    the ``cks`` named), rebuilding its members and joints (``ada.clash.subjects``)."""
    from ada.clash.subjects import weld_assemblies_for_detail

    from .clash_details import detail_paths, parse_record

    paths = detail_paths(str(opts.get("detail_id") or ""))
    progress("record", 0.1)
    record = parse_record(storage.get_bytes(paths.record))
    stats = json.loads(storage.get_bytes(paths.stats))
    progress("subjects", 0.3)
    cks = [str(c) for c in opts.get("cks") or ()] or None
    out = weld_assemblies_for_detail(record, stats, storage, record_key=paths.record, cks=cks)
    for s in out["subjects"]:
        s["members"] = [list(m) for m in s["members"]]
    return {"subjects": json.dumps(out).encode("utf-8")}


def contacts_work(opts: dict, storage, progress: Callable[[str, float], None], capability_of=None) -> dict[str, bytes]:
    """One ``clash_contacts`` job's work. Returns its outputs by name: ``run`` (the run document) and
    ``states`` (its element fingerprints, ``ada.assoc``) for ``check`` / ``annotate``; ``glb`` and
    ``stats`` (and ``subjects`` with ``make_subjects``) for ``detail``; ``subjects`` for ``subjects``."""
    from ada.clash.contacts import check_glb, founds_by_id
    from ada.clash.sidecar import parse_run

    mode = opts.get("mode")
    if mode == "check":
        glb_key = str(opts.get("glb_key") or "")
        if not glb_key:
            raise ContactsJobError("a check needs the glb_key of the GLB it measures")
        progress("geometry", 0.1)
        glb = storage.get_bytes(glb_key)
        progress("clash", 0.3)
        run = check_glb(
            glb,
            target=dict(opts.get("target") or {}),
            glb_key=glb_key,
            up_axis=str(opts.get("up_axis") or "z"),
            options=dict(opts.get("options") or {}),
            run_id=opts.get("run_id"),
        )
        # `annotate: false` -- the caller annotates next, through a geometry provider it is still
        # asking the user about; resolving the specs here first would be a read thrown away.
        if opts.get("annotate", True):
            run = _annotate(run, storage, capability_of, progress, opts.get("geometry_provider") or None)
        return _run_outputs(run, storage, glb)

    if mode == "subjects":
        return _subjects_work(opts, storage, progress)

    run_key = str(opts.get("run_key") or "")
    if not run_key:
        raise ContactsJobError(f"{mode!r} needs the run_key of a clash run")
    run = parse_run(storage.get_bytes(run_key))

    if mode == "annotate":
        annotated = _annotate(run, storage, capability_of, progress, opts.get("geometry_provider") or None)
        return _run_outputs(annotated, storage)

    if mode != "detail":
        raise ContactsJobError(f"unknown clash_contacts mode {mode!r} (one of {', '.join(CONTACT_MODES)})")

    from ada.api.connections.spec import get_registered
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.detail import build_detail, detail_progress

    ids = [str(i) for i in opts.get("contact_ids") or ()]
    spec_name = str(opts.get("spec") or "")
    if not ids or not spec_name:
        raise ContactsJobError("a detail needs contact_ids and a spec")
    register_builtin_specs()
    try:
        registered = get_registered(spec_name)
    except KeyError as exc:
        raise ContactsJobError(
            f"spec {spec_name!r} is not registered in this process -- it must be importable on the pool "
            "a detail for it is routed to"
        ) from exc

    progress("geometry", 0.1)
    check_geometry_unchanged(run, storage)
    progress("members", 0.3)
    model, _geometry = rebuild_run_members(run, storage, opts.get("geometry_provider") or None)
    by_id, problems = founds_by_id(run, model, ids)
    if not by_id:
        raise ContactsJobError(
            "none of the clashes bound to the rebuilt members: "
            + "; ".join(f"{cid}: {why}" for cid, why in problems.items())
        )
    progress("detail", 0.55)
    from ada.assoc.rows import connection_ck, member_identities_of

    identities = member_identities_of(run)
    built: list = []
    glb_bytes, stats = build_detail(
        registered,
        spec_name=spec_name,
        joint_ids=[cid for cid in ids if cid in by_id],
        by_id=by_id,
        gen_options=dict(opts.get("options") or {}),
        on_progress=detail_progress(progress),
        ck_of=lambda rec: connection_ck(rec, run=run, identities=identities),
        keep_built=built,
    )
    if problems:
        stats.setdefault("skipped", []).extend(f"{cid}: {why}" for cid, why in problems.items())
        # A clash that did not bind to the rebuilt members was never handed to a builder.
        stats.setdefault("not_built", []).extend(
            {"joint_id": cid, "kind": "failed", "reason": why, "declined": []} for cid, why in problems.items()
        )
    stats["run_id"] = run.run_id
    out = {"glb": glb_bytes, "stats": json.dumps(stats).encode("utf-8")}
    if opts.get("make_subjects") and opts.get("detail_id"):
        # The subjects of the connections just built, made of these very objects (``ada.clash.subjects``).
        from ada.clash.subjects import weld_assemblies_for_detail

        from .clash_details import detail_paths

        paths = detail_paths(str(opts["detail_id"]))
        record = {
            "detail_id": paths.detail_id,
            "spec": spec_name,
            "source": {"kind": "run", "run_key": run_key},
            "options": dict(opts.get("options") or {}),
        }
        try:
            made = weld_assemblies_for_detail(record, stats, storage, record_key=paths.record, run=run, built=built)
        except Exception as exc:  # noqa: BLE001 - the model is made; its subjects can be made again
            logger.warning(f"clash contacts: the subjects of detail {paths.detail_id} were not made: {exc}")
            made = {"subjects": [], "errors": [{"ck": None, "error": f"{type(exc).__name__}: {exc}"}]}
        for s in made["subjects"]:
            s["members"] = [list(m) for m in s["members"]]
        out["subjects"] = json.dumps(made).encode("utf-8")
    return out

"""Per-element associations (``ada.assoc``): what has been found, built and simulated under a node.

* ``POST /scopes/{scope}/elements/links/query`` -- the links under some nodes (or a saved group's
  members, or exact elements, or connections): summary counts, and the rows grouped per ck (or
  contact pair), paged by a keyset cursor without a cap.
* ``GET /scopes/{scope}/elements/{key}/links`` -- one element's links (its own and its descendants').
* ``GET /scopes/{scope}/connections/{ck}`` -- one connection's links: its members, welds, subject
  and simulation runs.
* ``POST /scopes/{scope}/elements/subjects`` -- make the missing simulation subjects of the built
  connections under some nodes (one ``clash_contacts`` ``subjects`` job per detail model).
* ``POST /scopes/{scope}/simulations/batch`` -- start a simulation of every connection under some
  nodes that a policy says needs one, as the caller, all in one batch, never twice (an intent per
  connection, taken atomically).
* ``GET`` / ``POST /scopes/{scope}/elements/links/reindex`` -- whether the scope's links have been
  built from its documents, and building them again.

A node is named by a ``NodeRef``: ``{key}`` (an element key), ``{collection, node_id, provider?}``
(a published node), or ``{target, path?, element?, element_id?}`` (a member of a model, as a saved
group stores it). Without Postgres every route still answers, from the files backend.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.assoc.keys import file_key, is_element_key, node_key
from ada.assoc.records import ACTIVE_SIM_STATUSES, params_hash
from ada.assoc.resolve import target_anchor
from ada.assoc.store import MAX_LIMIT, Intent, LinkQuery

from .. import auth as auth_module
from ..assoc_links import link_store
from ..assoc_reindex import ensure_indexed, reindex_scope, reindex_state, start_reindex
from ..auth import User
from ..scope import Scope
from .deps import RestContext, optional_pool, rest_context, scope_from_path

router = APIRouter()

_CK = re.compile(r"^[0-9a-f]{16}$")
_KINDS = ("contact", "connection", "weld", "subject", "sim_run")
#: The most connections one batch call starts; ``next`` goes on from there.
BATCH_PAGE = 100
POLICIES = ("missing", "failed", "all")
GROUPS_BLOB_KEY = "_groups/groups.json"


def _str_list(raw: Any, what: str) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or any(not isinstance(x, str) or not x.strip() for x in raw):
        raise HTTPException(status_code=400, detail=f"'{what}' must be a list of strings")
    return [x.strip() for x in raw]


def keys_of_ref(ref: Any) -> list[str]:
    """The element keys a NodeRef names (a published node by both its provider and none)."""
    if not isinstance(ref, dict):
        raise HTTPException(status_code=400, detail="every node must be an object")
    if ref.get("key"):
        key = str(ref["key"])
        if not is_element_key(key):
            raise HTTPException(status_code=400, detail=f"{key!r} is not an element key")
        return [key]
    if ref.get("collection") and ref.get("node_id"):
        provider = str(ref.get("provider") or "")
        out = [node_key(provider, ref["collection"], ref["node_id"])]
        return out + ([node_key("", ref["collection"], ref["node_id"])] if provider else [])
    target = ref.get("target")
    if isinstance(target, dict):
        return _member_keys(ref)
    raise HTTPException(
        status_code=400, detail="a node is {key}, {collection, node_id, provider?} or {target, path?, element?}"
    )


def _member_keys(member: dict) -> list[str]:
    """A saved group's member (``{target, element, path, source?}``) as element keys."""
    target = member.get("target") or {}
    kind = target.get("kind")
    source = member.get("source") if isinstance(member.get("source"), dict) else {}
    element_id = member.get("element_id") or (source.get("element_id") if source.get("kind") == "stored" else None)
    path = member.get("path") if isinstance(member.get("path"), list) else None
    whole = member.get("element") is None and not path
    try:
        if kind == "file" and target.get("source_key"):
            if whole:
                return [file_key(target["source_key"])]
            return [file_key(target["source_key"], path=path or [member.get("element")])]
        if kind == "node" and target.get("collection"):
            provider = str(target.get("provider") or "")
            providers = list(dict.fromkeys([provider, ""]))
            anchor = target.get("node") or target.get("subject")
            if element_id:
                return [node_key(p, target["collection"], element_id) for p in providers]
            if whole:
                return [node_key(p, target["collection"], anchor) for p in providers]
            return [node_key(p, target["collection"], anchor, path=path or [member.get("element")]) for p in providers]
    except ValueError:
        return []
    anchor = target_anchor(target)
    return [anchor] if anchor else []


async def _group_keys(ctx: RestContext, scope_obj: Scope, group_id: str) -> list[str]:
    try:
        doc = json.loads(await ctx.storage.get_bytes(scope_obj, GROUPS_BLOB_KEY))
    except (FileNotFoundError, KeyError, ValueError):
        doc = None
    groups = (doc or {}).get("groups") if isinstance(doc, dict) else None
    group = next((g for g in groups or () if isinstance(g, dict) and str(g.get("id")) == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail=f"no saved group {group_id!r} in this scope")
    keys: list[str] = []
    for m in group.get("members") or ():
        if isinstance(m, dict):
            keys.extend(_member_keys(m))
    return list(dict.fromkeys(keys))


async def _under(ctx: RestContext, scope_obj: Scope, body: dict) -> list[str] | None:
    """The keys a body's ``nodes`` / ``group_id`` name; None when it names neither."""
    keys: list[str] = []
    named = False
    if body.get("nodes") is not None:
        if not isinstance(body["nodes"], list):
            raise HTTPException(status_code=400, detail="'nodes' must be a list of nodes")
        named = True
        for ref in body["nodes"]:
            keys.extend(keys_of_ref(ref))
    if body.get("group_id"):
        named = True
        keys.extend(await _group_keys(ctx, scope_obj, str(body["group_id"])))
    return list(dict.fromkeys(keys)) if named else None


def _query_from(body: dict, under: list[str] | None) -> LinkQuery:
    kinds = _str_list(body.get("kinds"), "kinds")
    if kinds is not None and any(k not in _KINDS for k in kinds):
        raise HTTPException(status_code=400, detail=f"'kinds' are among {', '.join(_KINDS)}")
    cks = _str_list(body.get("cks"), "cks")
    if cks is not None and any(not _CK.match(c) for c in cks):
        raise HTTPException(status_code=400, detail="'cks' are connection keys (16 hex)")
    try:
        limit = int(body.get("limit") or 100)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="'limit' must be a number") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise HTTPException(status_code=400, detail=f"'limit' is 1..{MAX_LIMIT}")
    return LinkQuery(
        under=under,
        element_keys=_str_list(body.get("keys"), "keys"),
        cks=cks,
        kinds=kinds,
        include_stale=bool(body.get("include_stale", True)),
        latest_only=bool(body.get("latest_only", False)),
        cursor=str(body["cursor"]) if body.get("cursor") else None,
        limit=limit,
    )


async def _answer(ctx: RestContext, request: Request, scope_obj: Scope, q: LinkQuery, extra: dict) -> dict:
    pool = optional_pool(request)
    reindexing = await ensure_indexed(ctx.storage, scope_obj, pool=pool)
    page = await link_store(ctx.storage, scope_obj, pool).query(q)
    active = any(
        d["kind"] == "sim_run" and (d.get("data") or {}).get("status") in ACTIVE_SIM_STATUSES
        for g in page["groups"]
        for d in g["rows"]
    )
    return {**page, **extra, "active": active, "reindexing": reindexing}


@router.post("/scopes/{scope}/elements/links/query")
async def api_element_links_query(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The links under some nodes. Body ``{nodes?: [NodeRef], group_id?, keys?, cks?, kinds?,
    include_stale? (true), latest_only? (false), cursor?, limit? (100, at most 500)}``: ``nodes`` /
    ``group_id`` -- every element under them; ``keys`` -- exactly these elements; ``cks`` -- these
    connections; ``kinds`` among contact, connection, weld, subject, sim_run.

    ``{summary: {rows, elements, groups, kinds, sim_runs, stale, orphan, superseded}, groups: [{group,
    ck, kinds, rows}], next, keys, active, reindexing}``: ``summary`` counts every matching row;
    ``groups`` is one page, ``next`` the cursor of the next (null at the end); each row carries its
    ``stale`` / ``orphan`` / ``superseded`` flags. ``active``: a run on the page is queued or running
    (worth asking again). ``reindexing``: the scope's links are being built from its documents for
    the first time, so the answer may still grow."""
    under = await _under(ctx, scope_obj, body)
    if under is not None and not under:
        raise HTTPException(status_code=404, detail="the nodes named no element")
    q = _query_from(body, under)
    if under is None and q.element_keys is None and q.cks is None:
        raise HTTPException(status_code=400, detail="name 'nodes', 'group_id', 'keys' or 'cks'")
    return JSONResponse(await _answer(ctx, request, scope_obj, q, {"keys": under or q.element_keys or []}))


@router.get("/scopes/{scope}/elements/{key:path}/links")
async def api_element_links(
    key: str,
    request: Request,
    kinds: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    latest_only: bool = False,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One element's links and its descendants', as the query answers them (``key``: an element key,
    URL-encoded; ``kinds`` comma-separated)."""
    if not is_element_key(key):
        raise HTTPException(status_code=400, detail=f"{key!r} is not an element key")
    body = {"kinds": kinds.split(",") if kinds else None, "cursor": cursor, "limit": limit, "latest_only": latest_only}
    q = _query_from(body, [key])
    return JSONResponse(await _answer(ctx, request, scope_obj, q, {"keys": [key]}))


@router.get("/scopes/{scope}/connections/{ck}")
async def api_connection_links(
    ck: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One connection's links: ``{ck, members, connections, welds, subjects, sim_runs, contacts}``
    (rows, flagged; ``contacts`` are its members' contacts with each other)."""
    if not _CK.match(ck):
        raise HTTPException(status_code=400, detail=f"{ck!r} is not a connection key")
    pool = optional_pool(request)
    store = link_store(ctx.storage, scope_obj, pool)
    page = await store.query(LinkQuery(cks=[ck], limit=1))
    rows = [d for g in page["groups"] for d in g["rows"]]
    if not rows:
        raise HTTPException(status_code=404, detail=f"no links of connection {ck!r} in this scope")
    by_kind: dict[str, list] = {k: [] for k in _KINDS}
    for d in rows:
        by_kind[d["kind"]].append(d)
    members = sorted({d["element_key"] for d in rows})
    contacts = await store.query(LinkQuery(element_keys=members, kinds=["contact"], limit=MAX_LIMIT))
    by_kind["contact"] = [
        d for g in contacts["groups"] for d in g["rows"] if (d.get("data") or {}).get("other_key") in members
    ]
    return JSONResponse(
        {
            "ck": ck,
            "members": members,
            "connections": by_kind["connection"],
            "welds": by_kind["weld"],
            "subjects": by_kind["subject"],
            "sim_runs": by_kind["sim_run"],
            "contacts": by_kind["contact"],
        }
    )


@router.get("/scopes/{scope}/elements/links/reindex")
async def api_element_links_reindex_state(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """``{indexed, running, report}``: whether the scope's links have been built from its documents."""
    return JSONResponse(await reindex_state(ctx.storage, scope_obj))


@router.post("/scopes/{scope}/elements/links/reindex")
async def api_element_links_reindex(
    request: Request,
    body: dict | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Build the scope's links again from its documents (``assoc_reindex``). ``{wait: true}`` answers
    with the report when done; otherwise it runs in the background (``GET`` says when it is)."""
    pool = optional_pool(request)
    await ctx.audit(request, user, scope_obj, "element_links_reindex", status="queued")
    if (body or {}).get("wait"):
        report = await reindex_scope(ctx.storage, scope_obj, pool=pool)
        return JSONResponse({"report": report.to_dict()})
    start_reindex(ctx.storage, scope_obj, pool=pool)
    return JSONResponse({"started": True})


async def _built_connections(store, q: LinkQuery) -> dict[str, dict]:
    """Every connection (by ck) the rows of ``q`` hold, with its newest built detail, its subject (the
    newest) and its runs."""
    rows = await store.rows(
        LinkQuery(under=q.under, element_keys=q.element_keys, cks=q.cks, kinds=["connection", "subject", "sim_run"])
    )
    out: dict[str, dict] = {}
    for r in rows:
        if not r.ck:
            continue
        c = out.setdefault(
            r.ck, {"ck": r.ck, "detail": None, "spec": None, "subject": None, "runs": {}, "built": False}
        )
        at = str(r.created_at or "")
        if r.kind == "connection":
            if r.data.get("status") == "built" and at >= str((c["detail"] or {}).get("at") or ""):
                c["built"] = True
                c["detail"] = {"detail_id": r.data.get("detail_id"), "at": at, "joint_id": r.data.get("joint_id")}
                c["spec"] = r.data.get("requested_spec") or r.data.get("spec")
        elif r.kind == "subject":
            if at >= str((c["subject"] or {}).get("at") or ""):
                c["subject"] = {"key": r.data.get("subject_key"), "kind": r.data.get("subject_kind"), "at": at}
        else:
            c["runs"][str(r.data.get("run_id"))] = r.data
    return out


@router.post("/scopes/{scope}/elements/subjects")
async def api_element_subjects(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Make the missing simulation subjects of the built connections under some nodes. Body
    ``{nodes? | group_id? | cks?, dry_run?}``. One ``clash_contacts`` ``subjects`` job per detail
    model, each rebuilding its run's members and joints (``ada.clash.subjects``).
    ``{jobs: [{job_id, detail_id, cks}], missing, have}``."""
    from .. import clash_details
    from .clash_check import _route_with_reader, _spec_capability
    from .clash_contacts import _submit

    under = await _under(ctx, scope_obj, body)
    q = _query_from({"cks": body.get("cks")}, under)
    if under is None and q.cks is None:
        raise HTTPException(status_code=400, detail="name 'nodes', 'group_id' or 'cks'")
    pool = optional_pool(request)
    conns = await _built_connections(link_store(ctx.storage, scope_obj, pool), q)
    missing = [c for c in conns.values() if c["built"] and c["subject"] is None and c["detail"]]
    by_detail: dict[str, list[dict]] = {}
    for c in missing:
        by_detail.setdefault(str(c["detail"]["detail_id"]), []).append(c)
    jobs = []
    if not body.get("dry_run") and by_detail:
        ctx.jobs.require("clash_detail")
        for detail_id, cs in sorted(by_detail.items()):
            paths = clash_details.detail_paths(detail_id)
            spec = cs[0]["spec"] or ""
            capability = await _route_with_reader(ctx, await _spec_capability(ctx, spec), None, needs=f"spec {spec!r}")
            submitted = await _submit(
                ctx,
                request,
                user,
                scope_obj,
                feature="clash_detail",
                source_key=paths.record,
                derived_key=f"{paths.prefix}subjects.json",
                options={"mode": "subjects", "detail_id": detail_id, "cks": sorted(c["ck"] for c in cs)},
                capability=capability,
            )
            jobs.append({"job_id": submitted.job_id, "detail_id": detail_id, "cks": sorted(c["ck"] for c in cs)})
    return JSONResponse(
        {
            "jobs": jobs,
            "missing": sorted(c["ck"] for c in missing),
            "have": sorted(ck for ck, c in conns.items() if c["subject"] is not None),
        }
    )


def _wanted(policy: str, runs: list[dict]) -> bool:
    """Whether a connection needs a run under ``policy``, given its runs on this provider and these
    parameters."""
    if policy == "all" or not runs:
        return True
    if policy == "missing":
        return False
    newest = max(runs, key=lambda r: str(r.get("run_id") or ""))
    return newest.get("status") in ("error", "cancelled")


@router.post("/scopes/{scope}/simulations/batch")
async def api_simulation_batch_start(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Simulate every connection under some nodes that needs it, as the caller, in one batch. Body
    ``{nodes? | group_id? | cks?, provider, parameters?, kind?, policy: missing | failed | all,
    dry_run?, batch?: {id, label?}, cursor?, limit? (at most 100)}``.

    For each connection (by ck, in ck order, a page at a time): its newest subject, and its runs on
    this provider with these parameters. ``missing`` starts one for a connection with none,
    ``failed`` also for one whose newest run failed or was cancelled, ``all`` for every one. A run is
    started through ``POST .../simulations``' own path, after an intent for (ck, provider, parameters,
    generation) is taken -- so two presses (two tabs) never start the same run twice. Every run of
    one press carries the same ``batch`` (``ada.simulation.batches``): pass the answer's ``batch``
    back with ``cursor`` for the next page.

    ``{batch, started: [{ck, run_id}], skipped: [{ck, reason}], failed: [{ck, error}], next}``;
    ``dry_run`` answers what would be started without starting anything."""
    from .simulations import start_run

    provider = str(body.get("provider") or "")
    if not provider:
        raise HTTPException(status_code=400, detail="'provider' (a simulation provider id) is required")
    policy = str(body.get("policy") or "missing")
    if policy not in POLICIES:
        raise HTTPException(status_code=400, detail=f"'policy' is one of {', '.join(POLICIES)}")
    from ada.simulation.schema import ParameterError, validate_parameters

    from ..simulation_runs import provider_by_id
    from .simulations import _providers

    described = provider_by_id(await _providers(ctx, request), provider)
    if described is None:
        raise HTTPException(status_code=404, detail=f"unknown simulation provider {provider!r}")
    try:
        # As the run's record will hold them (defaults filled in): what "the same parameters" compares.
        parameters = validate_parameters(described["parameters_schema"], body.get("parameters"))
    except ParameterError as exc:
        raise HTTPException(status_code=400, detail=f"invalid parameters: {exc}") from exc
    under = await _under(ctx, scope_obj, body)
    q = _query_from({"cks": body.get("cks")}, under)
    if under is None and q.cks is None:
        raise HTTPException(status_code=400, detail="name 'nodes', 'group_id' or 'cks'")
    limit = max(1, min(int(body.get("limit") or BATCH_PAGE), BATCH_PAGE))
    raw_batch = body.get("batch") if isinstance(body.get("batch"), dict) else {}
    batch = {
        "id": str(raw_batch.get("id") or f"elements-{uuid.uuid4().hex[:12]}"),
        "label": str(raw_batch.get("label") or body.get("label") or "Connections"),
    }
    if body.get("group_id"):
        batch["source"] = {"kind": "group", "key": str(body["group_id"])}

    pool = optional_pool(request)
    store = link_store(ctx.storage, scope_obj, pool)
    conns = await _built_connections(store, q)
    cks = sorted(ck for ck in conns if not body.get("cursor") or ck > str(body["cursor"]))
    page, rest = cks[:limit], cks[limit:]
    ph = params_hash(parameters)
    started: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    for ck in page:
        c = conns[ck]
        if c["subject"] is None or not c["subject"].get("key"):
            skipped.append({"ck": ck, "reason": "no subject (POST .../elements/subjects makes it)"})
            continue
        runs = [r for r in c["runs"].values() if r.get("provider") == provider and r.get("params_hash") == ph]
        if any(r.get("status") in ACTIVE_SIM_STATUSES for r in runs):
            skipped.append({"ck": ck, "reason": "a run is already queued or running"})
            continue
        if not _wanted(policy, runs):
            skipped.append({"ck": ck, "reason": f"has a run ({policy})"})
            continue
        if body.get("dry_run"):
            started.append({"ck": ck, "run_id": None, "subject": c["subject"]["key"]})
            continue
        intent = Intent(ck, provider, ph, generation=len(runs))
        if not await store.take_intent(intent):
            skipped.append({"ck": ck, "reason": "already being started"})
            continue
        try:
            record = await start_run(
                ctx,
                request,
                user,
                scope_obj,
                {
                    "provider": provider,
                    "parameters": parameters,
                    **({"kind": body["kind"]} if body.get("kind") else {}),
                    "subject": {"kind": c["subject"].get("kind") or "weld-assembly", "key": c["subject"]["key"]},
                    "batch": batch,
                },
            )
        except HTTPException as exc:
            await store.release_intent(intent)
            if exc.status_code in (404, 409) and not started and "provider" in str(exc.detail):
                raise
            failed.append({"ck": ck, "error": str(exc.detail)})
            continue
        except Exception as exc:  # noqa: BLE001 - one connection is not the batch
            await store.release_intent(intent)
            failed.append({"ck": ck, "error": f"{type(exc).__name__}: {exc}"})
            continue
        await store.set_intent_run(intent, record["run_id"])
        started.append({"ck": ck, "run_id": record["run_id"]})
    return JSONResponse(
        {
            "batch": batch,
            "started": started,
            "skipped": skipped,
            "failed": failed,
            "next": page[-1] if rest and page else None,
            "dry_run": bool(body.get("dry_run")),
        }
    )

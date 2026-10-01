"""``POST /api/scopes/{scope}/export-selection`` -- a selected element of a loaded model, and
everything under it, as a downloadable STEP or IFC file.

THE ROUTE NEVER OPENS THE MODEL. Like the clash check it prices a derived key from a cheap
content token (``head()``'s e_tag) and the selection's identity, answers a repeat from the store,
and otherwise enqueues a job that reads the model where the readers live (a worker, or this
process's local transport). Exports of a large selection are slow -- the whole source is read to
find one deck -- which is why this is a job and not a response body.

TWO WAYS TO NAME THE MODEL, exactly one per request, for the reasons ``routes/clash_check.py``
gives: a FILE core reads (``source_key``), or a PUBLISHED NODE whose provider reads its own
format into ``ada`` objects (``collection`` + ``subject``). What the user selected INSIDE that
model rides separately as ``element`` (the tree row's name; omitted for the model's root row)
and ``path`` (the row names from below the root down to it), because names repeat.

A NODE DELIVERED AS A MESH IS REFUSED HERE, not in the job: a mesh-only provider hands over
triangles and no objects, so there is nothing to write a STEP from and an IFC could only be a
tessellated shell of what the user thinks they are downloading. Saying so before anything is
queued is the honest answer.
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from ..selection_export import (
    SELECTION_EXPORT_FORMATS,
    SELECTION_SOURCE_EXTS,
    export_filename,
    selection_derived_key,
)
from .clash_check import _asset_source_key, _source_content_token
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()


def _selection_from_body(body: dict) -> tuple[str | None, list[str]]:
    element = body.get("element")
    if element is not None and not isinstance(element, str):
        raise HTTPException(status_code=400, detail="'element' must be a string or null")
    raw_path = body.get("path") or []
    if not isinstance(raw_path, list) or not all(isinstance(p, str) for p in raw_path):
        raise HTTPException(status_code=400, detail="'path' must be a list of names")
    element = element or None
    if element is None and raw_path:
        raise HTTPException(status_code=400, detail="'path' names a selection, so 'element' is required with it")
    if element is not None and raw_path and raw_path[-1] != element:
        raise HTTPException(status_code=400, detail="'path' must end at 'element' -- it is the way down to it")
    return element, list(raw_path)


@router.post("/scopes/{scope}/export-selection")
async def api_export_selection(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Start (or answer from the store) an export. Returns ``{job_id, derived_key, cached, filename}``.

    Body: ``{format: "step"|"ifc", element?, path?, label?, force?}`` plus ONE of ``{source_key}``
    or ``{collection, subject, revision?, node?, provider?}``. ``node`` is the published node the
    viewer loaded (a covered row's own id); ``element`` narrows within it by name. ``label`` only
    names the download when no element is selected -- the model as the viewer calls it.
    """
    fmt = str(body.get("format") or "").lower()
    if fmt not in SELECTION_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"'format' must be one of {sorted(SELECTION_EXPORT_FORMATS)}")
    source_key = str(body.get("source_key") or "")
    collection = str(body.get("collection") or "")
    subject = str(body.get("subject") or "")
    if source_key and (collection or subject):
        raise HTTPException(
            status_code=400,
            detail=(
                "name the model ONE way: 'source_key' for a file core reads, or 'collection' + "
                "'subject' for a published node whose provider reads it"
            ),
        )
    if not source_key and not (collection and subject):
        raise HTTPException(status_code=400, detail="'source_key', or 'collection' and 'subject' together, is required")
    element, path = _selection_from_body(body)

    ctx.jobs.require("export_selection")

    if source_key:
        ext = pathlib.PurePosixPath(source_key).suffix.lower()
        if ext not in SELECTION_SOURCE_EXTS:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"a {ext or 'extensionless'} source cannot be exported by selection: there is no "
                    f"model behind its tree to re-read (supported: {sorted(SELECTION_SOURCE_EXTS)})"
                ),
            )
        if not await ctx.storage.exists(scope_obj, source_key):
            raise HTTPException(status_code=404, detail=f"no source at {source_key!r}")
        target = {"source_key": source_key}
        token_key = source_key
        target_format = "export_selection"
        options: dict = {"source_key": source_key}
    else:
        token_key, manifest = await _asset_source_key(ctx, scope_obj, collection, subject, body.get("revision") or None)
        asked_provider = str(body.get("provider") or "")
        if asked_provider and asked_provider != manifest.provider:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"subject {subject!r} at {manifest.revision} was produced by provider "
                    f"{manifest.provider!r}, not {asked_provider!r}"
                ),
            )
        if manifest.build is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"subject {subject!r} claims delivery {manifest.delivery!r} from provider "
                    f"{manifest.provider!r} and no build: there are no objects behind it to write a STEP "
                    f"or IFC from"
                ),
            )
        node = str(body.get("node") or subject)
        target = {"collection": collection, "subject": subject, "revision": manifest.revision, "node": node}
        # The kind as a LITERAL, as every route names its own: importing it from `..formats`
        # would pull the worker's handler chain into the slim API process.
        target_format = "export_selection_asset"
        options = dict(target)

    filename = export_filename(element or str(body.get("label") or "") or None, fmt)
    content_token = await _source_content_token(ctx, scope_obj, token_key)
    derived_key = selection_derived_key(content_token, target, fmt=fmt, element=element, path=path, filename=filename)

    if not bool(body.get("force")) and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True, "filename": filename})

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=token_key,
            target_format=target_format,
            scope=scope_obj,
            feature="export_selection",
            derived_prefix=derived_key.rsplit("/", 1)[0],
            derived_key=derived_key,
            conversion_options={**options, "format": fmt, "element": element, "path": path},
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "export_selection",
            key=derived_key,
            target_format=target_format,
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "cached": False, "filename": filename})

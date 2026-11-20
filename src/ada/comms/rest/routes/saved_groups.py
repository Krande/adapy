"""``GET /api/scopes/{scope}/saved-groups``: the scope's saved groups, read-only and in brief.

A saved group (the Scene panel's Groups) is a named list of members across models, kept in one
document per scope, ``_groups/groups.json`` (``src/frontend/src/utils/groups/savedGroups.ts`` owns
its format and writes it). The viewer reads that document through the file routes; this lists what
is in it for an agent or the CLI (``ada viewer groups``) -- id, name, how many members, and which
models -- so a group can be loaded by name (the ``load_saved_group`` viewer command). Not gated by
``ADA_VIEWER_CONTROL``: it reads, as the clash routes do.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()

GROUPS_BLOB_KEY = "_groups/groups.json"
GROUPS_SCHEMAS = ("ada.viewer/groups@1", "ada.viewer/groups@2")


def _source_of(member: dict) -> str | None:
    target = member.get("target") if isinstance(member.get("target"), dict) else {}
    if target.get("kind") == "file":
        return str(target.get("source_key") or "") or None
    if target.get("kind") == "node":
        return f"{target.get('collection')}/{target.get('subject')}"
    return None


def group_summaries(doc: object) -> list[dict]:
    """``[{id, name, members, sources, created_at, updated_at}]`` of a groups document; ValueError
    for one this does not read."""
    if doc is None:
        return []
    if not isinstance(doc, dict) or doc.get("schema") not in GROUPS_SCHEMAS:
        raise ValueError("not a saved-groups document")
    out = []
    for g in doc.get("groups") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        members = [m for m in g.get("members") or [] if isinstance(m, dict)]
        out.append(
            {
                "id": str(g["id"]),
                "name": str(g.get("name") or ""),
                "members": len(members),
                "sources": sorted({s for s in (_source_of(m) for m in members) if s}),
                "created_at": g.get("created_at"),
                "updated_at": g.get("updated_at"),
            }
        )
    return out


@router.get("/scopes/{scope}/saved-groups")
async def api_saved_groups(
    name: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The scope's saved groups, ``{groups: [{id, name, members, sources, ...}]}``; ``name`` keeps
    those whose name contains it (case-insensitive)."""
    try:
        raw = await ctx.storage.get_bytes(scope_obj, GROUPS_BLOB_KEY)
    except (FileNotFoundError, KeyError):
        raw = None
    try:
        groups = group_summaries(json.loads(raw) if raw else None)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"unreadable saved groups: {exc}") from exc
    if name:
        groups = [g for g in groups if name.lower() in g["name"].lower()]
    return JSONResponse({"groups": groups})

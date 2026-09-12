"""Admin project-management routes: ``GET``/``POST /api/admin/projects``,
``DELETE /api/admin/projects/{id}``, the member CRUD family, and the
per-project CI-bot provision/revoke pair.

User-facing project routes (``GET /api/me``, ``GET /api/projects``) live in
:mod:`~.projects` instead, mounted under the plain ``api`` router.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from .deps import require_pool

router = APIRouter()


def _validate_uuid(value: str, what: str = "id") -> str:
    import uuid as _uuid

    try:
        return str(_uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {what}") from exc


@router.get("/projects")
async def admin_projects_list(request: Request) -> JSONResponse:
    pool = require_pool(request)
    return JSONResponse({"projects": await db_module.list_all_projects(pool)})


@router.post("/projects")
async def admin_projects_create(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    pool = require_pool(request)
    body = await request.json()
    slug = (body.get("slug") or "").strip()
    name = (body.get("name") or "").strip()
    if not slug or not name:
        raise HTTPException(status_code=400, detail="slug and name required")
    # Slug shape: lowercase, alnum + hyphens. Keeps URLs / on-disk
    # prefixes predictable; doesn't otherwise constrain the name.
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
        raise HTTPException(
            status_code=400,
            detail="slug must be lowercase alnum/hyphens (max 63)",
        )
    try:
        project = await db_module.create_project(pool, slug, name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Auto-add the creator as owner so the new project shows up in
    # their /api/me.scopes immediately. Without this the project is
    # orphaned until an admin manually adds someone — easy to forget,
    # and it leaves the creator unable to push artefacts to the
    # project they just made.
    await db_module.add_project_member(pool, project["id"], user.sub, role="owner")
    project["member_count"] = 1
    return JSONResponse(project, status_code=201)


@router.delete("/projects/{project_id}")
async def admin_projects_archive(
    project_id: str,
    request: Request,
) -> Response:
    pool = require_pool(request)
    pid = _validate_uuid(project_id, "project_id")
    ok = await db_module.archive_project(pool, pid)
    if not ok:
        raise HTTPException(status_code=404, detail="project not found")
    return Response(status_code=204)


@router.get("/projects/{project_id}/members")
async def admin_project_members_list(
    project_id: str,
    request: Request,
) -> JSONResponse:
    pool = require_pool(request)
    pid = _validate_uuid(project_id, "project_id")
    if not await db_module.project_exists(pool, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return JSONResponse({"members": await db_module.list_project_members(pool, pid)})


@router.post("/projects/{project_id}/members")
async def admin_project_members_add(
    project_id: str,
    request: Request,
) -> JSONResponse:
    pool = require_pool(request)
    pid = _validate_uuid(project_id, "project_id")
    body = await request.json()
    sub = (body.get("user_sub") or "").strip()
    role = (body.get("role") or "member").strip() or "member"
    if not sub:
        raise HTTPException(status_code=400, detail="user_sub required")
    if not await db_module.project_exists(pool, pid):
        raise HTTPException(status_code=404, detail="project not found")
    added = await db_module.add_project_member(pool, pid, sub, role)
    return JSONResponse(
        {"user_sub": sub, "role": role, "added": added},
        status_code=201 if added else 200,
    )


@router.delete("/projects/{project_id}/members/{user_sub}")
async def admin_project_members_remove(
    project_id: str,
    user_sub: str,
    request: Request,
) -> Response:
    pool = require_pool(request)
    pid = _validate_uuid(project_id, "project_id")
    ok = await db_module.remove_project_member(pool, pid, user_sub)
    if not ok:
        raise HTTPException(status_code=404, detail="not a member")
    return Response(status_code=204)


# A bot name is one path-safe token. The colon is excluded because it is
# the SEPARATOR: a name containing one could spell another bot's subject
# (`ci:<slug>:a:b`) and quietly take over its tokens and its revocation.
_CI_BOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _ci_bot_identity(slug: str, name: str | None) -> tuple[str, str, str]:
    """``(sub, email, display)`` for a project's CI bot.

    Unnamed is ``ci:<slug>`` -- unchanged, so every token already issued
    under that subject keeps working and keeps being rotated by the same
    call as before. A name appends one more segment.
    """
    if not name:
        return f"ci:{slug}", f"ci+{slug}@bot.local", f"CI Bot: {slug}"
    return f"ci:{slug}:{name}", f"ci+{slug}.{name}@bot.local", f"CI Bot: {slug} / {name}"


async def _ci_bot_request(request: Request, project_id: str) -> tuple[object, str, str, str, str]:
    """Shared prologue: validate, resolve the project, build the identity."""
    pool = require_pool(request)
    pid = _validate_uuid(project_id, "project_id")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    name = (str(body.get("name") or "")).strip().lower() or None
    if name is not None and not _CI_BOT_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "name must be 1-64 characters of a-z, 0-9, dot, dash or underscore, "
                "starting alphanumeric. A colon is not allowed: it separates the "
                "project from the bot, so a name containing one could spell another "
                "bot's identity."
            ),
        )
    row = await pool.fetchrow(
        "SELECT slug FROM projects WHERE id = $1 AND archived_at IS NULL",
        pid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    sub, email, display = _ci_bot_identity(row["slug"], name)
    return pool, pid, sub, email, display


@router.post("/projects/{project_id}/ci-bot")
async def admin_provision_ci_bot(
    project_id: str,
    request: Request,
) -> JSONResponse:
    """Provision (or rotate the token of) a CI bot user for a project.

    One-shot: creates the bot user row if missing, ensures it's a
    project member, revokes any prior tokens, and mints a fresh
    30-day CLI bearer. The token is returned exactly once, and
    re-calling ROTATES -- prior tokens for that bot stop validating
    immediately via the per-user revoke cutoff. Always admin-gated.

    MORE THAN ONE BOT PER PROJECT. Body: ``{"name": "..."}``, optional.
    Without it the subject is ``ci:<slug>``, exactly as before. With it,
    ``ci:<slug>:<name>``.

    The reason is that one identity per project forces every consumer to
    share one credential, and the revoke cutoff is stored per SUBJECT --
    so rotating for one consumer silently breaks the others, and every
    audit row reads ``ci:<slug>`` no matter which of them acted. Give the
    build uploader and a data-recording worker a name each and both
    problems go away: separate rotation, separate revocation, and an audit
    trail that says which one did the thing.

    Nothing about the token or the revocation model changes to allow it. A
    distinct subject simply HAS its own cutoff, which is why this is a new
    segment on the subject rather than a token id and a revocation list.
    """
    pool, pid, bot_sub, bot_email, bot_display = await _ci_bot_request(request, project_id)

    await db_module.upsert_user(pool, bot_sub, bot_email, bot_display)
    await db_module.add_project_member(pool, pid, bot_sub, role="ci")

    bot_user = User(
        sub=bot_sub,
        email=bot_email,
        display_name=bot_display,
        groups=frozenset(),
        is_admin=False,
    )
    # Rotate: invalidate any tokens minted before now for this bot,
    # then mint a fresh one. The cutoff is iat-based so the token
    # we're about to mint (with a fresh iat) survives.
    await auth_module.revoke_cli_tokens(pool, bot_user)
    config = request.app.state.auth_config
    token, exp = auth_module.mint_cli_token(bot_user, config)
    return JSONResponse(
        {
            "user_sub": bot_sub,
            "token": token,
            "expires_at": exp,
        },
        status_code=201,
    )


@router.post("/projects/{project_id}/ci-bot/revoke")
async def admin_revoke_ci_bot(
    project_id: str,
    request: Request,
) -> JSONResponse:
    """Kill a CI bot's tokens WITHOUT minting a replacement.

    Until now the only way to invalidate a bot's token was to mint another
    one, which is the wrong move for a leaked credential or a decommissioned
    consumer: it hands you a fresh secret you did not want and leaves the
    bot able to act. Revoking on its own is the thing an operator reaches
    for when something has gone wrong, and it did not exist.

    The bot stays a project member. Removing it is a separate, deliberate
    act (``DELETE /projects/{id}/members/{sub}``) -- and keeping the
    membership means its audit history still resolves to a named principal
    rather than a bare subject nobody can identify later.
    """
    pool, _pid, bot_sub, bot_email, bot_display = await _ci_bot_request(request, project_id)
    bot_user = User(
        sub=bot_sub,
        email=bot_email,
        display_name=bot_display,
        groups=frozenset(),
        is_admin=False,
    )
    revoked_at = await auth_module.revoke_cli_tokens(pool, bot_user)
    logger.info("admin: revoked CI bot tokens for %s", bot_sub)
    return JSONResponse({"user_sub": bot_sub, "revoked_at": revoked_at})

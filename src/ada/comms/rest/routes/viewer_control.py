"""``/api/viewer/...``: drive the caller's own open viewer, and read back what it shows.

The vocabulary, the transport and who may drive whose viewer are in :mod:`..viewer_control`. Every
route is the CALLER's: sessions are looked up by (user, session id), so another user's session id
answers 404 here exactly as an unknown one does.

Agent side:

* ``GET  /viewer/sessions``                 -- the caller's live viewer tabs, each with its last state
* ``GET  /viewer/sessions/{session_id}``    -- one of them
* ``POST /viewer/commands``                 -- ``{command | commands, session_id?, wait_s?}``: send a
  command, or a batch each tab runs in order, to one tab or all of them, and wait (default 10 s)
  for their answers
* ``GET  /viewer/commands/{command_id}``    -- a command's answers so far
* ``GET  /viewer/commands/schema``          -- the command vocabulary as JSON schema

Browser side (``src/frontend/src/services/viewerControl``):

* ``POST   /viewer/sessions``                                   -- register this tab
* ``GET    /viewer/sessions/{session_id}/commands?wait_s=``     -- long-poll for commands
* ``PUT    /viewer/sessions/{session_id}/state``                -- report what it shows
* ``POST   /viewer/sessions/{session_id}/commands/{command_id}`` -- answer a command
* ``DELETE /viewer/sessions/{session_id}``                      -- the tab is closing

OFF BY DEFAULT. Every route answers 404 unless the server is started with ``ADA_VIEWER_CONTROL=true``
(``Settings.viewer_control``); ``/config.js`` then tells the page (``window.ADA_VIEWER_CONTROL``), and
a tab registers only once its user has also turned it on in the viewer's settings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .. import auth as auth_module
from ..auth import User
from ..viewer_control import (
    POLL_MAX_S,
    ViewerHub,
    command_schema,
    parse_request,
    state_size_ok,
)
from .deps import RestContext, rest_context

#: The setting's environment variable, named in the answer of a server that has it off.
ENV_VAR = "ADA_VIEWER_CONTROL"


def require_viewer_control(ctx: RestContext = Depends(rest_context)) -> None:
    """404 on a server that has viewer control off (the default)."""
    if not ctx.settings.viewer_control:
        raise HTTPException(
            status_code=404, detail=f"viewer control is disabled on this server (set {ENV_VAR}=true to enable it)"
        )


router = APIRouter(dependencies=[Depends(require_viewer_control)])

#: How long ``POST /viewer/commands`` waits for answers when the body does not say.
DEFAULT_WAIT_S = 10.0


def viewer_hub(request: Request) -> ViewerHub:
    """The app's hub, made on first use."""
    hub = getattr(request.app.state, "viewer_hub", None)
    if hub is None:
        hub = ViewerHub()
        request.app.state.viewer_hub = hub
    return hub


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="the body must be JSON") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


def _state_from(body: dict, *, required: bool) -> dict | None:
    state = body.get("state")
    if state is None and not required:
        return None
    if not isinstance(state, dict):
        raise HTTPException(status_code=400, detail="'state' must be an object")
    if not state_size_ok(state):
        raise HTTPException(status_code=413, detail="the state report is too large")
    return state


def _no_session(session_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no viewer session {session_id!r} of yours")


# ── agent side ───────────────────────────────────────────────────────────────────────────────────


@router.get("/viewer/commands/schema")
async def api_viewer_command_schema() -> JSONResponse:
    """The ``POST /viewer/commands`` body as JSON schema: ``command`` (one), or ``commands`` (a batch),
    each a union with one branch per ``action``."""
    return JSONResponse(command_schema())


@router.get("/viewer/sessions")
async def api_viewer_sessions(
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """The caller's live viewer sessions, the most recently heard from first, each with the state it
    last reported (scope, loaded models, selection, camera, open clash run, detail models)."""
    return JSONResponse({"sessions": hub.sessions(user.sub)})


@router.get("/viewer/sessions/{session_id}")
async def api_viewer_session(
    session_id: str,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    found = hub.session(user.sub, session_id)
    if found is None:
        raise _no_session(session_id)
    return JSONResponse(found)


@router.post("/viewer/commands")
async def api_viewer_command(
    request: Request,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Send ``command`` -- or ``commands``, a batch -- to the caller's viewer: to ``session_id``, else
    to every live session of theirs. Each tab runs a batch in order and stops at the first command
    that fails. Waits up to ``wait_s`` (default 10, at most 60; 0 does not wait) for the answers.

    Returns ``{command_id, command | commands, issued_at, results: [{session_id, status, error,
    result, state, steps?}]}``, ``status`` per session ``done``, ``error``, ``pending`` (no answer
    yet: ask ``GET /viewer/commands/{command_id}`` later) or ``gone``. A batch's answer has
    ``steps``: ``[{index, action, status: done | error | skipped, error, result}]``, one per command.
    400 for a command outside the vocabulary, 404 when the caller has no such (or no) viewer open."""
    body = await _body(request)
    try:
        req = parse_request(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors(include_url=False)],
        ) from exc
    issued = hub.issue(user.sub, req.payload(), req.session_id)
    if issued is None:
        if req.session_id is not None:
            raise _no_session(req.session_id)
        raise HTTPException(status_code=404, detail="you have no viewer open (no live viewer session)")
    wait_s = DEFAULT_WAIT_S if req.wait_s is None else req.wait_s
    out = await hub.wait(user.sub, issued["command_id"], wait_s)
    return JSONResponse(out)


@router.get("/viewer/commands/{command_id}")
async def api_viewer_command_result(
    command_id: str,
    wait_s: float = 0.0,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    out = await hub.wait(user.sub, command_id, max(0.0, wait_s))
    if out is None:
        raise HTTPException(status_code=404, detail=f"no command {command_id!r} of yours")
    return JSONResponse(out)


# ── browser side ─────────────────────────────────────────────────────────────────────────────────


@router.post("/viewer/sessions")
async def api_viewer_register(
    request: Request,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Register a viewer tab. Body ``{client?: {...}, state?: {...}}``. Returns the session, with
    ``poll_s`` -- how long a poll may be held."""
    body = await _body(request)
    client = body.get("client") or {}
    if not isinstance(client, dict) or not state_size_ok(client):
        raise HTTPException(status_code=400, detail="'client' must be a small object")
    session = hub.register(user.sub, client=client, state=_state_from(body, required=False))
    return JSONResponse({**session, "poll_s": POLL_MAX_S})


@router.get("/viewer/sessions/{session_id}/commands")
async def api_viewer_poll(
    session_id: str,
    wait_s: float = POLL_MAX_S,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """The tab's commands, held up to ``wait_s`` (at most ``poll_s``) until there is one. 404 when
    the session is gone (expired, or replaced): the tab registers again."""
    commands = await hub.poll(user.sub, session_id, max(0.0, wait_s))
    if commands is None:
        raise _no_session(session_id)
    return JSONResponse({"commands": commands})


@router.put("/viewer/sessions/{session_id}/state")
async def api_viewer_state(
    session_id: str,
    request: Request,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    state = _state_from(await _body(request), required=True)
    if not hub.report_state(user.sub, session_id, state):
        raise _no_session(session_id)
    return JSONResponse({"ok": True})


@router.post("/viewer/sessions/{session_id}/commands/{command_id}")
async def api_viewer_answer(
    session_id: str,
    command_id: str,
    request: Request,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """A tab's answer: ``{ok, error?, result?, state?}``."""
    body = await _body(request)
    if "state" in body and body["state"] is not None:
        _state_from(body, required=True)
    if body.get("result") is not None and not state_size_ok(body.get("result")):
        raise HTTPException(status_code=413, detail="the result is too large")
    if not hub.complete(user.sub, session_id, command_id, body):
        raise HTTPException(status_code=404, detail=f"no command {command_id!r} sent to session {session_id!r}")
    return JSONResponse({"ok": True})


@router.delete("/viewer/sessions/{session_id}")
async def api_viewer_close(
    session_id: str,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    if not hub.close(user.sub, session_id):
        raise _no_session(session_id)
    return JSONResponse({"ok": True})

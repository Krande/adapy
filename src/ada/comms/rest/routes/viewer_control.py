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

Viewer side (``src/frontend/src/services/viewerControl``):

* ``POST /viewer/ws-ticket``                 -- a single-use ticket for the socket
* ``GET  /api/viewer/ws?ticket=``            -- the tab's websocket: one session while it is open;
  commands come down as ``VIEWER_COMMAND``, answers and state go up (``ada.comms.viewer_wire``)

OFF BY DEFAULT. Every route answers 404 unless the server is started with ``ADA_VIEWER_CONTROL=true``
(``Settings.viewer_control``); ``/config.js`` then tells the page (``window.ADA_VIEWER_CONTROL``), and
a tab opens its socket only once its user has also turned it on in the viewer's settings.
"""

from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ada.config import logger

from .. import auth as auth_module
from ..auth import User
from ..viewer_control import (
    TICKET_TTL_S,
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
    return _app_hub(request.app)


def _app_hub(app) -> ViewerHub:
    hub = getattr(app.state, "viewer_hub", None)
    if hub is None:
        hub = ViewerHub()
        app.state.viewer_hub = hub
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


# ── the websocket leg ────────────────────────────────────────────────────────────────────────────


@router.post("/viewer/ws-ticket")
async def api_viewer_ws_ticket(
    request: Request,
    hub: ViewerHub = Depends(viewer_hub),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """A single-use ticket for ``/api/viewer/ws`` (a browser's websocket cannot send the bearer header):
    ``{ticket, expires_s, path}``. Body ``{client?: {...}}``: how the tab describes itself."""
    body = await _body(request)
    client = body.get("client") or {}
    if not isinstance(client, dict) or not state_size_ok(client):
        raise HTTPException(status_code=400, detail="'client' must be a small object")
    return JSONResponse(
        {"ticket": hub.mint_ticket(user.sub, client), "expires_s": TICKET_TTL_S, "path": "/api/viewer/ws"}
    )


#: Outside the authenticated ``/api`` router: a websocket handshake carries no bearer header, so the
#: ticket is the credential. Included on the app itself (``create_app``).
ws_router = APIRouter()

#: Close codes: the server has viewer control off; the ticket was not (or no longer) valid.
WS_CLOSE_DISABLED = 4404
WS_CLOSE_TICKET = 4403


@ws_router.websocket("/api/viewer/ws")
async def api_viewer_ws(websocket: WebSocket, ticket: str = "") -> None:
    """A viewer tab's session: commands pushed down as ``VIEWER_COMMAND``, answers and state sent up as
    ``VIEWER_ANSWER`` / ``VIEWER_STATE`` (``ada.comms.viewer_wire``). The session is the ticket's
    user's, and lives as long as the socket."""
    from ada.comms.fb_wrap_model_gen import CommandTypeDC
    from ada.comms.viewer_wire import payload_of, read

    if not websocket.app.state.rest.settings.viewer_control:
        await websocket.close(code=WS_CLOSE_DISABLED)
        return
    hub = _app_hub(websocket.app)
    redeemed = hub.redeem_ticket(ticket)
    if redeemed is None:
        await websocket.close(code=WS_CLOSE_TICKET)
        return
    owner, client = redeemed
    await websocket.accept()
    lock = asyncio.Lock()

    async def send(data: bytes) -> None:
        async with lock:
            await websocket.send_bytes(data)

    sid = hub.register(owner, client=client, send=send)["session_id"]
    try:
        while True:
            data = await websocket.receive_bytes()
            try:
                msg = read(data)
            except Exception:  # noqa: BLE001 - not a message: ignored, the socket stays
                logger.debug("viewer ws: an unreadable message on session %s", sid)
                continue
            payload = payload_of(msg)
            if msg.command_type == CommandTypeDC.VIEWER_ANSWER and isinstance(payload, dict) and msg.request_id:
                if state_size_ok(payload):
                    hub.complete(owner, sid, msg.request_id, payload)
            elif msg.command_type == CommandTypeDC.VIEWER_STATE and isinstance(payload, dict):
                if state_size_ok(payload):
                    hub.report_state(owner, sid, payload)
    except WebSocketDisconnect:
        pass
    finally:
        hub.close(owner, sid)

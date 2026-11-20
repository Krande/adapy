"""Driving a user's open viewer from the REST API: the command vocabulary and the session hub.

An agent (a script, later an MCP server wrapping these routes 1:1) asks the viewer a user has open
to show something -- focus a clash joint, select objects by name, move the camera, load a detail
model -- or to say what it shows. ``routes/viewer_control.py`` serves it; this module holds what the
routes share:

THE VOCABULARY. One typed command per action (:data:`ViewerCommand`, a union discriminated by
``action``), validated here before anything reaches a browser, and published as JSON schema
(``GET /api/viewer/commands/schema``) so a tool layer can be generated from it. The browser runs
them (``src/frontend/src/services/viewerControl``).

THE TRANSPORT. Agents call the REST routes; a viewer tab holds a websocket (``/api/viewer/ws``,
opened with a single-use ticket from ``POST /api/viewer/ws-ticket``: a browser's websocket cannot
send the bearer header) and the hub pushes each command down it as a FlatBuffer ``VIEWER_COMMAND``
(``ada.comms.viewer_wire``). The tab answers ``VIEWER_ANSWER`` and reports ``VIEWER_STATE``. A session
is one open socket.

WHOSE VIEWER. A session belongs to the user whose ticket opened it; every lookup is by (user, session),
so a session id alone neither reads nor drives another user's viewer. A command goes to one named
session, or to all of the caller's live sessions (each tab answers for itself).

ONE PROCESS. Sessions and pending commands live in this process's memory. That is the API's
deployment today (one replica); a scaled-out API would need the hub on NATS (as the job queue is).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

__all__ = [
    "COMMAND_ADAPTER",
    "MAX_SESSIONS_PER_USER",
    "MAX_STATE_BYTES",
    "TICKET_TTL_S",
    "ViewerCommand",
    "ViewerHub",
    "CommandRequest",
    "MAX_BATCH",
    "command_schema",
    "parse_command",
    "parse_request",
    "state_size_ok",
]

#: The longest an agent's command waits for the viewers to answer.
COMMAND_WAIT_MAX_S = 60.0
#: Sessions kept per user; the longest-silent one is dropped for a new one past this.
MAX_SESSIONS_PER_USER = 16
#: A state report larger than this is refused (it is a summary, not a model).
MAX_STATE_BYTES = 256 * 1024
#: How long a websocket ticket (``POST /api/viewer/ws-ticket``) may wait to be used.
TICKET_TTL_S = 60.0
#: Commands' outcomes kept per user, for ``GET /viewer/commands/{id}`` after the wait ran out.
RESULTS_KEPT = 64

CAMERA_VIEWS = ("top", "bottom", "front", "back", "left", "right", "isometric")

Vec3 = Annotated[list[float], Field(min_length=3, max_length=3, description="x, y, z in model units")]
_Name = Annotated[str, Field(min_length=1, max_length=512)]


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FocusJoint(_Command):
    """Make a clash joint the viewer's cursor: open its group, select its members in the loaded
    model(s), reveal the joints review, and frame them. The joint is looked up in the clash result
    the viewer has open; ``run_key`` opens that convex-contact run first when it is not the one."""

    action: Literal["focus_joint"]
    joint_id: Annotated[str, Field(min_length=1, max_length=128, examples=["0123456789ab"])]
    run_key: str | None = Field(None, description="A convex-contact run key to open first (`GET .../latest`).")
    scope: str | None = Field(None, description="The scope `run_key` is in; default the viewer's own.")
    frame: bool = Field(True, description="Fit the camera to the joint's members.")
    reveal_panel: bool = Field(True, description="Bring up the Connections > Review panel at the joint.")


class Select(_Command):
    """Select objects by name in a loaded model and (by default) frame them."""

    action: Literal["select"]
    names: Annotated[list[_Name], Field(min_length=1, max_length=1000, examples=[["column_1", "brace_1"]])]
    file: str | None = Field(None, description="The loaded model (source name); default the active one.")
    frame: bool = True


class ClearSelection(_Command):
    """Clear the selection."""

    action: Literal["clear_selection"]


class Box(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: Vec3
    max: Vec3


class Camera(_Command):
    """Move the camera. Exactly one of: ``fit`` (``selection`` or ``all``), ``view`` (a named
    view), ``eye`` with ``target`` (look from a point at a point; the world's up axis stays up), or
    ``box`` (frame a world box, keeping the direction the camera looks in)."""

    action: Literal["camera"]
    fit: Literal["selection", "all"] | None = None
    view: Literal[CAMERA_VIEWS] | None = None  # type: ignore[valid-type]
    eye: Vec3 | None = None
    target: Vec3 | None = None
    box: Box | None = None

    @model_validator(mode="after")
    def _one_move(self) -> "Camera":
        if (self.eye is None) != (self.target is None):
            raise ValueError("'eye' and 'target' go together")
        given = [self.fit is not None, self.view is not None, self.eye is not None, self.box is not None]
        if sum(given) != 1:
            raise ValueError("give exactly one of 'fit', 'view', 'eye'+'target' or 'box'")
        return self


class OpenDetailModel(_Command):
    """Load a kept detail model (``GET /scopes/{scope}/clash-detail/models``) into the scene, as its
    Load button does. With ``joint_id``, then select that joint's connection in it and frame it; a
    joint the model does not hold (declined, failed) is focused in the clash run it was detailed
    from instead, so its members are still shown. The model must be built (``status: done``)."""

    action: Literal["open_detail_model"]
    detail_id: Annotated[str, Field(min_length=1, max_length=64)]
    scope: str | None = Field(None, description="Default the viewer's own scope.")
    joint_id: str | None = None
    frame: bool = True
    with_source: bool = Field(
        False,
        description="Also load the saved group the model's clash run was made on, so its welds sit in their "
        "structure (a run on a file or a node has no group: nothing more is loaded).",
    )


class LoadSavedGroup(_Command):
    """Load a saved group (the Scene panel's Groups, ``GET /scopes/{scope}/saved-groups``) into the
    scene, as its "Load into scene" does: each member's model, only the members showing. By
    ``group_id``, or by ``name`` (case-insensitive; an unknown or ambiguous name is an error naming
    the candidates). The load selects the members; ``select`` keeps that selection, ``frame`` fits
    the camera to them."""

    action: Literal["load_saved_group"]
    group_id: str | None = None
    name: str | None = None
    scope: str | None = Field(None, description="Default the viewer's own scope.")
    select: bool = False
    frame: bool = False

    @model_validator(mode="after")
    def _one_key(self) -> "LoadSavedGroup":
        if (self.group_id is None) == (self.name is None):
            raise ValueError("give exactly one of 'group_id' or 'name'")
        return self


class FindObjects(_Command):
    """Look objects up by name in the loaded models, without changing anything: per name, which
    model has it, how many objects draw it, and where (centre; the box of what draws it)."""

    action: Literal["find_objects"]
    names: Annotated[list[_Name], Field(min_length=1, max_length=200)]
    file: str | None = Field(None, description="Look in this loaded model only.")


class GetState(_Command):
    """Answer with what the viewer shows now (the same state every command's answer carries)."""

    action: Literal["get_state"]


ViewerCommand = Annotated[
    Union[FocusJoint, Select, ClearSelection, Camera, OpenDetailModel, LoadSavedGroup, FindObjects, GetState],
    Field(discriminator="action"),
]
COMMAND_ADAPTER: TypeAdapter = TypeAdapter(ViewerCommand)

#: The most commands one batch may hold.
MAX_BATCH = 20


class CommandRequest(BaseModel):
    """``POST /api/viewer/commands``: one ``command``, or a batch of ``commands`` that each tab runs
    in order, stopping at the first that fails (the rest are reported ``skipped``)."""

    model_config = ConfigDict(extra="forbid")

    command: ViewerCommand | None = None
    commands: Annotated[list[ViewerCommand], Field(min_length=1, max_length=MAX_BATCH)] | None = None
    session_id: str | None = Field(None, description="One of the caller's sessions; default all of them.")
    wait_s: float | None = Field(
        None, ge=0, description="How long to wait for the answers (default 10, at most 60; 0 does not wait)."
    )

    @model_validator(mode="after")
    def _one_form(self) -> "CommandRequest":
        if (self.command is None) == (self.commands is None):
            raise ValueError("give either 'command' or 'commands' (a batch)")
        return self

    def payload(self) -> dict:
        """What the tab receives: ``{"command"}`` or ``{"commands"}``, defaults filled in."""
        if self.command is not None:
            return {"command": self.command.model_dump(mode="json")}
        return {"commands": [c.model_dump(mode="json") for c in self.commands or ()]}


def command_schema() -> dict:
    """The request body as JSON schema; each action's command is one branch of its
    ``command`` / ``commands`` union (in ``$defs``)."""
    return CommandRequest.model_json_schema()


def parse_command(raw: Any) -> dict:
    """``raw`` validated into its command, as the plain JSON the browser receives (defaults filled
    in). Raises pydantic's ``ValidationError``."""
    return COMMAND_ADAPTER.validate_python(raw).model_dump(mode="json")


def parse_request(raw: Any) -> CommandRequest:
    """A ``POST /api/viewer/commands`` body. Raises pydantic's ``ValidationError``."""
    return CommandRequest.model_validate(raw)


# ── the hub ──────────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Session:
    session_id: str
    owner: str
    created_at: float
    last_seen: float
    client: dict
    #: Sends bytes on the session's websocket (``/api/viewer/ws``).
    send: Any
    state: dict = field(default_factory=dict)
    state_at: float | None = None

    def public(self, now: float) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "idle_s": round(now - self.last_seen, 3),
            "client": self.client,
            "state": self.state,
            "state_at": self.state_at,
        }


@dataclass
class _Issued:
    command_id: str
    owner: str
    #: ``{"command": {...}}`` or ``{"commands": [...]}``: what the tabs receive.
    payload: dict
    issued_at: float
    sessions: list[str]
    results: dict[str, dict] = field(default_factory=dict)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def public(self) -> dict:
        return {
            "command_id": self.command_id,
            **self.payload,
            "issued_at": self.issued_at,
            "results": [{"session_id": sid, **self.results.get(sid, {"status": "pending"})} for sid in self.sessions],
        }


class ViewerHub:
    """The viewer sessions (one per open websocket) and their commands, per user. Runs on the API's
    event loop."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._issued: OrderedDict[str, _Issued] = OrderedDict()
        self._tickets: dict[str, tuple[str, float, dict]] = {}

    # -- sessions ------------------------------------------------------------------------------

    def _drop(self, sid: str) -> None:
        if self._sessions.pop(sid, None) is None:
            return
        for issued in self._issued.values():
            if sid in issued.sessions and sid not in issued.results:
                issued.results[sid] = {"status": "gone", "error": "the viewer session closed before it answered"}
                self._maybe_done(issued)

    def register(self, owner: str, send: Any, client: dict | None = None, state: dict | None = None) -> dict:
        """A new session of ``owner``'s, whose commands go out through ``send`` (an async function
        taking bytes). Past :data:`MAX_SESSIONS_PER_USER` the longest-silent one is dropped."""
        mine = sorted((s for s in self._sessions.values() if s.owner == owner), key=lambda s: s.last_seen)
        for old in mine[: max(0, len(mine) - MAX_SESSIONS_PER_USER + 1)]:
            self._drop(old.session_id)
        now = self._clock()
        s = _Session(
            session_id=uuid.uuid4().hex,
            owner=owner,
            created_at=now,
            last_seen=now,
            client=dict(client or {}),
            send=send,
        )
        if state is not None:
            s.state, s.state_at = dict(state), now
        self._sessions[s.session_id] = s
        return s.public(now)

    def _mine(self, owner: str, sid: str) -> _Session | None:
        s = self._sessions.get(sid)
        return s if s is not None and s.owner == owner else None

    def session(self, owner: str, sid: str) -> dict | None:
        s = self._mine(owner, sid)
        return s.public(self._clock()) if s else None

    def sessions(self, owner: str) -> list[dict]:
        now = self._clock()
        mine = [s for s in self._sessions.values() if s.owner == owner]
        return [s.public(now) for s in sorted(mine, key=lambda s: s.last_seen, reverse=True)]

    def close(self, owner: str, sid: str) -> bool:
        if self._mine(owner, sid) is None:
            return False
        self._drop(sid)
        return True

    def report_state(self, owner: str, sid: str, state: dict) -> bool:
        s = self._mine(owner, sid)
        if s is None:
            return False
        s.state, s.state_at = dict(state), self._clock()
        s.last_seen = s.state_at
        return True

    # -- commands ------------------------------------------------------------------------------

    def issue(self, owner: str, payload: dict, session_id: str | None = None) -> dict | None:
        """Send ``payload`` (already validated: ``{"command"}`` or ``{"commands"}``, or a bare command)
        to one of the owner's sessions, or all of them. None when there is no such session (or none
        at all)."""
        if "command" not in payload and "commands" not in payload:
            payload = {"command": payload}
        if session_id is not None:
            targets = [s for s in [self._mine(owner, session_id)] if s is not None]
        else:
            targets = [s for s in self._sessions.values() if s.owner == owner]
        if not targets:
            return None
        issued = _Issued(
            command_id=uuid.uuid4().hex,
            owner=owner,
            payload=payload,
            issued_at=self._clock(),
            sessions=[s.session_id for s in targets],
        )
        self._issued[issued.command_id] = issued
        mine = [cid for cid, i in self._issued.items() if i.owner == owner]
        for cid in mine[: max(0, len(mine) - RESULTS_KEPT)]:
            self._issued.pop(cid, None)
        loop = asyncio.get_running_loop()
        for s in targets:
            loop.create_task(self._push(s, issued.command_id, payload))
        return issued.public()

    async def _push(self, s: _Session, command_id: str, payload: dict) -> None:
        """Send a command down a session's websocket; a socket that cannot take it ends the session."""
        from ada.comms.viewer_wire import command_message

        try:
            await s.send(command_message(command_id, payload))
        except Exception:  # noqa: BLE001 - a dead socket: the session is gone
            self._drop(s.session_id)

    # -- websocket tickets ---------------------------------------------------------------------

    def mint_ticket(self, owner: str, client: dict | None = None) -> str:
        """A single-use ticket that opens ``/api/viewer/ws`` as ``owner`` within :data:`TICKET_TTL_S`:
        a browser's websocket cannot carry the bearer header the rest of the API reads."""
        now = self._clock()
        self._tickets = {k: v for k, v in self._tickets.items() if v[1] > now}
        ticket = uuid.uuid4().hex + uuid.uuid4().hex
        self._tickets[ticket] = (owner, now + TICKET_TTL_S, dict(client or {}))
        return ticket

    def redeem_ticket(self, ticket: str) -> tuple[str, dict] | None:
        """The ticket's (owner, client), once; None for an unknown, used or expired ticket."""
        found = self._tickets.pop(ticket or "", None)
        if found is None or found[1] <= self._clock():
            return None
        return found[0], found[2]

    def _maybe_done(self, issued: _Issued) -> None:
        if all(sid in issued.results for sid in issued.sessions):
            issued.done.set()

    async def wait(self, owner: str, command_id: str, wait_s: float) -> dict | None:
        issued = self._issued.get(command_id)
        if issued is None or issued.owner != owner:
            return None
        if wait_s > 0 and not issued.done.is_set():
            try:
                await asyncio.wait_for(issued.done.wait(), timeout=min(wait_s, COMMAND_WAIT_MAX_S))
            except asyncio.TimeoutError:
                pass
        return issued.public()

    def complete(self, owner: str, sid: str, command_id: str, outcome: dict) -> bool:
        """Record a session's answer to a command it was sent. False when either is not the
        owner's, or the command was not sent to that session."""
        s = self._mine(owner, sid)
        issued = self._issued.get(command_id)
        if s is None or issued is None or issued.owner != owner or sid not in issued.sessions:
            return False
        now = self._clock()
        s.last_seen = now
        state = outcome.get("state")
        if isinstance(state, dict):
            s.state, s.state_at = dict(state), now
        ok = bool(outcome.get("ok"))
        issued.results[sid] = {
            "status": "done" if ok else "error",
            "error": None if ok else str(outcome.get("error") or "failed"),
            "result": outcome.get("result"),
            "state": s.state,
            "answered_at": now,
        }
        if "commands" in issued.payload:
            issued.results[sid]["steps"] = _steps(outcome.get("steps"), issued.payload["commands"])
        self._maybe_done(issued)
        return True


_STEP_STATUSES = ("done", "error", "skipped")


def _steps(raw: Any, commands: list[dict]) -> list[dict]:
    """A batch's per-command outcomes as the tab reported them, one per command in order; a
    command the tab said nothing about is ``skipped``."""
    by_index: dict[int, dict] = {}
    for step in raw if isinstance(raw, list) else []:
        if isinstance(step, dict) and isinstance(step.get("index"), int):
            by_index[step["index"]] = step
    out = []
    for i, command in enumerate(commands):
        step = by_index.get(i, {})
        status = step.get("status") if step.get("status") in _STEP_STATUSES else "skipped"
        out.append(
            {
                "index": i,
                "action": command.get("action"),
                "status": status,
                "error": str(step.get("error")) if status == "error" and step.get("error") else None,
                "result": step.get("result") if status == "done" else None,
            }
        )
    return out


def state_size_ok(state: Any) -> bool:
    try:
        return len(json.dumps(state, default=str)) <= MAX_STATE_BYTES
    except (TypeError, ValueError):
        return False

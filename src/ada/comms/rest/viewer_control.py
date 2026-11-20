"""Driving a user's open viewer from the REST API: the command vocabulary and the session hub.

An agent (a script, later an MCP server wrapping these routes 1:1) asks the viewer a user has open
to show something -- focus a clash joint, select objects by name, move the camera, load a detail
model -- or to say what it shows. ``routes/viewer_control.py`` serves it; this module holds what the
routes share:

THE VOCABULARY. One typed command per action (:data:`ViewerCommand`, a union discriminated by
``action``), validated here before anything reaches a browser, and published as JSON schema
(``GET /api/viewer/commands/schema``) so a tool layer can be generated from it. The browser runs
them (``src/frontend/src/services/viewerControl``).

THE TRANSPORT. A viewer tab REGISTERS a session, then LONG-POLLS for its commands and posts each
command's outcome back, with what it shows now. Long-polling rather than server-sent events: the
SPA authenticates with a bearer header, which ``EventSource`` cannot send, and the deployed studio
already follows server state by polling (the connection builder does); a poll that holds for
:data:`POLL_MAX_S` costs one idle request per tab and needs nothing from a proxy.

WHOSE VIEWER. A session belongs to the user who registered it; every lookup is by (user, session),
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
    "POLL_MAX_S",
    "SESSION_STALE_S",
    "ViewerCommand",
    "ViewerHub",
    "command_schema",
    "parse_command",
    "state_size_ok",
]

#: The longest a browser's poll is held open before it answers "nothing".
POLL_MAX_S = 25.0
#: A session not heard from (no poll, no state) for this long is gone: its tab closed or slept.
SESSION_STALE_S = 75.0
#: The longest an agent's command waits for the viewers to answer.
COMMAND_WAIT_MAX_S = 60.0
#: Sessions kept per user; the longest-silent one is dropped for a new one past this.
MAX_SESSIONS_PER_USER = 16
#: A state report larger than this is refused (it is a summary, not a model).
MAX_STATE_BYTES = 256 * 1024
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
    Union[FocusJoint, Select, ClearSelection, Camera, OpenDetailModel, FindObjects, GetState],
    Field(discriminator="action"),
]
COMMAND_ADAPTER: TypeAdapter = TypeAdapter(ViewerCommand)


def command_schema() -> dict:
    """The vocabulary as JSON schema: one ``oneOf`` branch per action."""
    return COMMAND_ADAPTER.json_schema()


def parse_command(raw: Any) -> dict:
    """``raw`` validated into its command, as the plain JSON the browser receives (defaults filled
    in). Raises pydantic's ``ValidationError``."""
    return COMMAND_ADAPTER.validate_python(raw).model_dump(mode="json")


# ── the hub ──────────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Session:
    session_id: str
    owner: str
    created_at: float
    last_seen: float
    client: dict
    state: dict = field(default_factory=dict)
    state_at: float | None = None
    pending: list[dict] = field(default_factory=list)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def public(self, now: float) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "idle_s": round(now - self.last_seen, 3),
            "client": self.client,
            "state": self.state,
            "state_at": self.state_at,
            "pending_commands": len(self.pending),
        }


@dataclass
class _Issued:
    command_id: str
    owner: str
    command: dict
    issued_at: float
    sessions: list[str]
    results: dict[str, dict] = field(default_factory=dict)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def public(self) -> dict:
        return {
            "command_id": self.command_id,
            "command": self.command,
            "issued_at": self.issued_at,
            "results": [{"session_id": sid, **self.results.get(sid, {"status": "pending"})} for sid in self.sessions],
        }


class ViewerHub:
    """The viewer sessions and their commands, per user. Async methods run on the API's event loop."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._issued: OrderedDict[str, _Issued] = OrderedDict()

    # -- sessions ------------------------------------------------------------------------------

    def _prune(self) -> None:
        now = self._clock()
        for sid, s in list(self._sessions.items()):
            if now - s.last_seen > SESSION_STALE_S:
                self._drop(sid)

    def _drop(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        if s is None:
            return
        s.wake.set()
        for issued in self._issued.values():
            if sid in issued.sessions and sid not in issued.results:
                issued.results[sid] = {"status": "gone", "error": "the viewer session closed before it answered"}
                self._maybe_done(issued)

    def register(self, owner: str, client: dict | None = None, state: dict | None = None) -> dict:
        self._prune()
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
        )
        if state is not None:
            s.state, s.state_at = dict(state), now
        self._sessions[s.session_id] = s
        return s.public(now)

    def _mine(self, owner: str, sid: str) -> _Session | None:
        s = self._sessions.get(sid)
        return s if s is not None and s.owner == owner else None

    def session(self, owner: str, sid: str) -> dict | None:
        self._prune()
        s = self._mine(owner, sid)
        return s.public(self._clock()) if s else None

    def sessions(self, owner: str) -> list[dict]:
        self._prune()
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

    async def poll(self, owner: str, sid: str, wait_s: float) -> list[dict] | None:
        """The session's commands, waiting up to ``wait_s`` for one. None: no such session."""
        s = self._mine(owner, sid)
        if s is None:
            return None
        s.last_seen = self._clock()
        if not s.pending and wait_s > 0:
            s.wake.clear()
            try:
                await asyncio.wait_for(s.wake.wait(), timeout=min(wait_s, POLL_MAX_S))
            except asyncio.TimeoutError:
                pass
        if self._sessions.get(sid) is not s:
            return None
        s.last_seen = self._clock()
        out, s.pending = s.pending, []
        return out

    # -- commands ------------------------------------------------------------------------------

    def issue(self, owner: str, command: dict, session_id: str | None = None) -> dict | None:
        """Queue ``command`` (already validated) for one of the owner's sessions, or all of them.
        None when there is no such session (or none at all)."""
        self._prune()
        if session_id is not None:
            targets = [s for s in [self._mine(owner, session_id)] if s is not None]
        else:
            targets = [s for s in self._sessions.values() if s.owner == owner]
        if not targets:
            return None
        issued = _Issued(
            command_id=uuid.uuid4().hex,
            owner=owner,
            command=command,
            issued_at=self._clock(),
            sessions=[s.session_id for s in targets],
        )
        self._issued[issued.command_id] = issued
        mine = [cid for cid, i in self._issued.items() if i.owner == owner]
        for cid in mine[: max(0, len(mine) - RESULTS_KEPT)]:
            self._issued.pop(cid, None)
        for s in targets:
            s.pending.append({"command_id": issued.command_id, "command": command, "issued_at": issued.issued_at})
            s.wake.set()
        return issued.public()

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

    def result(self, owner: str, command_id: str) -> dict | None:
        issued = self._issued.get(command_id)
        return issued.public() if issued is not None and issued.owner == owner else None

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
        self._maybe_done(issued)
        return True


def state_size_ok(state: Any) -> bool:
    try:
        return len(json.dumps(state, default=str)) <= MAX_STATE_BYTES
    except (TypeError, ValueError):
        return False

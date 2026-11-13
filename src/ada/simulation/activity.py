"""The activity feed of a simulation run: what each of its actors was doing and planning, over time.

A run has ACTORS -- a robot, an arm, a solver stage, the mission that directs them -- and a
provider that wants the run to be understood, not just watched, says what each is up to as the run
goes: what it is doing now, what it intends to do, and what happened to it. The viewer shows that
as the run's console, in step with the replay and while the run is still going; core reads the
feed itself, so a provider needs no viewer code for it.

The feed is the run artefact with the role ``activity`` (:data:`ACTIVITY_ROLE`): a text file of
JSON Lines, append-only, so it can be stored again and again while it grows (``partial=True``)
and a reader takes only the lines it has not seen.

* The first line is the header: ``{"schema": "ada-activity/1", "actors": [...]}``. An actor is
  ``{"id": "amr", "label": "Logistics robot", "color": "#4db3e6"}``; label and colour are
  optional, and an actor that only ever appears in an entry is listed under its id.
  A later line of the same shape, ``{"actors": [...]}``, names more actors or gives one its label
  and colour: whoever writes the feed may learn who the actors are after it has begun it.
* Every other line is an entry: ``{"t": 41.2, "actor": "amr", "kind": "state", "text": "Docked"}``.

  ``t``      seconds on the run's own clock: the one its recording and its model's animation are
             on, so the console shows the moment the replay shows.
  ``actor``  the id of who it is about.
  ``kind``   ``"state"``: what the actor is doing from ``t`` on, until its next state.
             ``"plan"``:  what it intends to do from ``t`` on, until its next plan (empty text:
             none).
             ``"event"``: something that happened at ``t`` (the default).
  ``text``   one line, for a person.
  ``level``  ``"info"`` (the default), ``"warn"`` or ``"error"``.
  ``data``   optional, anything JSON: the numbers behind the line (a goal pose, an error in mm).

Stdlib only. A provider whose run is recorded somewhere without adapy (a sidecar container)
writes the same lines itself: the format is this docstring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Iterable

__all__ = [
    "ACTIVITY_MEDIA_TYPE",
    "ACTIVITY_ROLE",
    "ACTIVITY_SCHEMA",
    "ActivityActor",
    "ActivityEntry",
    "ActivityFeed",
    "ActivityWriter",
    "parse_activity",
]

ACTIVITY_SCHEMA = "ada-activity/1"
#: The artefact role a run's feed is stored with (one of ``COMMON_ARTEFACT_ROLES``).
ACTIVITY_ROLE = "activity"
ACTIVITY_MEDIA_TYPE = "application/x-ndjson"

KINDS = ("state", "plan", "event")
LEVELS = ("info", "warn", "error")


@dataclass(frozen=True)
class ActivityActor:
    id: str
    label: str = ""
    color: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.label:
            out["label"] = self.label
        if self.color:
            out["color"] = self.color
        return out


@dataclass(frozen=True)
class ActivityEntry:
    t: float
    actor: str
    text: str
    kind: str = "event"
    level: str = "info"
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"t": round(float(self.t), 3), "actor": self.actor, "kind": self.kind, "text": self.text}
        if self.level != "info":
            out["level"] = self.level
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass
class ActivityFeed:
    """A feed as read: its actors (those of the header, then any an entry named) and its entries."""

    actors: list[ActivityActor] = field(default_factory=list)
    entries: list[ActivityEntry] = field(default_factory=list)


class ActivityWriter:
    """Writes a feed, a line at a time, flushing each: the file is always a whole number of lines,
    so it can be stored (``ctx.store_artefact(..., role="activity", partial=True)``) at any moment."""

    def __init__(self, path: str | Path, actors: Iterable[ActivityActor] = ()) -> None:
        self.path = Path(path)
        self._fh: IO[str] = self.path.open("w", encoding="utf-8", newline="\n")
        self._line({"schema": ACTIVITY_SCHEMA, "actors": [a.to_dict() for a in actors]})

    def _line(self, value: dict[str, Any]) -> None:
        self._fh.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._fh.flush()

    def actors(self, actors: Iterable[ActivityActor]) -> None:
        """Name more actors, or give one its label and colour, after the feed was begun."""
        self._line({"actors": [a.to_dict() for a in actors]})

    def add(self, t: float, actor: str, text: str, *, kind: str = "event", level: str = "info", data: Any = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"an activity entry's kind is one of {', '.join(KINDS)}, not {kind!r}")
        if level not in LEVELS:
            raise ValueError(f"an activity entry's level is one of {', '.join(LEVELS)}, not {level!r}")
        self._line(ActivityEntry(t, actor, text, kind, level, data).to_dict())

    def state(self, t: float, actor: str, text: str, **more: Any) -> None:
        """What `actor` is doing from `t` on."""
        self.add(t, actor, text, kind="state", **more)

    def plan(self, t: float, actor: str, text: str, **more: Any) -> None:
        """What `actor` intends to do from `t` on; an empty text takes the plan away."""
        self.add(t, actor, text, kind="plan", **more)

    def event(self, t: float, actor: str, text: str, **more: Any) -> None:
        """Something that happened to `actor` at `t`."""
        self.add(t, actor, text, kind="event", **more)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "ActivityWriter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def parse_activity(text: str) -> ActivityFeed:
    """A feed from its text. Lenient, as a reader of a file that is still being written has to be:
    a line that is not an entry (cut short, not JSON, without a time or an actor) is left out.
    ValueError when the first line is not this format's header."""
    lines = text.splitlines()
    try:
        header = json.loads(lines[0]) if lines else None
    except ValueError:
        header = None
    if not isinstance(header, dict) or header.get("schema") != ACTIVITY_SCHEMA:
        raise ValueError(f"not an activity feed: its first line is not an {ACTIVITY_SCHEMA} header")
    feed = ActivityFeed()
    known: dict[str, ActivityActor] = {}

    def name(actors: Any) -> None:
        for a in actors if isinstance(actors, list) else []:
            if isinstance(a, dict) and isinstance(a.get("id"), str):
                known[a["id"]] = ActivityActor(a["id"], str(a.get("label") or ""), str(a.get("color") or ""))

    name(header.get("actors"))
    for line in lines[1:]:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("actors"), list) and not isinstance(e.get("t"), (int, float)):
            name(e["actors"])
            continue
        if not isinstance(e, dict) or not isinstance(e.get("actor"), str) or not isinstance(e.get("t"), (int, float)):
            continue
        kind = e.get("kind") if e.get("kind") in KINDS else "event"
        level = e.get("level") if e.get("level") in LEVELS else "info"
        feed.entries.append(ActivityEntry(float(e["t"]), e["actor"], str(e.get("text") or ""), kind, level, e.get("data")))
        known.setdefault(e["actor"], ActivityActor(e["actor"]))
    feed.actors = list(known.values())
    return feed

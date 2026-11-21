"""Association rows: what is known about one element, one row per element per fact.

FIVE KINDS (:data:`KINDS`), each written by the document that produced it:

==========  ==========================================================  ==============================
kind        ``data``                                                    written by
==========  ==========================================================  ==============================
contact     run_key, run_id, contact_id, other_key, role, area,         a clash run stored or annotated
            distance, n_patches, applicable[]
connection  detail_id, joint_id, connection, spec, role, centre,        a detail model settled
            status (built | not_built | failed)
weld        detail_id, weld_name, weld_type, length, members[], kept,   a detail model settled
            dup_of
subject     subject_kind, subject_key, detail_id                        a subject file written
sim_run     subject_key, run_id, provider, params_hash, status,          a simulation run started, and
            started_by, finished_at, batch                              again when it ends
==========  ==========================================================  ==============================

A contact gives two rows (one per member), a connection one per member, a weld one per member it
joins. Every row carries the element's ancestors (``anc``), the connection key (``ck``) where it has
one, its ``group`` (the ck, or the member pair of a contact: what a listing groups by), the
``source`` it was measured on (a target token) and, where the producer knew it, the element's
fingerprint (``fp``) at the time.

STALE, SUPERSEDED, ORPHAN are read, never written (:func:`flag_rows`):

* stale -- the element's current fingerprint (its :class:`ElementState`) differs from the row's;
* orphan -- the row's source has a current snapshot and the element is not in it (renamed or gone);
* superseded -- a newer row of the same kind exists for the same element and group.

STDLIB ONLY.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ACTIVE_SIM_STATUSES",
    "KINDS",
    "SHEET_SCHEMA",
    "STATE_SCHEMA",
    "ElementState",
    "LinkRow",
    "SourceState",
    "contact_group",
    "decode_sheet",
    "decode_state",
    "doc_id",
    "encode_sheet",
    "encode_state",
    "flag_rows",
    "group_rows",
    "is_under",
    "newest_by_group",
    "params_hash",
]

KINDS = ("contact", "connection", "weld", "subject", "sim_run")
ACTIVE_SIM_STATUSES = ("queued", "running")

SHEET_SCHEMA = "ada.assoc/sheet@1"
STATE_SCHEMA = "ada.assoc/state@1"


def doc_id(doc_key: str) -> str:
    """The file-safe id of a producing document's key."""
    return hashlib.sha256(str(doc_key).encode("utf-8")).hexdigest()[:32]


def params_hash(parameters: Mapping[str, Any] | None) -> str:
    """A simulation's parameters as one hash: what "the same run again" compares."""
    raw = json.dumps(dict(parameters or {}), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def contact_group(a: str, b: str) -> str:
    """A contact's group: its two members, in either order."""
    return "pair:" + hashlib.sha256("|".join(sorted((a, b))).encode("utf-8")).hexdigest()[:16]


@dataclass
class LinkRow:
    kind: str
    element_key: str
    #: The document that produced it (a run key, a detail record key, a subject key, a run record key).
    doc_key: str
    anc: tuple[str, ...] = ()
    ck: str | None = None
    group: str | None = None
    source: str | None = None
    revision: str | None = None
    geometry_sha256: str | None = None
    fp: str | None = None
    created_at: str | None = None
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown link kind {self.kind!r} (known: {', '.join(KINDS)})")
        self.anc = tuple(dict.fromkeys(a for a in self.anc if a and a != self.element_key))
        if self.group is None:
            self.group = self.ck

    @property
    def group_key(self) -> str:
        """What a listing groups by and pages on: the group, else the row's own document."""
        return self.group or f"doc:{self.doc_key}"

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind, "element_key": self.element_key, "doc_key": self.doc_key}
        for name in ("anc", "ck", "group", "source", "revision", "geometry_sha256", "fp", "created_at"):
            value = getattr(self, name)
            if value not in (None, ()):
                out[name] = list(value) if name == "anc" else value
        out["data"] = dict(self.data)
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LinkRow":
        return cls(
            kind=str(raw["kind"]),
            element_key=str(raw["element_key"]),
            doc_key=str(raw.get("doc_key") or ""),
            anc=tuple(str(a) for a in raw.get("anc") or ()),
            ck=raw.get("ck") or None,
            group=raw.get("group") or None,
            source=raw.get("source") or None,
            revision=raw.get("revision") or None,
            geometry_sha256=raw.get("geometry_sha256") or None,
            fp=raw.get("fp") or None,
            created_at=raw.get("created_at") or None,
            data=dict(raw.get("data") or {}),
        )


@dataclass(frozen=True)
class ElementState:
    """An element's newest fingerprint, as its source was last measured."""

    element_key: str
    fp: str | None
    source: str | None = None
    revision: str | None = None
    seen_at: str | None = None


@dataclass(frozen=True)
class SourceState:
    """A source's newest snapshot: every element it has, with its fingerprint."""

    source: str
    seen_at: str
    revision: str | None = None
    geometry_sha256: str | None = None
    fps: Mapping[str, str | None] = field(default_factory=dict)

    def state(self, element_key: str) -> ElementState | None:
        if element_key not in self.fps:
            return None
        return ElementState(element_key, self.fps[element_key], self.source, self.revision, self.seen_at)


def is_under(row: LinkRow, keys: Iterable[str]) -> bool:
    wanted = set(keys)
    return row.element_key in wanted or any(a in wanted for a in row.anc)


def flag_rows(
    rows: Sequence[LinkRow],
    *,
    states: Mapping[str, ElementState] | None = None,
    sources: Mapping[str, SourceState] | None = None,
    newest: Mapping[tuple[str, str, str], str] | None = None,
) -> list[dict]:
    """Each row as a dict with its ``stale``, ``orphan`` and ``superseded`` flags.

    ``states`` / ``sources``: the current fingerprints, by element key and by source. ``newest``:
    ``(kind, element_key, group_key) -> created_at`` of the newest row, when the caller has it for
    more rows than these (a page of a longer listing); otherwise worked out from ``rows``."""
    states = states or {}
    sources = sources or {}
    if newest is None:
        newest = newest_by_group(rows)
    out = []
    for row in rows:
        d = row.to_dict()
        state = states.get(row.element_key)
        snapshot = sources.get(row.source or "")
        if snapshot is not None and str(snapshot.seen_at or "") < str(row.created_at or ""):
            snapshot = None  # measured before the row was made: it says nothing about the row
        if state is None and snapshot is not None:
            state = snapshot.state(row.element_key)
        d["orphan"] = snapshot is not None and state is None
        d["stale"] = bool(row.fp and state is not None and state.fp and state.fp != row.fp)
        latest = newest.get((row.kind, row.element_key, row.group_key))
        d["superseded"] = latest is not None and str(row.created_at or "") < latest
        out.append(d)
    return out


def newest_by_group(rows: Iterable[LinkRow]) -> dict[tuple[str, str, str], str]:
    newest: dict[tuple[str, str, str], str] = {}
    for row in rows:
        k = (row.kind, row.element_key, row.group_key)
        at = str(row.created_at or "")
        if at > newest.get(k, ""):
            newest[k] = at
    return newest


def group_rows(flagged: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Flagged rows as groups -- one per ck (or contact pair) -- in group-key order: ``{group, ck,
    kinds: {kind: n}, rows}``."""
    groups: dict[str, dict] = {}
    for d in flagged:
        g = d.get("group") or f"doc:{d.get('doc_key')}"
        entry = groups.setdefault(g, {"group": g, "ck": d.get("ck"), "kinds": {}, "rows": []})
        entry["kinds"][d["kind"]] = entry["kinds"].get(d["kind"], 0) + 1
        entry["rows"].append(dict(d))
    return [groups[g] for g in sorted(groups)]


# ── the files backend's documents ─────────────────────────────────────────────────────────────


def encode_sheet(kind: str, doc_key: str, rows: Iterable[LinkRow], *, written_at: str | None = None) -> bytes:
    rows = [r for r in rows if r.kind == kind]
    doc = {
        "schema": SHEET_SCHEMA,
        "kind": kind,
        "doc_key": doc_key,
        "written_at": written_at,
        "rows": [r.to_dict() for r in rows],
    }
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def decode_sheet(raw: bytes | str | Mapping[str, Any]) -> tuple[str, str, list[LinkRow]]:
    """``(kind, doc_key, rows)``; ValueError for anything that is not a sheet."""
    doc = json.loads(raw) if isinstance(raw, (bytes, str)) else dict(raw)
    if not isinstance(doc, dict) or doc.get("schema") != SHEET_SCHEMA:
        raise ValueError("not an association sheet")
    return str(doc["kind"]), str(doc["doc_key"]), [LinkRow.from_dict(r) for r in doc.get("rows") or ()]


def encode_state(state: SourceState) -> bytes:
    doc = {
        "schema": STATE_SCHEMA,
        "source": state.source,
        "seen_at": state.seen_at,
        "revision": state.revision,
        "geometry_sha256": state.geometry_sha256,
        "fps": dict(state.fps),
    }
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def decode_state(raw: bytes | str | Mapping[str, Any]) -> SourceState:
    doc = json.loads(raw) if isinstance(raw, (bytes, str)) else dict(raw)
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA:
        raise ValueError("not an element state document")
    return SourceState(
        source=str(doc["source"]),
        seen_at=str(doc.get("seen_at") or ""),
        revision=doc.get("revision"),
        geometry_sha256=doc.get("geometry_sha256"),
        fps={str(k): v for k, v in (doc.get("fps") or {}).items()},
    )

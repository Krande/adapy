"""``ada.clash/result@1`` -- what one clash check found, as a document.

ONE OBJECT, READ BY EVERYTHING. The panel's groups, counts, filters and its hand-off to a
generator are all functions of this document; the browser recomputes nothing. That is the same
discipline the asset browser's ``AssetView`` holds, for the same reason: a surface that re-derives
its own numbers can disagree with the row beside it, and no screenshot of the result is then
explainable.

WHAT IS IN CORE'S VOCABULARY, AND WHAT IS NOT. A joint's ``type_key`` is built ONLY from terms core
already owns -- how many members meet, each one's :class:`MemberKind`, its section family, its
``member_type`` (Column / Girder / Brace) and one bucketed angle. No fabrication-process term, no
provider's name for anything. That is what lets a joint type be recognised the same way whatever
package eventually details it, and it is enforced by a vocabulary gate in CI.

``applicable[]`` names the registered specs that could detail a joint, each with the CAPABILITY it
arrives with -- never the package that registered it. Core dispatches on that capability and never
learns whose code it is (Decision 1's convention, reused unchanged in Decision 10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "CLASH_RESULT_SCHEMA",
    "ApplicableSpec",
    "ClashResult",
    "ClashResultError",
    "JointMember",
    "JointRecord",
    "JointGroup",
    "parse_clash_result",
]

CLASH_RESULT_SCHEMA = "ada.clash/result@1"


class ClashResultError(ValueError):
    """A result document core refuses to read."""


@dataclass(frozen=True)
class JointMember:
    """One member meeting at a joint, in core's terms only."""

    name: str
    kind: str  # MemberKind.name -- BEAM | PLATE
    guid: str | None = None
    #: The section FAMILY (`section.type.value.upper()`), which is what `MemberCriteria`
    #: matches on -- never the profile's full designation, which no spec compares against.
    section: str | None = None
    #: Column | Girder | Brace for a beam; None for a plate, which has no axis to classify.
    member_type: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        for key in ("guid", "section", "member_type"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass(frozen=True)
class ApplicableSpec:
    """A registered spec that could detail this joint, and where it would run."""

    spec: str
    #: WHERE this spec would run. ``None`` means "wherever core runs" -- the default pool, or
    #: in-process on a queue-less viewer -- which is what a BUILT-IN spec carries, and the panel
    #: offers it without asking any heartbeat.
    #:
    #: A NON-NULL capability names a pool, and is the case that can be unavailable: if no live
    #: worker advertises that token the panel must show the spec as unavailable rather than offer
    #: a hand-off that would queue for ever. Absent and unavailable are therefore opposite
    #: answers, which is worth stating because an earlier version of this comment conflated them.
    capability: str | None = None
    tags: tuple[str, ...] = ()
    priority: int = 0

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"spec": self.spec, "priority": self.priority}
        if self.capability is not None:
            out["capability"] = self.capability
        if self.tags:
            out["tags"] = list(self.tags)
        return out


@dataclass(frozen=True)
class JointRecord:
    id: str
    centre: tuple[float, float, float]
    members: tuple[JointMember, ...]
    type_key: str
    type_label: str
    applicable: tuple[ApplicableSpec, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "centre": list(self.centre),
            "members": [m.to_dict() for m in self.members],
            "type_key": self.type_key,
            "type_label": self.type_label,
            "applicable": [a.to_dict() for a in self.applicable],
        }


@dataclass(frozen=True)
class JointGroup:
    """Joints that share a ``type_key``. The panel's ROWS are groups, not joints: a frame has
    hundreds of joints and a handful of KINDS of joint, and the kinds are what a person decides
    about."""

    type_key: str
    type_label: str
    count: int
    joint_ids: tuple[str, ...]
    applicable: tuple[ApplicableSpec, ...] = ()

    def to_dict(self) -> dict:
        return {
            "type_key": self.type_key,
            "type_label": self.type_label,
            "count": self.count,
            "joint_ids": list(self.joint_ids),
            "applicable": [a.to_dict() for a in self.applicable],
        }


@dataclass(frozen=True)
class ClashResult:
    source_key: str
    options: Mapping[str, Any]
    joints: tuple[JointRecord, ...]
    groups: tuple[JointGroup, ...]
    #: Name-keyed, and OMITTING what was not measured rather than zeroing it -- a zero is a
    #: measurement, and "this source has no members" must not read as "no joints were found".
    counts: Mapping[str, int] = field(default_factory=dict)
    source_sha256: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    schema: str = CLASH_RESULT_SCHEMA

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "schema": self.schema,
            "source_key": self.source_key,
            "options": dict(self.options),
            "counts": dict(self.counts),
            "joints": [j.to_dict() for j in self.joints],
            "groups": [g.to_dict() for g in self.groups],
            "provenance": dict(self.provenance),
        }
        if self.source_sha256 is not None:
            out["source_sha256"] = self.source_sha256
        if self.warnings:
            out["warnings"] = list(self.warnings)
        return out

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")


def group_joints(joints: Sequence[JointRecord]) -> tuple[JointGroup, ...]:
    """Fold joints into groups by ``type_key``, newest-largest first.

    ``sum(group.count) == len(joints)`` is a property the panel leans on (and a test pins): a
    joint that fell out of every group would be invisible in the only view that lists them.
    """
    by_key: dict[str, list[JointRecord]] = {}
    for joint in joints:
        by_key.setdefault(joint.type_key, []).append(joint)
    groups = []
    for key, members in by_key.items():
        # The applicable set of a GROUP is what every joint in it shares: offering a generator
        # for a group where it fits only some of the joints would hand the worker members the
        # spec cannot bind, and the failure would surface as a build error rather than as a
        # filter that never offered it.
        common: set[tuple] | None = None
        for joint in members:
            here = {(a.spec, a.capability, a.tags, a.priority) for a in joint.applicable}
            common = here if common is None else (common & here)
        applicable = tuple(
            ApplicableSpec(spec=s, capability=c, tags=t, priority=p)
            for (s, c, t, p) in sorted(common or set(), key=lambda x: (-x[3], x[0]))
        )
        groups.append(
            JointGroup(
                type_key=key,
                type_label=members[0].type_label,
                count=len(members),
                joint_ids=tuple(j.id for j in members),
                applicable=applicable,
            )
        )
    groups.sort(key=lambda g: (-g.count, g.type_key))
    return tuple(groups)


def parse_clash_result(doc: bytes | str | Mapping[str, Any]) -> ClashResult:
    """Read a result document. An unknown schema is REFUSED, never partially read."""
    if isinstance(doc, (bytes, str)):
        try:
            raw = json.loads(doc)
        except (ValueError, TypeError) as exc:
            raise ClashResultError(f"clash result is not valid JSON: {exc}") from exc
    else:
        raw = doc
    if not isinstance(raw, dict):
        raise ClashResultError(f"clash result must be a JSON object, got {type(raw).__name__}")
    if raw.get("schema") != CLASH_RESULT_SCHEMA:
        raise ClashResultError(
            f"unknown clash result schema {raw.get('schema')!r}: this core reads " f"{CLASH_RESULT_SCHEMA!r} only."
        )

    def _applicable(entries) -> tuple[ApplicableSpec, ...]:
        return tuple(
            ApplicableSpec(
                spec=str(a["spec"]),
                capability=a.get("capability"),
                tags=tuple(a.get("tags") or ()),
                priority=int(a.get("priority", 0)),
            )
            for a in entries or ()
        )

    joints = tuple(
        JointRecord(
            id=str(j["id"]),
            centre=tuple(float(v) for v in j["centre"]),  # type: ignore[arg-type]
            members=tuple(
                JointMember(
                    name=str(m["name"]),
                    kind=str(m["kind"]),
                    guid=m.get("guid"),
                    section=m.get("section"),
                    member_type=m.get("member_type"),
                )
                for m in j.get("members") or ()
            ),
            type_key=str(j["type_key"]),
            type_label=str(j.get("type_label") or j["type_key"]),
            applicable=_applicable(j.get("applicable")),
        )
        for j in raw.get("joints") or ()
    )
    groups = tuple(
        JointGroup(
            type_key=str(g["type_key"]),
            type_label=str(g.get("type_label") or g["type_key"]),
            count=int(g["count"]),
            joint_ids=tuple(str(i) for i in g.get("joint_ids") or ()),
            applicable=_applicable(g.get("applicable")),
        )
        for g in raw.get("groups") or ()
    )
    return ClashResult(
        source_key=str(raw.get("source_key") or ""),
        options=dict(raw.get("options") or {}),
        joints=joints,
        groups=groups,
        counts={str(k): int(v) for k, v in (raw.get("counts") or {}).items()},
        source_sha256=raw.get("source_sha256"),
        provenance=dict(raw.get("provenance") or {}),
        warnings=tuple(str(w) for w in raw.get("warnings") or ()),
    )

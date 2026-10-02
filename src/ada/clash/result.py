"""``ada.clash/result@2`` -- what one clash check found, as a document.

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
    "READABLE_CLASH_RESULT_SCHEMAS",
    "ApplicableSpec",
    "ClashContact",
    "ClashResult",
    "ClashResultError",
    "JointMember",
    "JointRecord",
    "JointGroup",
    "parse_clash_result",
]

#: @2 over @1: a typed ``contact``, member ``role``/``end``, and the ``checker`` that produced the
#: result. Every addition is optional, so an @1 document reads as an @2 one with those absent --
#: which is exactly what it was: core's checker, axis passes, no contact measured.
CLASH_RESULT_SCHEMA = "ada.clash/result@2"
READABLE_CLASH_RESULT_SCHEMAS = ("ada.clash/result@1", CLASH_RESULT_SCHEMA)

Vec3 = tuple[float, float, float]


def _vec3(value) -> Vec3 | None:
    if value is None:
        return None
    x, y, z = (float(v) for v in value)
    return (x, y, z)


def _opt_float(value) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True)
class ClashContact:
    """What a geometric pass measured where two members meet -- the neutral form of a contact.

    Every field is optional: an axis pass measures none of them and carries no contact at all, a
    mesh pass measures most of them. Absent means NOT MEASURED, never zero -- the same rule
    ``counts`` follows, because a penetration depth of 0 is a touching contact and an absent one is
    a contact nobody measured.

    ``near_points`` is one point per member, in the joint's ``members`` order: the point on that
    member's solid nearest the other. ``normal`` points from the first member towards the second --
    and a pass that resolves roles lists the INCOMING member first, so where roles are present the
    normal points incoming -> landing and ``near_points[0]`` is on the incoming member.

    ``penetration_depth`` is how deep the solids overlap, POSITIVE when they do (0 when they only
    touch). Collision libraries disagree on the sign -- coal reports overlap as negative -- so a
    pass converts to this convention rather than passing its library's through.

    ``extras`` is the checker's own, for what this vocabulary does not cover. Core carries it and
    reads none of it; a builder from the same provider may.
    """

    normal: Vec3 | None = None
    penetration_depth: float | None = None
    near_points: tuple[Vec3, ...] = ()
    #: Contact-patch area in m^2.
    contact_area: float | None = None
    #: The margin the checker inflated solids by when it tested them, in m.
    security_margin: float | None = None
    #: Degrees between the INCOMING member's axis and the contact normal, where the checker
    #: resolved which member is incoming (``JointMember.role``).
    incoming_angle_deg: float | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {}
        if self.normal is not None:
            out["normal"] = list(self.normal)
        if self.penetration_depth is not None:
            out["penetration_depth"] = self.penetration_depth
        if self.near_points:
            out["near_points"] = [list(p) for p in self.near_points]
        for key in ("contact_area", "security_margin", "incoming_angle_deg"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.extras:
            out["extras"] = dict(self.extras)
        return out

    # Read-only mapping access, for builders written when ``contact`` was an untyped mapping:
    # ``contact["penetration_depth"]`` and ``contact.get("patch_area")`` keep working, an extra
    # answering by its own key exactly as it did before it was moved under ``extras``.
    def _flat(self) -> dict:
        flat = self.to_dict()
        extras = flat.pop("extras", {})
        return {**extras, **flat}

    def __getitem__(self, key: str) -> Any:
        return self._flat()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._flat()

    def get(self, key: str, default: Any = None) -> Any:
        return self._flat().get(key, default)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ClashContact | None":
        """Read a contact. ``None`` in, ``None`` out; unknown keys go to ``extras`` rather than being
        dropped, so an @1 contact (an untyped mapping) survives the read whole."""
        if raw is None:
            return None
        if isinstance(raw, ClashContact):
            return raw
        known = {
            "normal",
            "penetration_depth",
            "near_points",
            "contact_area",
            "security_margin",
            "incoming_angle_deg",
            "extras",
        }
        extras = dict(raw.get("extras") or {})
        extras.update({k: v for k, v in raw.items() if k not in known})
        return cls(
            normal=_vec3(raw.get("normal")),
            penetration_depth=_opt_float(raw.get("penetration_depth")),
            near_points=tuple(_vec3(p) for p in raw.get("near_points") or ()),  # type: ignore[misc]
            contact_area=_opt_float(raw.get("contact_area")),
            security_margin=_opt_float(raw.get("security_margin")),
            incoming_angle_deg=_opt_float(raw.get("incoming_angle_deg")),
            extras=extras,
        )


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
    #: ``incoming`` | ``landing`` where the pass resolved which member lands on which; ``None``
    #: where it did not (a node shared by three columns has no landing). The same two words
    #: ``MemberRole`` binds a spec by, so a builder can trust them instead of re-deriving them.
    role: str | None = None
    #: Which end of this member is at the contact: ``start`` | ``end``, or ``None`` when the
    #: contact is not at an end (a plate, or a beam met mid-span).
    end: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        for key in ("guid", "section", "member_type", "role", "end"):
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
    #: The PASS that found this joint (`ada.clash.passes`), which is also what its id is hashed
    #: from. Two passes over one model find overlapping but different joints -- a shared node and
    #: a two-millimetre overlap are both true -- and a reader who cannot tell which pass produced
    #: a joint can neither judge it nor filter it out.
    origin: str = "beam-beam"
    #: What the pass measured at the contact, where it measured anything: contact normal,
    #: penetration depth, the nearest point on each member, patch area. Present only for a pass
    #: that works on geometry rather than axes, and passed to a connection builder as its
    #: ``clash`` argument -- the data a generator sizes its output from. Core reads none of it;
    #: it is carried, not interpreted.
    contact: ClashContact | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "centre": list(self.centre),
            "members": [m.to_dict() for m in self.members],
            "type_key": self.type_key,
            "type_label": self.type_label,
            "origin": self.origin,
            "applicable": [a.to_dict() for a in self.applicable],
        }
        if self.contact is not None:
            out["contact"] = self.contact.to_dict()
        return out


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
    #: One entry per pass the check KNEW ABOUT, each saying whether it ran and what it found.
    #: A pass that was available and not selected is in here too, because "not run" and "found
    #: nothing" are different answers and the panel offers the difference as a checkbox.
    passes: tuple[Mapping[str, Any], ...] = ()
    #: The checker that ran (``ada.clash.passes.ClashChecker``) and the pool it ran on. A detail
    #: job re-runs identification to get real members back, so it has to run where this ran.
    checker: str | None = None
    checker_capability: str | None = None
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
        if self.checker is not None:
            out["checker"] = self.checker
        if self.checker_capability is not None:
            out["checker_capability"] = self.checker_capability
        if self.source_sha256 is not None:
            out["source_sha256"] = self.source_sha256
        if self.warnings:
            out["warnings"] = list(self.warnings)
        if self.passes:
            out["passes"] = [dict(p) for p in self.passes]
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
    if raw.get("schema") not in READABLE_CLASH_RESULT_SCHEMAS:
        raise ClashResultError(
            f"unknown clash result schema {raw.get('schema')!r}: this core reads "
            f"{', '.join(READABLE_CLASH_RESULT_SCHEMAS)} only."
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
                    role=m.get("role"),
                    end=m.get("end"),
                )
                for m in j.get("members") or ()
            ),
            type_key=str(j["type_key"]),
            type_label=str(j.get("type_label") or j["type_key"]),
            applicable=_applicable(j.get("applicable")),
            # Defaulted, not required: a document written before joints carried their producer
            # still reads, and reads as what it was -- core's beam pass was the only one there.
            origin=str(j.get("origin") or "beam-beam"),
            contact=ClashContact.from_dict(j.get("contact")),
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
        passes=tuple(dict(p) for p in raw.get("passes") or ()),
        # Absent on @1, where core's checker was the only one there was.
        checker=raw.get("checker") or (raw.get("options") or {}).get("checker"),
        checker_capability=raw.get("checker_capability"),
    )

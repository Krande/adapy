"""Give a joint a TYPE, in core's vocabulary and nothing else.

THE POINT OF A TYPE KEY. A frame has hundreds of joints and a handful of KINDS of joint. The kinds
are what a person decides about ("detail every girder-to-column joint like this"), so the browser's
rows are groups and a group is a type key. Two joints share a key when the same decision applies to
both.

WHAT THE KEY MAY BE MADE OF -- the whole list, and it is short:

* how many members meet;
* each member's :class:`MemberKind` (BEAM / PLATE);
* its section FAMILY -- ``section.type.value.upper()``, which is exactly what ``MemberCriteria``
  matches on. Never the full profile designation: ``IPE200`` and ``IPE300`` meeting a column are
  the same decision, and a key that separated them would put two rows in front of a user who has
  one choice to make;
* its ``member_type`` (Column / Girder / Brace), which is an axis test, not a name;
* ONE bucketed angle, bucketed to the ranges specs actually declare (``AngleRange``).

Nothing else is admissible -- no fabrication-process term, no provider vocabulary. That is what
lets a joint type mean the same thing to whatever package eventually details it, and CI greps this
module for the words that would break it.

MEMBERS ARE SORTED INTO THE KEY, not taken in discovery order: the same two members meeting must
produce one key whichever the clash walk happened to see first, or the grouping splits by accident
of iteration.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ada.api.connections.spec import AngleRange, MemberKind, all_registered
from ada.clash.result import JointMember

__all__ = ["ANGLE_BUCKETS", "angle_bucket", "describe_member", "type_key_for", "type_label_for"]

# The buckets a key may name. Derived from the ranges REGISTERED SPECS declare, with a coarse
# fallback -- a key that invented finer buckets than any spec distinguishes would split groups no
# decision separates.
_FALLBACK_BUCKETS: tuple[tuple[str, AngleRange], ...] = (
    ("parallel", AngleRange(min_deg=-15.0, max_deg=15.0)),
    ("skew", AngleRange(min_deg=15.0, max_deg=75.0)),
    ("perpendicular", AngleRange(min_deg=75.0, max_deg=105.0)),
    ("skew", AngleRange(min_deg=105.0, max_deg=165.0)),
    ("parallel", AngleRange(min_deg=165.0, max_deg=180.0)),
)

ANGLE_BUCKETS = tuple(name for name, _ in _FALLBACK_BUCKETS)


def angle_bucket(angle_deg: float | None) -> str:
    """One word for an angle, or ``unknown`` when there is no angle to take.

    ``unknown`` is a real value, not a failure: a plate-to-plate contact has no pair of axes to
    measure between, and calling that ``parallel`` would be an invented fact.
    """
    if angle_deg is None:
        return "unknown"
    folded = abs(((float(angle_deg) + 180.0) % 360.0) - 180.0)
    for name, rng in _FALLBACK_BUCKETS:
        if rng.contains(folded):
            return name
    return "unknown"


def _section_family(member) -> str | None:
    """The family ``MemberCriteria`` compares against, or None for something with no section."""
    section = getattr(member, "section", None)
    if section is None:
        return None
    sec_type = getattr(section, "type", None)
    value = getattr(sec_type, "value", None)
    return str(value).upper() if value is not None else None


def describe_member(member) -> JointMember:
    """One member, reduced to the terms a type key may use."""
    kind = MemberKind.from_ada_type(member)
    member_type = None
    if kind is MemberKind.BEAM:
        try:
            member_type = member.member_type
        except Exception:  # noqa: BLE001 - a member whose axis cannot be classified still has a type key
            member_type = None
    return JointMember(
        name=str(getattr(member, "name", "") or ""),
        kind=kind.name,
        guid=getattr(member, "guid", None),
        section=_section_family(member) if kind is MemberKind.BEAM else "PLATE",
        member_type=member_type,
    )


def _member_token(m: JointMember) -> str:
    parts = [m.kind]
    if m.section:
        parts.append(m.section)
    if m.member_type:
        parts.append(m.member_type.upper())
    return ":".join(parts)


def type_key_for(members: Sequence[JointMember], angle_deg: float | None = None) -> str:
    """``<n>|<member token>+…|<angle bucket>`` -- stable, sorted, and core-vocabulary only."""
    tokens = sorted(_member_token(m) for m in members)
    return f"{len(members)}|{'+'.join(tokens)}|{angle_bucket(angle_deg)}"


def type_label_for(members: Sequence[JointMember], angle_deg: float | None = None) -> str:
    """The same fact, for a human: "2 × BEAM · IPE/HEB · Girder→Column · perpendicular"."""
    kinds = sorted({m.kind for m in members})
    sections = sorted({m.section for m in members if m.section})
    types = [m.member_type for m in members if m.member_type]
    bits = [f"{len(members)} × {'/'.join(kinds)}"]
    if sections:
        bits.append("/".join(sections))
    if types:
        bits.append("→".join(sorted(types)))
    bucket = angle_bucket(angle_deg)
    if bucket != "unknown":
        bits.append(bucket)
    return " · ".join(bits)


def declared_angle_ranges() -> Iterable[AngleRange]:
    """Every angle range a REGISTERED spec distinguishes. Exposed so a deployment can see which
    distinctions its installed specs actually make -- the buckets above are the fallback for when
    nothing is registered at all."""
    for registered in all_registered():
        for crit in registered.spec.roles:
            if crit.angle_range is not None:
                yield crit.angle_range

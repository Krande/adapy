"""Which registered specs could detail a joint -- the role-binding matcher, in core.

A ``ConnectionSpec`` declares ROLES (incoming / landing) with criteria: a member kind, a set of
section families, an angle range to another role, an optional predicate. Matching is therefore a
small backtracking search over the members that actually meet at a joint, and a spec matches when
every role can be bound at once with all its criteria satisfied.

WHAT THIS PORT DROPPED, AND WHY IT IS NOT A LOSS. The out-of-tree original asked a mesh-clash
store which member was "landed on". Core's ``JointBase`` already answers that -- ``main_mem`` is
the landing member, worked out from the joint's own geometry -- so the matcher needs no contact
geometry at all. That is the whole reason identification can live in core while the mesh clash
with penetration depth and contact patches stays a generator's business (Decision 10, item 5).

CAPABILITY, NEVER PACKAGE. Each match is reported with the capability its spec arrives with, from
the live heartbeat where there is one and from a published components bake otherwise. Core
dispatches a detail job on that capability and never learns which package registered the spec --
the same "capabilities present, never provider id" convention the asset providers use.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from ada.api.connections.spec import (
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    RegisteredConnection,
    all_registered,
)
from ada.clash.result import ApplicableSpec

__all__ = ["applicable_specs", "bindings_for_spec", "detail_pairs"]


def _axis_dir(member):
    """A member's axis, or None for something that has no single axis (a plate)."""
    xvec = getattr(member, "xvec", None)
    return xvec


def _angle_between(a, b) -> float | None:
    """Degrees between two members' axes, or None when one of them has no axis.

    None is a real answer (a plate has no single axis) and is NOT the same as an error: an
    exception here would mean an angle that could not be computed for a member that has one, and
    swallowing that would make every angle-constrained spec silently stop matching -- which is
    exactly what an earlier version of this function did.
    """
    da, db = _axis_dir(a), _axis_dir(b)
    if da is None or db is None:
        return None
    import math

    from ada.core.vector_utils import angle_between

    return math.degrees(angle_between(da, db))


def _angle_ok(binding: Mapping[MemberRole, Any], crit: MemberCriteria) -> bool:
    if not (crit.angle_to_role and crit.angle_range):
        return True
    a = binding.get(crit.role)
    b = binding.get(crit.angle_to_role)
    if a is None or b is None:
        return False
    angle = _angle_between(a, b)
    if angle is None:
        return False
    return crit.angle_range.contains(angle)


def _pred_ok(binding: Mapping[MemberRole, Any], crit: MemberCriteria) -> bool:
    return True if crit.predicate is None else bool(crit.predicate(dict(binding)))


def _role_side_ok(crit: MemberCriteria, member, landing) -> bool:
    """LANDING binds only to the member the joint says is landed on; INCOMING to any other.

    ``landing`` is ``JointBase.main_mem`` where the joint knows one. When it does not (a
    plate-to-plate contact, or a joint built without that determination), the side test is
    skipped rather than guessed -- a spec that genuinely distinguishes the two sides will then
    match both ways round, which the caller sees as two bindings rather than as a wrong one.

    The caller passes ``landing=None`` when the spec describes only PART of the contact: see
    ``bindings_for_spec``.
    """
    if landing is None or MemberKind.from_ada_type(member) is not MemberKind.BEAM:
        return True
    landed = member is landing
    return landed if crit.role == MemberRole.LANDING else not landed


def bindings_for_spec(
    spec: ConnectionSpec,
    members: Sequence[Any],
    *,
    landing: Any | None = None,
) -> list[dict[MemberRole, Any]]:
    """Every way this spec's roles bind to these members, criteria satisfied."""
    role_criteria = spec.roles
    if not role_criteria or len(members) < len(role_criteria):
        return []
    # A spec written for a beam-to-plate joint cannot describe a beam-to-beam one whatever its
    # angles say: checking the kinds up front keeps the search from exploring a shape that can
    # never bind, which is the common case when many specs are registered.
    wanted_kinds = {c.kind for c in role_criteria if c.kind is not None}
    present_kinds = {MemberKind.from_ada_type(m) for m in members}
    if wanted_kinds and not wanted_kinds.issubset(present_kinds):
        return []

    # The joint's own landing member is about the WHOLE contact, so it binds the LANDING role only
    # when the spec is about the whole contact too. Where more members meet than the spec has
    # roles, the spec describes a sub-connection between some of them -- a gusset between two
    # girders that happen to arrive at a column head -- and forcing its LANDING role onto the
    # column means the spec binds nothing at all. That is how every multi-member joint came to
    # offer a generator it could never satisfy: the roles bound the column, and the builder, which
    # wants two girders, refused the joint at BUILD time.
    honour_landing = landing if len(members) == len(role_criteria) else None

    out: list[dict[MemberRole, Any]] = []

    def backtrack(index: int, binding: dict[MemberRole, Any]) -> None:
        if index == len(role_criteria):
            if all(_angle_ok(binding, c) and _pred_ok(binding, c) for c in role_criteria):
                out.append(dict(binding))
            return
        crit = role_criteria[index]
        for candidate in members:
            if any(candidate is bound for bound in binding.values()):
                continue
            if not crit.matches_single(candidate):
                continue
            if not _role_side_ok(crit, candidate, honour_landing):
                continue
            binding[crit.role] = candidate
            if _angle_ok(binding, crit) and _pred_ok(binding, crit):
                backtrack(index + 1, binding)
            binding.pop(crit.role, None)

    backtrack(0, {})
    return out


def applicable_specs(
    members: Sequence[Any],
    *,
    landing: Any | None = None,
    capability_of: Callable[[RegisteredConnection], str | None] | None = None,
    registered: Iterable[RegisteredConnection] | None = None,
) -> tuple[ApplicableSpec, ...]:
    """The specs that could detail this joint, highest priority first.

    ``capability_of`` resolves where a spec would RUN -- injected because that answer comes from
    the live worker heartbeat, which this module must not reach for: it is pure so the same
    matching can be driven in a test with no cluster at all.
    """
    matches: list[ApplicableSpec] = []
    for reg in registered if registered is not None else all_registered():
        if not bindings_for_spec(reg.spec, members, landing=landing):
            continue
        matches.append(
            ApplicableSpec(
                spec=reg.spec.name,
                capability=capability_of(reg) if capability_of else None,
                tags=tuple(sorted(reg.spec.tags or ())),
                priority=int(reg.spec.priority or 0),
            )
        )
    # Priority first, then the more SPECIFIC spec: one that constrains sections or angles is a
    # deliberate narrowing, and offering the general spec above it would bury the answer its
    # author wrote for exactly this joint.
    matches.sort(key=lambda a: (-a.priority, a.spec))
    return tuple(matches)


def detail_pairs(spec: ConnectionSpec, found) -> list[tuple[Any, Any]]:
    """The ``(landing, incoming)`` pairs a spec describes at one contact.

    A builder takes a PAIR -- ``fn(landing=..., incoming=..., centre=..., **config)`` is the shape
    every registered one has -- but a joint is a contact NODE, and three or four members can meet
    at it. Which two of them a given spec is about is the spec's own answer: its role criteria,
    bound by the same search that decided the spec was applicable in the first place. So a
    column head where three girders frame in is three connections of that kind, not one, and not
    a refusal -- which is what requiring the joint itself to have exactly two members produced.

    Pairs are de-duplicated by the members they name, so a spec whose roles can bind the same two
    members either way round still builds once.
    """
    members = list(found.members)
    pairs: list[tuple[Any, Any]] = []
    seen: set[frozenset[int]] = set()
    for binding in bindings_for_spec(spec, members, landing=getattr(found, "landing", None)):
        landing = binding.get(MemberRole.LANDING)
        incoming = binding.get(MemberRole.INCOMING)
        if landing is None or incoming is None or landing is incoming:
            continue
        key = frozenset((id(landing), id(incoming)))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((landing, incoming))
    if pairs:
        return pairs

    # A spec that declares no LANDING/INCOMING roles binds nothing above; a two-member contact
    # still has exactly one sensible pair, which is what this has always meant. A plate-to-plate
    # contact has no landing member (`JointBase` never determines one for it), so the first member
    # stands in -- a spec that cares declares a role pair with a `kind`, and one that matched at
    # all already agreed its roles fit those two members however they are ordered.
    if len(members) == 2:
        landing = getattr(found, "landing", None)
        if landing is None:
            landing = members[0]
        incoming = next((mem for mem in members if mem is not landing), members[-1])
        return [(landing, incoming)]

    raise ValueError(
        f"spec {getattr(spec, 'name', spec)!r} binds no pair of members at a joint of "
        f"{len(members)} members, so there is nothing for it to detail here"
    )

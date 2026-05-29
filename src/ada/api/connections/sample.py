"""Generic spec-driven sample-member synthesis for component previews.

`build_sample(spec, inputs)` fabricates a minimal set of Beams that
satisfy a ConnectionSpec's MemberCriteria. Used by Stage-4 build
orchestration to feed registered connection handlers without requiring
a real model context (clash data, parent assembly, etc.).

Inputs shape mirrors `spec_to_form_schema(spec)`: a per-role dict keyed
by the role's lowercase enum value, with `section` and optionally
`angle_deg`.

    inputs = {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing":  {"section": "BOX200x200x10x10"},
    }
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ada.api.connections.spec import (
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
)

if TYPE_CHECKING:
    from ada import Beam


_SAMPLE_LENGTH_M = 1.0


def build_sample(spec: ConnectionSpec, inputs: dict[str, dict[str, Any]]) -> dict[MemberRole, Beam]:
    """Fabricate one Beam per role in the spec, sharing the origin as joint point.

    The role referenced as an anchor (`angle_to_role` target of any
    other role) runs along the +X axis. Each other role is rotated in
    the XY plane by its `angle_deg` from its `angle_to_role` member.
    """
    from ada import Beam

    anchor_role = _resolve_anchor_role(spec)

    members: dict[MemberRole, Beam] = {}
    for role_crit in spec.roles:
        if role_crit.kind is MemberKind.PLATE:
            raise NotImplementedError(
                f"role {role_crit.role.value!r}: plate sample synthesis not implemented yet"
            )

        role_inputs = _require_role_inputs(inputs, role_crit)
        section = _resolve_section(role_inputs, role_crit)

        if role_crit is _criteria_for(spec, anchor_role):
            # Anchor (the landing member) runs along ±X through the
            # joint point so it presents a continuous side surface for
            # incoming members to butt against. Without this the
            # incoming's end-face lands on a single point (both n2's
            # at origin) and the connection handler has no contact
            # surface to trace welds along.
            n1 = (-_SAMPLE_LENGTH_M, 0.0, 0.0)
            n2 = (_SAMPLE_LENGTH_M, 0.0, 0.0)
        else:
            angle_deg = _resolve_angle_to_anchor(
                spec, inputs, role_crit, anchor_role,
            )
            direction = _direction_in_xy_plane(angle_deg)
            # Incoming starts at the joint point and extends OUTWARD;
            # its n1 (origin) is the contact end that meets the
            # anchor's side surface.
            n1 = (0.0, 0.0, 0.0)
            n2 = tuple(c * _SAMPLE_LENGTH_M for c in direction)
        beam = Beam(
            name=f"sample_{role_crit.role.value}",
            n1=n1,
            n2=n2,
            sec=section,
        )
        if role_crit.section_in is not None and not role_crit.matches_single(beam):
            allowed = sorted(role_crit.section_in)
            raise ValueError(
                f"role {role_crit.role.value!r}: section {section!r} "
                f"(resolved type {beam.section.type.value!r}) "
                f"does not match section_in {allowed}"
            )
        members[role_crit.role] = beam

    return members


def _resolve_anchor_role(spec: ConnectionSpec) -> MemberRole:
    """Pick the role that runs as the through-beam in the sample.

    Prefer ``LANDING`` when the spec uses it — the conventional name
    for the member being landed onto, which needs to extend past the
    joint so the incoming has a contact surface. Else fall back to the
    role that another role references via ``angle_to_role`` (the
    angle-reference direction), then to the first declared role.
    """
    for crit in spec.roles:
        if crit.role is MemberRole.LANDING:
            return crit.role
    referenced = [r.angle_to_role for r in spec.roles if r.angle_to_role is not None]
    if referenced:
        return referenced[0]
    return spec.roles[0].role


def _criteria_for(spec: ConnectionSpec, role: MemberRole) -> MemberCriteria:
    for crit in spec.roles:
        if crit.role is role:
            return crit
    raise ValueError(f"spec {spec.name!r} has no criteria for role {role.value!r}")


def _require_role_inputs(inputs: dict[str, dict[str, Any]], role_crit: MemberCriteria) -> dict[str, Any]:
    key = role_crit.role.value
    role_inputs = inputs.get(key)
    if role_inputs is None:
        raise ValueError(f"role {key!r}: missing inputs entry")
    return role_inputs


def _resolve_section(role_inputs: dict[str, Any], role_crit: MemberCriteria) -> str:
    section = role_inputs.get("section")
    if section is None:
        raise ValueError(f"role {role_crit.role.value!r}: missing 'section' in inputs")
    if not isinstance(section, str):
        raise ValueError(
            f"role {role_crit.role.value!r}: 'section' must be a string, got {type(section).__name__}"
        )
    return section


def _resolve_angle(role_inputs: dict[str, Any], role_crit: MemberCriteria) -> float:
    if role_crit.angle_to_role is None and role_crit.angle_range is None:
        return 0.0
    angle = role_inputs.get("angle_deg")
    if angle is None:
        raise ValueError(f"role {role_crit.role.value!r}: missing 'angle_deg' in inputs")
    if not isinstance(angle, (int, float)):
        raise ValueError(
            f"role {role_crit.role.value!r}: 'angle_deg' must be numeric, got {type(angle).__name__}"
        )
    if role_crit.angle_range is not None and not role_crit.angle_range.contains(float(angle)):
        rng = role_crit.angle_range
        raise ValueError(
            f"role {role_crit.role.value!r}: angle {angle}° outside angle_range "
            f"[{rng.min_deg}°, {rng.max_deg}°]"
        )
    return float(angle)


def _resolve_angle_to_anchor(
    spec: ConnectionSpec,
    inputs: dict[str, dict[str, Any]],
    role_crit: MemberCriteria,
    anchor_role: MemberRole,
) -> float:
    """Angle of ``role_crit`` relative to ``anchor_role``.

    The angle relationship is symmetric: ``A.angle_to_role = B`` with
    ``angle_deg = θ`` means the angle between A and B is θ° regardless
    of which is the "anchor". So when placing a non-anchor role, look
    for an angle reference in either direction:

    * If this role's criteria has ``angle_to_role = anchor`` → use
      this role's ``angle_deg``.
    * If the anchor's criteria has ``angle_to_role = this`` → use the
      anchor's ``angle_deg`` (the input lives in the anchor's inputs
      block, since that's where the criteria with angle_range sits).
    * Otherwise default to 0° (parallel with anchor).
    """
    if role_crit.angle_to_role is anchor_role:
        return _resolve_angle(_require_role_inputs(inputs, role_crit), role_crit)
    anchor_crit = _criteria_for(spec, anchor_role)
    if anchor_crit.angle_to_role is role_crit.role:
        return _resolve_angle(_require_role_inputs(inputs, anchor_crit), anchor_crit)
    return 0.0


def _direction_in_xy_plane(angle_deg: float) -> tuple[float, float, float]:
    rad = math.radians(angle_deg)
    return (math.cos(rad), math.sin(rad), 0.0)

"""Generic spec-driven sample-member synthesis for component previews.

`build_sample(spec, inputs)` fabricates a minimal set of members that
satisfy a ConnectionSpec's MemberCriteria. Used by Stage-4 build
orchestration to feed registered connection handlers without requiring
a real model context (clash data, parent assembly, etc.).

Inputs shape mirrors `spec_to_form_schema(spec)`: a per-role dict keyed
by the role's lowercase enum value. A beam role takes a `section`, a
plate role a `thickness` (and optionally `width` / `height`), and either
takes an `angle_deg` where the role carries an angle criterion.

    inputs = {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing":  {"section": "BOX200x200x10x10"},
    }

`default_inputs(spec)` returns inputs that satisfy the spec: its declared
`defaults` where it has them, else a representative value per field.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ada.api.connections.spec import (
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    role_input_fields,
)

if TYPE_CHECKING:
    from ada import Beam, Plate
    from ada.sections import Section


_SAMPLE_LENGTH_M = 1.0

#: Plate extent when the inputs name only a thickness.
_DEFAULT_PLATE_WIDTH_M = 0.4
_DEFAULT_PLATE_HEIGHT_M = 0.3
_DEFAULT_PLATE_THICKNESS_M = 0.02

#: A representative section per ``section_in`` family, for specs that declare no defaults.
_REPRESENTATIVE_SECTIONS: dict[str, str] = {
    "BOX": "BOX300x300x12x12",
    "TUB": "TUB300x10",
    "TUBULAR": "TUB300x10",
    "I": "IPE300",
    "IPROFILE": "IPE300",
    "IPE": "IPE300",
    "HEA": "HEA300",
    "HEB": "HEB300",
    "UNP": "UNP200",
    "CHANNEL": "UNP200",
    "FB": "FB200x20",
    "FLATBAR": "FB200x20",
}
_FALLBACK_SECTION = "IPE300"


def build_sample(spec: ConnectionSpec, inputs: dict[str, dict[str, Any]]) -> dict[MemberRole, Beam | Plate]:
    """Fabricate one member per role in the spec, sharing the origin as joint point.

    If the spec declared a ``synth_sample`` callable, that takes
    precedence — the default placement below only runs when the spec
    hasn't customised synthesis (stub geometries that need pre-applied
    boolean cuts, etc. provide their own synth via the spec field).

    Default behaviour (no synth_sample):

    * the anchor role (see ``_resolve_anchor_role``) is a beam along X
      through the origin, or — for a plate anchor — a plate in the XY
      plane with its top face at z=0;
    * every other beam starts at the origin and extends at its
      ``angle_deg`` (in the XY plane beside a beam anchor, rising out of
      the XZ plane from a plate anchor);
    * every other plate stands on the anchor's top face, spanning its
      width along the ``angle_deg`` direction (90° — across the anchor —
      when the spec names no angle).
    """
    if spec.synth_sample is not None:
        return spec.synth_sample(spec, inputs)

    anchor_role = _resolve_anchor_role(spec)
    anchor_crit = _criteria_for(spec, anchor_role)
    anchor_inputs = _require_role_inputs(inputs, anchor_crit)

    members: dict[MemberRole, Beam | Plate] = {}
    if anchor_crit.kind is MemberKind.PLATE:
        anchor_is_plate = True
        members[anchor_role] = _sample_plate_anchor(anchor_inputs, anchor_crit)
        base_z = 0.0
    else:
        # Anchor (the landing member) runs along ±X through the joint
        # point so it presents a continuous side surface for incoming
        # members to butt against. Without this the incoming's end-face
        # lands on a single point (both n2's at origin) and the
        # connection handler has no contact surface to trace welds along.
        anchor_is_plate = False
        anchor = _sample_beam(
            anchor_crit,
            _resolve_section(anchor_inputs, anchor_crit),
            n1=(-_SAMPLE_LENGTH_M, 0.0, 0.0),
            n2=(_SAMPLE_LENGTH_M, 0.0, 0.0),
            corners=_corner_radii(anchor_inputs, anchor_crit),
        )
        members[anchor_role] = anchor
        base_z = _half_height(anchor.section)

    for role_crit in spec.roles:
        if role_crit is anchor_crit:
            continue
        role_inputs = _require_role_inputs(inputs, role_crit)
        if role_crit.kind is MemberKind.PLATE:
            angle_deg = _resolve_angle_to_anchor(spec, inputs, role_crit, anchor_role, default=90.0)
            members[role_crit.role] = _sample_plate_on(role_inputs, role_crit, angle_deg, base_z)
            continue

        section = _resolve_section(role_inputs, role_crit)
        if anchor_is_plate:
            angle_deg = _resolve_angle_to_anchor(spec, inputs, role_crit, anchor_role, default=90.0)
            rad = math.radians(angle_deg)
            direction = (math.cos(rad), 0.0, math.sin(rad))
        else:
            angle_deg = _resolve_angle_to_anchor(spec, inputs, role_crit, anchor_role)
            direction = _direction_in_xy_plane(angle_deg)
        # Incoming starts at the joint point and extends OUTWARD; its n1
        # (origin) is the contact end that meets the anchor's surface.
        n2 = tuple(c * _SAMPLE_LENGTH_M for c in direction)
        members[role_crit.role] = _sample_beam(
            role_crit, section, n1=(0.0, 0.0, 0.0), n2=n2, corners=_corner_radii(role_inputs, role_crit)
        )

    return {crit.role: members[crit.role] for crit in spec.roles}


def default_inputs(spec: ConnectionSpec) -> dict[str, dict[str, Any]]:
    """Inputs ``build_sample`` accepts for ``spec``: its declared ``defaults``, with every field
    they leave out filled by a representative value (a section from the role's ``section_in``
    family, a 20 mm plate, 90° or the middle of the role's angle range)."""
    declared = spec.defaults or {}
    out: dict[str, dict[str, Any]] = {}
    for crit in spec.roles:
        key = crit.role.value
        role_inputs = dict(declared.get(key) or {})
        for field in role_input_fields(crit):
            if field in role_inputs:
                continue
            value = _default_field(spec, crit, field)
            if value is not None:
                role_inputs[field] = value
        out[key] = role_inputs
    return out


def _default_field(spec: ConnectionSpec, crit: MemberCriteria, field: str) -> Any:
    if field == "section":
        for family in sorted(crit.section_in or ()):
            section = _REPRESENTATIVE_SECTIONS.get(family.upper())
            if section is not None:
                return section
        return _FALLBACK_SECTION
    if field == "thickness":
        return _DEFAULT_PLATE_THICKNESS_M
    if field == "width":
        return _DEFAULT_PLATE_WIDTH_M
    if field == "height":
        return _DEFAULT_PLATE_HEIGHT_M
    if field == "angle_deg":
        rng = crit.angle_range
        if rng is None or rng.contains(90.0):
            return 90.0
        return (rng.min_deg + rng.max_deg) / 2
    return None


def _sample_beam(
    role_crit: MemberCriteria,
    section: str,
    n1: tuple[float, float, float],
    n2: tuple[float, ...],
    corners: tuple[float | None, float | None] = (None, None),
) -> Beam:
    from ada import Beam

    sec = _rounded_box(section, corners, role_crit) if corners != (None, None) else section
    beam = Beam(name=f"sample_{role_crit.role.value}", n1=n1, n2=n2, sec=sec)
    if role_crit.section_in is not None and not role_crit.matches_single(beam):
        allowed = sorted(role_crit.section_in)
        raise ValueError(
            f"role {role_crit.role.value!r}: section {section!r} "
            f"(resolved type {beam.section.type.value!r}) "
            f"does not match section_in {allowed}"
        )
    return beam


def _sample_plate_anchor(role_inputs: dict[str, Any], role_crit: MemberCriteria) -> Plate:
    """A plate in the XY plane, centred on the origin, its top face at z=0."""
    from ada import Plate

    t, w, h = _plate_dims(role_inputs, role_crit)
    # The outline is the plate's top face: this winding puts its thickness below z=0.
    pts = [(-w / 2, -h / 2, 0.0), (w / 2, -h / 2, 0.0), (w / 2, h / 2, 0.0), (-w / 2, h / 2, 0.0)]
    return Plate.from_3d_points(f"sample_{role_crit.role.value}", pts, t)


def _sample_plate_on(role_inputs: dict[str, Any], role_crit: MemberCriteria, angle_deg: float, base_z: float) -> Plate:
    """A plate standing at ``base_z``: ``width`` along the ``angle_deg`` direction in the XY plane
    (centred on the origin), ``height`` up the Z axis."""
    from ada import Plate

    t, w, h = _plate_dims(role_inputs, role_crit)
    dx, dy, _ = _direction_in_xy_plane(angle_deg)
    pts = [
        (-dx * w / 2, -dy * w / 2, base_z),
        (dx * w / 2, dy * w / 2, base_z),
        (dx * w / 2, dy * w / 2, base_z + h),
        (-dx * w / 2, -dy * w / 2, base_z + h),
    ]
    return Plate.from_3d_points(f"sample_{role_crit.role.value}", pts, t)


def _plate_dims(role_inputs: dict[str, Any], role_crit: MemberCriteria) -> tuple[float, float, float]:
    t = _positive_number(role_inputs, "thickness", role_crit, default=None)
    w = _positive_number(role_inputs, "width", role_crit, default=_DEFAULT_PLATE_WIDTH_M)
    h = _positive_number(role_inputs, "height", role_crit, default=_DEFAULT_PLATE_HEIGHT_M)
    return t, w, h


def _positive_number(role_inputs: dict[str, Any], key: str, role_crit: MemberCriteria, default: float | None) -> float:
    value = role_inputs.get(key, default)
    if value is None:
        raise ValueError(f"role {role_crit.role.value!r}: missing {key!r} in inputs")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"role {role_crit.role.value!r}: {key!r} must be numeric, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"role {role_crit.role.value!r}: {key!r} must be positive, got {value}")
    return float(value)


def _rounded_box(section: str, corners: tuple[float | None, float | None], role_crit: MemberCriteria):
    """A box section of its own, with corner radii: ``r`` outer, ``r_inner`` inner
    (``ada.sections.profiles.box``).

    Its own: a section string resolves to ONE cached Section shared by every beam built from it
    (``interpret_section_str``), so rounding that one would round every such beam in the process.
    """
    from ada.sections import Section
    from ada.sections.categories import BaseTypes
    from ada.sections.string_to_section import interpret_section_str

    outer, inner = corners
    base, _ = interpret_section_str(section)
    if base.type is not BaseTypes.BOX:
        raise ValueError(f"role {role_crit.role.value!r}: corner radii round a box section; {section!r} is not one")
    if outer is None:
        raise ValueError(f"role {role_crit.role.value!r}: an inner corner radius needs an outer one")
    suffix = f"R{outer * 1000:g}" + (f"_Ri{inner * 1000:g}" if inner is not None else "")
    return Section(
        f"{base.name}_{suffix}",
        sec_type=BaseTypes.BOX,
        h=base.h,
        w_top=base.w_top,
        w_btn=base.w_btn,
        t_w=base.t_w,
        t_ftop=base.t_ftop,
        t_fbtn=base.t_fbtn,
        r=outer,
        r_inner=inner,
        units=base.units,
    )


def _corner_radii(role_inputs: dict[str, Any], role_crit: MemberCriteria) -> tuple[float | None, float | None]:
    """A box beam's ``(outer, inner)`` corner radii, where the inputs set them: absent, null or 0 is
    none -- a sharp box, or (inner) the outer radius less the wall."""

    def one(key: str) -> float | None:
        value = role_inputs.get(key)
        if value is None or value == 0:
            return None
        return _positive_number(role_inputs, key, role_crit, default=None)

    return one("corner_radius"), one("corner_radius_inner")


def _half_height(section: Section) -> float:
    """Half the section's depth: where a plate standing on the beam's top face starts."""
    h = getattr(section, "h", None)
    if h:
        return float(h) / 2
    r = getattr(section, "r", None)
    if r:
        return float(r)
    return 0.0


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
        raise ValueError(f"role {role_crit.role.value!r}: 'section' must be a string, got {type(section).__name__}")
    return section


def _resolve_angle(role_inputs: dict[str, Any], role_crit: MemberCriteria) -> float:
    if role_crit.angle_to_role is None and role_crit.angle_range is None:
        return 0.0
    angle = role_inputs.get("angle_deg")
    if angle is None:
        raise ValueError(f"role {role_crit.role.value!r}: missing 'angle_deg' in inputs")
    if not isinstance(angle, (int, float)):
        raise ValueError(f"role {role_crit.role.value!r}: 'angle_deg' must be numeric, got {type(angle).__name__}")
    if role_crit.angle_range is not None and not role_crit.angle_range.contains(float(angle)):
        rng = role_crit.angle_range
        raise ValueError(
            f"role {role_crit.role.value!r}: angle {angle}° outside angle_range " f"[{rng.min_deg}°, {rng.max_deg}°]"
        )
    return float(angle)


def _resolve_angle_to_anchor(
    spec: ConnectionSpec,
    inputs: dict[str, dict[str, Any]],
    role_crit: MemberCriteria,
    anchor_role: MemberRole,
    default: float = 0.0,
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
    * Otherwise ``default`` (0° — parallel with a beam anchor).
    """
    if role_crit.angle_to_role is anchor_role:
        return _resolve_angle(_require_role_inputs(inputs, role_crit), role_crit)
    anchor_crit = _criteria_for(spec, anchor_role)
    if anchor_crit.angle_to_role is role_crit.role:
        return _resolve_angle(_require_role_inputs(inputs, anchor_crit), anchor_crit)
    return default


def _direction_in_xy_plane(angle_deg: float) -> tuple[float, float, float]:
    rad = math.radians(angle_deg)
    return (math.cos(rad), math.sin(rad), 0.0)

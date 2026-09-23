"""What the BUILT-IN detailing engine will detail, declared as ``ConnectionSpec``s.

WHY THESE EXIST. ``applicable[]`` on a joint is "which registered spec could detail this", and
before this module core registered none at all: the built-in engine
(:mod:`ada.topo_model.detailing`) collects its own joints internally and never declared what it
accepts. So a deployment with no plugins identified joints and could offer nothing to do with
them -- the surface worked and was useless, which is the failure mode hardest to notice.

WHAT THEY ARE, AND ARE NOT. Each spec is a DECLARATION of the shape the built-in engine details --
"two I-family girders meeting", "two box members" -- in the same
vocabulary any other spec uses (:class:`MemberCriteria`). They are not a second implementation:
the engine remains the builder, and a `clash_detail` run for one of these names calls it.

THEY CARRY NO CAPABILITY. A built-in spec runs wherever core runs -- the default pool, or
in-process on a queue-less viewer -- so its capability is ``None`` and the panel offers it without
asking a heartbeat. An out-of-tree spec arrives with its own capability and is routed to the pool
advertising it; core never learns which package either came from.
"""

from __future__ import annotations

from ada.api.connections.spec import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    register_connection,
)
from ada.clash.builtin_names import (
    BOX_JOINT_SPEC_NAME,
    BUILTIN_SPEC_NAMES,
    GIRDER_GUSSET_SPEC_NAME,
)

__all__ = [
    "BUILTIN_SPEC_NAMES",
    "BOX_JOINT_SPEC",
    "GIRDER_GUSSET_SPEC",
    "register_builtin_specs",
]

#: Section families, as ``MemberCriteria`` compares them: ``section.type.value.upper()``, i.e. the
#: VALUE of :class:`ada.sections.categories.BaseTypes` (``I``, ``BOX``, ``HP``), never the enum's
#: python name and never the profile designation.
#:
#: HP bulb-flats are deliberately absent from the girder family: ``topo_model.detail_joints``
#: excludes them (they classify as girders by direction but are secondary members), so declaring
#: them here would offer a detail the built-in engine then declines to make.
_I_FAMILY = frozenset({"I", "T"})
#: `Beam.member_type` is core's own axis classification (Column / Girder / Brace) -- see
#: `MemberCriteria.member_types`.
_GIRDER_ONLY = frozenset({"Girder"})
_BOX_FAMILY = frozenset({"BOX"})

GIRDER_GUSSET_SPEC = ConnectionSpec(
    name=GIRDER_GUSSET_SPEC_NAME,
    roles=(
        MemberCriteria(
            role=MemberRole.LANDING,
            kind=MemberKind.BEAM,
            section_in=_I_FAMILY,
            # BOTH members must classify as girders, because `GirderJoint` requires exactly that
            # (`mem_types = ["Girder", "Girder"]`). A column is I-family too, so without this the
            # spec claimed every column head in the frame and the builder refused the joint at
            # BUILD time -- a failed job where the honest answer is "this spec does not fit".
            member_types=_GIRDER_ONLY,
        ),
        MemberCriteria(
            role=MemberRole.INCOMING,
            kind=MemberKind.BEAM,
            section_in=_I_FAMILY,
            member_types=_GIRDER_ONLY,
            angle_to_role=MemberRole.LANDING,
            # Wide on purpose: the built-in pass sizes a gusset from the section, and does not
            # refuse a joint for being a few degrees off square. A narrow range here would make
            # the tab offer nothing for a joint the engine would happily detail.
            angle_range=AngleRange(min_deg=30.0, max_deg=150.0),
        ),
    ),
    tags=frozenset({"builtin", "gusset"}),
    priority=10,
)

BOX_JOINT_SPEC = ConnectionSpec(
    name=BOX_JOINT_SPEC_NAME,
    roles=(
        # `BoxJoint` requires two girders as well (`mem_types = ["Girder", "Girder"]`).
        MemberCriteria(
            role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=_BOX_FAMILY, member_types=_GIRDER_ONLY
        ),
        MemberCriteria(
            role=MemberRole.INCOMING, kind=MemberKind.BEAM, section_in=_BOX_FAMILY, member_types=_GIRDER_ONLY
        ),
    ),
    tags=frozenset({"builtin", "box"}),
    priority=10,
)

# NO BASE-PLATE SPEC, deliberately. The built-in engine details base plates, but a base plate is a
# column meeting its SUPPORT -- and the support is not a member, so no clash pass finds it. A
# declaration for it here would bind to its one role on every joint that contains any beam, and
# the tab would offer a base plate on every girder crossing in the model. Measured on the steel
# demo before it was removed: 84 joints out of 84. The built-in engine still collects base plates
# its own way (`collect_base_plate_joints`); they are simply not a clash-check answer.


# --- the builders these declarations stand for ----------------------------------------------------
#
# Each spec is registered WITH the built-in joint class that details it, so a `clash_detail` run
# for one of these names has something to call. The classes already build exactly one joint from
# its own members (`GirderJoint`, `BoxJoint`, `BasePlateJoint` all take `(name, members, centre)`
# and expose the resulting `Connection`), which is why this is a declaration and not a second
# implementation.


def _accepted(joint_cls, config: dict) -> dict:
    """The subset of ``config`` the joint class actually takes.

    Filtered by SIGNATURE rather than by naming the keys here, for two reasons. The joint class
    owns which knobs it has -- a UI already sends them by those names
    (``topo_model.detailing_catalog``), so restating them in this module would be a second list to
    keep in step, and the way that drifts is silently: a renamed key stops reaching the builder
    and the geometry simply stops changing. It also keeps this package clear of fabrication
    vocabulary, which the CI gate greps for and which those key names contain.
    """
    import inspect

    try:
        accepted = inspect.signature(joint_cls).parameters
    except (TypeError, ValueError):  # pragma: no cover - a class without an inspectable signature
        return {}
    return {k: v for k, v in config.items() if k in accepted and v is not None}


def _build_girder_gusset(*, landing, incoming, centre, name: str | None = None, **config):
    from ada.topo_model.detail_joints import GirderJoint

    joint = GirderJoint(
        name or f"gusset_{getattr(landing, 'name', 'a')}_{getattr(incoming, 'name', 'b')}",
        [landing, incoming],
        centre,
        **_accepted(GirderJoint, config),
    )
    return joint.connection


def _build_box_joint(*, landing, incoming, centre, name: str | None = None, **config):
    from ada.topo_model.detailing import BoxJoint

    # `incoming` / `landing` are REQUIRED keyword arguments of this joint (it cuts the one with
    # the other), separate from the member list every JointBase takes. Passing only the list
    # raised TypeError the first time a box joint was actually offered.
    joint = BoxJoint(
        name or f"box_{getattr(landing, 'name', 'a')}_{getattr(incoming, 'name', 'b')}",
        [landing, incoming],
        centre,
        incoming=incoming,
        landing=landing,
        **_accepted(BoxJoint, config),
    )
    return getattr(joint, "connection", joint)


_BUILTIN = (
    (GIRDER_GUSSET_SPEC, _build_girder_gusset),
    (BOX_JOINT_SPEC, _build_box_joint),
)


def register_builtin_specs() -> None:
    """Register the built-in declarations with the builders they stand for.

    Tolerant of an already-registered name rather than idempotent by a module flag: the spec
    registry is process-global and tests clear it (`_clear_registry`), so a flag would leave a
    cleared registry permanently empty. `register_connection` is a DECORATOR FACTORY -- calling it
    without applying the result registers NOTHING, silently, which is how an earlier version of
    this module registered nothing at all while looking like it did.
    """
    from ada.api.connections.spec import _REGISTRY

    for spec, fn in _BUILTIN:
        if spec.name in _REGISTRY:
            continue
        register_connection(spec)(fn)

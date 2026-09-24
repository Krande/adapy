"""Find the joints in a model, group them by type, and say who could detail each group.

INPUT IS THE MODEL'S SOURCE, NEVER THE GLB. The GLB is triangles: it has no members, no sections
and no axes, and every primitive below takes a ``Beam`` or a ``Plate``. So a check runs on the
source the scene was loaded from -- an IFC file, a Sesam/Genie XML, a FEM, a compiled procedural
model -- and a source whose reader yields only shapes is answered with ``counts.members == 0`` and
a sentence, rather than with a mesh pass core does not have.

THREE PASSES, ONE JOINT PER CONTACT NODE:

* beam-to-beam -- ``Connections.find``, which already folds the pairwise clash results into one
  joint per node (its ``nmap``). Core does not re-do that grouping.
* plate-to-beam -- ``find_beams_connected_to_plate`` per plate.
* plate-to-plate -- ``find_edge_connected_perpendicular_plates``.

The passes are kept separate because their primitives are, and because a deployment without an
OCC-capable backend can still run the first: the plate passes reach the CAD backend for distances,
the beam pass does not. A pass that cannot run is reported in ``warnings`` and leaves its joints
uncounted rather than silently contributing none -- "no plate joints" and "plate joints were never
looked for" are different answers, and only one of them is a reason to trust the number.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ada.api.connections.spec import RegisteredConnection
from ada.clash import native_joints
from ada.clash.classify import describe_member, type_key_for, type_label_for
from ada.clash.match import _angle_between, applicable_specs
from ada.clash.options import ClashOptions
from ada.clash.result import ClashResult, JointRecord, group_joints
from ada.config import logger

__all__ = ["ClashOptions", "identify_joints", "run_clash_check"]


@dataclass
class _Found:
    """One contact, before it is classified."""

    members: list[Any]
    centre: tuple[float, float, float]
    landing: Any | None = None
    origin: str = "beam-beam"


@dataclass
class IdentifyOutcome:
    joints: list[_Found] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _part_for(model, root: str | None):
    """The subtree a check is scoped to, or the whole model."""
    if not root:
        return model
    for part in model.get_all_subparts(include_self=True):
        if part.name == root:
            return part
    raise ValueError(f"no part named {root!r} in this model to scope the check to")


def _centre_of(node) -> tuple[float, float, float]:
    point = getattr(node, "p", node)
    return (float(point[0]), float(point[1]), float(point[2]))


def identify_joints(model, options: ClashOptions | None = None) -> IdentifyOutcome:
    """Run the three passes over ``model`` and return the raw contacts."""
    from ada.api.containers.connections import Connections

    options = options or ClashOptions()
    outcome = IdentifyOutcome()
    part = _part_for(model, options.root)

    beams = list(part.get_all_physical_objects(by_type=_beam_type()))
    plates = list(part.get_all_physical_objects(by_type=_plate_type()))
    outcome.counts["members"] = len(beams) + len(plates)
    outcome.counts["beams"] = len(beams)
    outcome.counts["plates"] = len(plates)

    if not beams and not plates:
        # Not a failure: a STEP or a shape-only IFC read yields no members at all, and the honest
        # answer is that this source cannot be checked -- not that it has no joints.
        outcome.warnings.append(
            "this source yielded no beams or plates, so there is nothing to find joints between. "
            "A clash check runs on a source adapy reads into members; a shapes-only read has none."
        )
        return outcome

    if beams and native_joints.available():
        # The compiled pass (adacpp), which is also the one the BROWSER runs -- see
        # `clash/native_joints.py` for why that matters and why this is not merely an optimisation.
        try:
            found = native_joints.find_beam_joints(beams, options.out_of_plane_tol, options.point_tol)
        except Exception as exc:  # noqa: BLE001 - a pass that cannot run must say so, not vanish
            logger.exception("clash: the native beam-to-beam pass failed")
            outcome.warnings.append(f"beam-to-beam pass failed: {exc}")
        else:
            for joint in found:
                members = [beams[i] for i in joint["members"]]
                if len(members) < 2:
                    continue
                outcome.joints.append(
                    _Found(
                        members=members,
                        centre=tuple(float(v) for v in joint["centre"]),
                        # `main_mem` is the Python pass's notion of which member the others land
                        # ON. The compiled pass does not rank them, and a spec that needs a landing
                        # resolves one itself (`match.bindings_for_spec`), so this stays None
                        # rather than guessing at an order the geometry does not state.
                        landing=None,
                        origin="beam-beam",
                    )
                )
    elif beams:
        connections = Connections(parent=part)
        try:
            connections.find(out_of_plane_tol=options.out_of_plane_tol, point_tol=options.point_tol)
        except Exception as exc:  # noqa: BLE001 - a pass that cannot run must say so, not vanish
            logger.exception("clash: the beam-to-beam pass failed")
            outcome.warnings.append(f"beam-to-beam pass failed: {exc}")
        else:
            for joint in connections:
                # `beams`, not `members`: JointBase names its own accessor after what a
                # beam-to-beam joint holds.
                members = list(joint.beams)
                if len(members) < 2:
                    continue
                outcome.joints.append(
                    _Found(
                        members=members,
                        centre=_centre_of(joint.centre),
                        landing=getattr(joint, "main_mem", None),
                        origin="beam-beam",
                    )
                )

    if options.include_plate_joints and plates:
        _plate_passes(part, beams, plates, outcome)

    return outcome


def _beam_type():
    from ada import Beam

    return Beam


def _plate_type():
    from ada import Plate

    return Plate


def _plate_passes(part, beams, plates, outcome: IdentifyOutcome) -> None:
    """The two plate passes. Kept apart from the beam pass because they need the CAD backend and
    it does not -- a deployment without one still gets beam joints rather than nothing."""
    from ada.core.clash_check import find_beams_connected_to_plate

    try:
        for plate in plates:
            attached = find_beams_connected_to_plate(plate, beams)
            for beam in attached:
                outcome.joints.append(
                    _Found(
                        members=[plate, beam],
                        centre=_centre_of(plate.poly.origin),
                        landing=beam,
                        origin="plate-beam",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("clash: the plate-to-beam pass failed")
        outcome.warnings.append(f"plate-to-beam pass failed: {exc}")

    try:
        from ada.core.clash_check import find_edge_connected_perpendicular_plates

        # PlateConnections has no `.edges` -- it carries two dicts, `edge_connected` and
        # `mid_span_connected`, each `Plate -> [Plate, ...]` and each recorded from BOTH sides
        # of a pair (`test_plate_checking.py` pins this). Dedup by the unordered pair, or every
        # touching pair would be reported twice.
        connections = find_edge_connected_perpendicular_plates(plates)
        seen: set[frozenset] = set()
        for mapping in (connections.edge_connected, connections.mid_span_connected):
            for pl1, others in mapping.items():
                for pl2 in others:
                    key = frozenset((pl1.guid, pl2.guid))
                    if key in seen:
                        continue
                    seen.add(key)
                    outcome.joints.append(
                        _Found(
                            members=[pl1, pl2],
                            centre=_centre_of(pl1.poly.origin),
                            landing=None,
                            origin="plate-plate",
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        logger.exception("clash: the plate-to-plate pass failed")
        outcome.warnings.append(f"plate-to-plate pass failed: {exc}")


def run_clash_check(
    model,
    *,
    source_key: str,
    options: ClashOptions | None = None,
    capability_of: Callable[[RegisteredConnection], str | None] | None = None,
    source_sha256: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> ClashResult:
    """Identify, classify, match and group -- the whole check, as one document.

    The joint ids are deterministic for a given (source, options): a hash of the contact's member
    names and its origin pass. That is what lets a ``clash_detail`` job re-derive exactly the
    joints a user selected from a CACHED result, without the ids having to be stored anywhere
    between the two calls.
    """
    options = options or ClashOptions()
    outcome = identify_joints(model, options)

    joints: list[JointRecord] = []
    for found in outcome.joints:
        described = [describe_member(m) for m in found.members]
        angle = _angle_between(found.members[0], found.members[1]) if len(found.members) >= 2 else None
        ident = hashlib.sha256(
            "|".join([found.origin, *sorted(m.name for m in described)]).encode("utf-8")
        ).hexdigest()[:12]
        joints.append(
            JointRecord(
                id=ident,
                centre=found.centre,
                members=tuple(described),
                type_key=type_key_for(described, angle),
                type_label=type_label_for(described, angle),
                applicable=applicable_specs(found.members, landing=found.landing, capability_of=capability_of),
            )
        )

    counts = dict(outcome.counts)
    counts["joints"] = len(joints)
    matched = sum(1 for j in joints if j.applicable)
    counts["joints_with_a_generator"] = matched
    # NOT zero-filled: a pass that did not run leaves its count absent, so "no plate joints" and
    # "plate joints were never looked for" stay different answers (see the module docstring).
    return ClashResult(
        source_key=source_key,
        source_sha256=source_sha256,
        options=options.to_dict(),
        joints=tuple(joints),
        groups=group_joints(joints),
        counts=counts,
        provenance=dict(provenance or {}),
        warnings=tuple(outcome.warnings),
    )

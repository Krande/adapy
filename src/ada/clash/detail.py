"""Re-find named joints in a reloaded model and build them with a registered spec -- the one loop
both ``clash_detail`` engines run (the worker's ``formats/clash_detail.py`` and the queue-less
``local_jobs.start_clash_detail``).

It used to be written out in each of them. The two copies had to agree on every argument a builder
receives -- the same job kind must not mean two different things depending on where it ran -- and
the only way that stays true is for there to be one copy. How the model is REOPENED is what differs
between a file, a published node and a group, and that stays with the caller.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

__all__ = ["build_detail", "found_id", "joints_by_id"]


def found_id(found) -> str:
    """The exact formula ``run_clash_check`` stamps a joint's id with -- a documented, deterministic
    PUBLIC contract (``ada.clash.identify.run_clash_check``'s own docstring), re-derived here so a
    cached id can be matched back up without ``identify`` exposing an id-taking entrypoint."""
    from ada.clash.classify import describe_member

    names = sorted(describe_member(m).name for m in found.members)
    return hashlib.sha256("|".join([found.origin, *names]).encode("utf-8")).hexdigest()[:12]


def joints_by_id(model, options) -> dict[str, Any]:
    """Identify ``model`` at ``options`` and key the raw contacts by the id the check gave them."""
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.identify import identify_joints

    register_builtin_specs()
    return {found_id(f): f for f in identify_joints(model, options).joints}


def build_detail(
    registered,
    *,
    spec_name: str,
    joint_ids: list[str],
    by_id: Mapping[str, Any],
    gen_options: Mapping[str, Any],
) -> tuple[bytes, dict]:
    """Build every named joint with ``registered`` and return ``(glb_bytes, stats)``.

    Every id must be in ``by_id`` -- the caller has already refused the ones that were not
    reproducible, with the message only it can word. Raises ``ValueError`` when nothing at all was
    built; a joint the builder refuses is reported in ``stats["skipped"]`` and the rest go on.
    """
    from ada import Part
    from ada.core.file_system import new_temp_path
    from ada.topo_model.takeoff import _joints_takeoff

    joints_part = Part("Joints")
    skipped: list[str] = []
    built_by: dict[str, int] = {}
    for jid in joint_ids:
        found = by_id[jid]
        # The requested spec first; when its builder DECLINES (`NotApplicable`: "not mine"), the
        # joint's other applicable specs from the same provider, by priority -- the fall-through a
        # provider's own design loop does. Without it the highest-priority spec, which is the one
        # the panel batches a joint under, refused every joint it did not specialise in and the
        # run built nothing (a stub spec declining every plain joint, its plain sibling never tried).
        declined: list[str] = []
        for reg in [registered, *_fallbacks(registered, found)]:
            name = reg.spec.name
            outcome = _build_with(reg, name, jid, found, gen_options)
            if isinstance(outcome, _Declined):
                declined.append(f"{name}: {outcome.reason}")
                continue
            if isinstance(outcome, str):
                # A real failure (not a decline) of THIS spec: reported, and not passed on -- a
                # builder that crashed is not saying the joint belongs to another spec.
                skipped.append(f"{jid}: {outcome}")
                break
            for conn in outcome:
                joints_part.add_part(conn)
            built_by[name] = built_by.get(name, 0) + 1
            break
        else:
            skipped.append(f"{jid}: no applicable spec took it (" + "; ".join(declined) + ")")
    if not joints_part.parts and skipped:
        raise ValueError(
            f"{spec_name} detailed none of the {len(joint_ids)} joint(s) handed to it: " + "; ".join(skipped[:5])
        )

    glb_path = new_temp_path(suffix=".glb")
    try:
        joints_part.to_gltf(glb_path)
        glb_bytes = glb_path.read_bytes()
    finally:
        glb_path.unlink(missing_ok=True)

    stats: dict[str, Any] = {"joints": _joints_takeoff(joints_part)}
    # Which spec built how many joints: with fall-through, not every joint handed to `spec_name`
    # was built by it, and a take-off naming only the requested spec would misreport the rest.
    stats["built_by"] = built_by
    if skipped:
        stats["skipped"] = skipped
    return glb_bytes, stats


class _Declined:
    """A builder's `NotApplicable`: the joint is not this spec's, try the next one."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _is_decline(exc: BaseException) -> bool:
    """Core's `NotApplicable`, or a provider's own exception of that NAME on an older core."""
    from ada.api.connections.spec import NotApplicable

    return isinstance(exc, NotApplicable) or any(c.__name__ == "NotApplicable" for c in type(exc).__mro__)


def _build_with(reg, spec_name: str, jid: str, found, gen_options) -> "list[Any] | _Declined | str":
    """Every connection ``reg`` builds at this joint, a decline, or a failure sentence."""
    from ada.clash.match import detail_pairs

    # One connection per way the spec's roles bind at this contact -- a joint is a contact NODE and
    # three or four members can meet at one, while a builder takes a pair. See
    # `ada.clash.match.detail_pairs`: requiring the joint itself to have exactly two members refused
    # every column head, which is the first thing a "detail everything" run hits.
    try:
        pairs = detail_pairs(reg.spec, found)
    except ValueError as exc:
        # The spec's roles do not bind here: as good as a decline for falling through.
        return _Declined(str(exc))
    built = []
    for i, (landing, incoming) in enumerate(pairs):
        try:
            conn = reg.fn(
                landing=landing,
                incoming=incoming,
                centre=found.centre,
                # What the PASS measured at this contact, where it measured anything: normal,
                # penetration depth, nearest points, patch area. A pass that works on axes has none
                # and this is None, which every builder tolerates -- but a builder given a geometric
                # contact can size its output from the real overlap instead of inferring one.
                clash=found.contact,
                name=f"{spec_name}_{jid}" if i == 0 else f"{spec_name}_{jid}_{i}",
                **gen_options,
            )
        except Exception as exc:  # noqa: BLE001 - one joint's refusal is not the run's
            if _is_decline(exc):
                return _Declined(str(exc))
            # A builder's own prerequisites can be finer than a spec's criteria express; the other
            # joints in the batch are still worth building. Only a run where NOTHING built fails.
            return f"{type(exc).__name__}: {exc}"
        built.append(conn)
    return built


def _fallbacks(registered, found) -> list:
    """The joint's OTHER applicable specs from the same provider (the same capability), highest
    priority first -- what a decline falls through to.

    Same provider only: a provider's specs are written to fall through to one another, while a
    different provider's spec (a built-in among them) is a different detailing decision, made by
    choosing that spec, not by one provider's builder saying "not mine"."""
    from ada.api.connections.spec import all_registered
    from ada.clash.match import detail_pairs

    cap = getattr(registered.spec, "capability", None)
    out = []
    for reg in all_registered():
        if reg.spec.name == registered.spec.name or getattr(reg.spec, "capability", None) != cap:
            continue
        # Qualified exactly as the requested spec is -- by whether its roles bind at this contact
        # (`detail_pairs`) -- so a fall-through candidate is one the build would accept.
        try:
            if detail_pairs(reg.spec, found):
                out.append(reg)
        except ValueError:
            continue
    out.sort(key=lambda r: (-(r.spec.priority or 0), r.spec.name))
    return out

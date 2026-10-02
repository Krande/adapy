"""Which searches a clash check runs, and who may add one.

A CHECK IS NOT ONE ALGORITHM. Members meeting at a shared node and members whose solids overlap
by two millimetres are different questions, answered by different machinery, and wanted by
different people. Core's own passes look at axes and at surface distances, which is the right
answer for a model whose members meet exactly -- an analysis model, a frame built from a grid. A
detail model, where geometry is modelled as fabricated and parts merely touch, needs a mesh-level
interference test with penetration depth and a contact patch, and that machinery is heavy enough
that it does not belong in core and specific enough that it should not be core's opinion.

So the passes are a REGISTRY rather than a list. Core registers its own; anything else registers
its own against a CAPABILITY, the same way a connection spec does -- core never learns which
package contributed it, only which pool can run it. A check then runs the passes it was asked
for, reports per pass what ran and what did not, and stamps every joint with the pass that found
it, so a result can be read -- and filtered -- by where its joints came from.

WHY THE STAMP MATTERS. Two passes over one model will find overlapping but different joints: the
axis pass finds a node three beams share, the mesh pass finds the two millimetres by which a
bracket overlaps a flange. Both are true. A reader who cannot tell which pass produced a joint
cannot judge it, and cannot turn one off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from ada.config import logger

__all__ = [
    "BUILTIN_CHECKER",
    "ClashChecker",
    "ClashPass",
    "all_checkers",
    "all_passes",
    "get_checker",
    "get_pass",
    "list_checkers",
    "list_passes",
    "passes_for",
    "register_checker",
    "register_pass",
    "selected_passes",
]

#: Core's own checker. Its passes are whichever registered passes run wherever core runs.
from ada.clash.builtin_names import BUILTIN_CHECKER  # noqa: E402 - re-exported


@dataclass(frozen=True)
class ClashPass:
    """One search a check may run."""

    #: Stable id. It is also the joint's ``origin``, which the joint id is hashed from, so
    #: renaming a pass renames its joints -- treat it as a wire constant, not a label.
    name: str
    #: For a person: what this pass looks for.
    label: str
    #: The pool that can run it, or None for a pass core runs wherever core runs. Core never
    #: learns which package registered a capability-bearing pass -- only which pool has it, the
    #: same convention the connection-spec registry follows.
    capability: str | None = None
    #: Whether the pass needs a CAD backend. A deployment without one still runs the passes that
    #: do not, and is told which it skipped rather than quietly getting fewer joints.
    needs_backend: bool = False
    #: ``fn(part, options) -> Iterable[_Found]``. Returning contacts rather than joints: the
    #: classify/match/group half is core's and is the same for every pass, which is the whole
    #: point of contributing a pass instead of a pipeline.
    fn: Callable[..., Iterable[Any]] | None = None
    #: Ordering in the panel and in the run. Lower runs first.
    priority: int = 0


_REGISTRY: dict[str, ClashPass] = {}


def register_pass(clash_pass: ClashPass) -> ClashPass:
    """Add a pass. Re-registering a name REPLACES it, so a plugin may override a core pass."""
    if not clash_pass.name:
        raise ValueError("a clash pass needs a name; it is the joint's origin and part of its id")
    if clash_pass.name in _REGISTRY and _REGISTRY[clash_pass.name] is not clash_pass:
        logger.info(f"clash: pass {clash_pass.name!r} re-registered, replacing the previous one")
    _REGISTRY[clash_pass.name] = clash_pass
    return clash_pass


def get_pass(name: str) -> ClashPass | None:
    return _REGISTRY.get(name)


def all_passes() -> tuple[ClashPass, ...]:
    """Every registered pass, in run order."""
    return tuple(sorted(_REGISTRY.values(), key=lambda p: (p.priority, p.name)))


def list_passes() -> list[dict]:
    """The registry as plain data, for a route to echo and a panel to offer as checkboxes."""
    return [
        {
            "name": p.name,
            "label": p.label,
            "capability": p.capability,
            "needs_backend": p.needs_backend,
            "priority": p.priority,
        }
        for p in all_passes()
    ]


def selected_passes(names: Iterable[str] | None) -> tuple[ClashPass, ...]:
    """The passes a check was asked for.

    ``None`` means every pass core can run BY ITSELF -- the capability-bearing ones are opt-in,
    because a pass routed to a pool that is not there would otherwise make every default check
    report a failure for something nobody asked for.
    """
    if names is None:
        return tuple(p for p in all_passes() if p.capability is None)
    wanted = {str(n) for n in names}
    return tuple(p for p in all_passes() if p.name in wanted)


# ── checkers ─────────────────────────────────────────────────────────────────────────────────
#
# A CHECKER IS AN ENGINE, A PASS IS ONE SEARCH IT RUNS. Core's checker is its three axis/distance
# passes; a contributed one -- a mesh-interference engine, say -- brings its own pass and its own
# settings. A check runs ONE checker: two engines over one model find the same contacts by
# different means, and running both would report every joint twice under two origins.
#
# The checker is also what a check is ROUTED by. Core's runs wherever core runs; a contributed one
# names the pool that has its code, exactly as a pass or a connection spec does, and core never
# learns which package that was.


@dataclass(frozen=True)
class ClashChecker:
    """A selectable clash-check engine."""

    #: Stable id, sent as ``ClashOptions.checker``. A wire constant, like a pass name.
    name: str
    label: str
    #: The passes this checker runs, by name. Empty for core's checker, whose passes are resolved
    #: live (every pass that needs no pool) so that a core pass added later joins it unasked.
    passes: tuple[str, ...] = ()
    #: The pool that runs it. ``None`` means wherever core runs.
    capability: str | None = None
    description: str = ""
    #: The checker's own settings as form fields: ``{key, label, type, default, min?, max?,
    #: unit?, help?}`` with ``type`` one of ``number | integer | boolean | string``. The panel draws
    #: these and sends the values back as ``ClashOptions.checker_options``; core reads none of them.
    options: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    #: Whether core's own tolerances (out-of-plane, point, plate joints) mean anything to it.
    uses_core_options: bool = False
    priority: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "capability": self.capability,
            "passes": [p.name for p in passes_for(self.name)],
            "options": [dict(o) for o in self.options],
            "uses_core_options": self.uses_core_options,
            "priority": self.priority,
        }


_CHECKERS: dict[str, ClashChecker] = {}


def register_checker(checker: ClashChecker) -> ClashChecker:
    """Add a checker. Re-registering a name replaces it, as for passes."""
    if not checker.name:
        raise ValueError("a clash checker needs a name; it is what a check request selects it by")
    if checker.name in _CHECKERS and _CHECKERS[checker.name] is not checker:
        logger.info(f"clash: checker {checker.name!r} re-registered, replacing the previous one")
    _CHECKERS[checker.name] = checker
    return checker


def get_checker(name: str | None) -> ClashChecker | None:
    return _CHECKERS.get(name or BUILTIN_CHECKER)


def all_checkers() -> tuple[ClashChecker, ...]:
    return tuple(sorted(_CHECKERS.values(), key=lambda c: (c.priority, c.name)))


def list_checkers() -> list[dict]:
    return [c.to_dict() for c in all_checkers()]


def passes_for(checker_name: str | None) -> tuple[ClashPass, ...]:
    """The passes a checker owns, in run order. Unknown checker -> none."""
    checker = get_checker(checker_name)
    if checker is None:
        return ()
    if checker.name == BUILTIN_CHECKER and not checker.passes:
        return tuple(p for p in all_passes() if p.capability is None)
    wanted = set(checker.passes)
    return tuple(p for p in all_passes() if p.name in wanted)


register_checker(
    ClashChecker(
        name=BUILTIN_CHECKER,
        label="adapy (built-in)",
        description="Members meeting at shared nodes, beams landing on plates, and plates meeting edge-on.",
        uses_core_options=True,
        priority=0,
    )
)

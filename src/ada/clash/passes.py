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

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ada.config import logger

__all__ = [
    "ClashPass",
    "all_passes",
    "get_pass",
    "list_passes",
    "register_pass",
    "selected_passes",
]


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

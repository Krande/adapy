"""The options a clash check takes -- kept in a module of its own, and stdlib-only.

WHY IT IS NOT IN ``identify``. The REST route reads and echoes these before any check runs, and
the API that serves that route is the SLIM runtime: it has no numpy, no CAD backend and no
reader. ``identify`` imports ``ada.api`` (beams, plates, containers) the moment it is loaded, so a
route importing ClashOptions from there would drag the whole modelling stack into an image built
not to have it -- which does not fail at build time, it crashloops the API on start.

So the options live here, the reasoning lives in ``identify``, and each side imports only what it
can actually carry.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ClashOptions"]


@dataclass(frozen=True)
class ClashOptions:
    """Core's options, and only core's: tolerances and a subtree scope.

    No provider option rides in this document. ``root`` exists because
    ``Part.beam_clash_check`` is one bbox query per beam over every subpart, so a plant-scale
    source wants to be asked about one area at a time -- the wall time of a whole-file run is
    §Still open in the design note, and this is the lever that answer will be measured with.
    """

    out_of_plane_tol: float = 0.1
    point_tol: float = 1e-5
    root: str | None = None
    include_plate_joints: bool = True
    #: Which registered passes to run, by name (``ada.clash.passes``). ``None`` means every pass
    #: core can run by itself -- a capability-bearing pass, contributed by a plugin, is opt-in,
    #: because a pass routed to a pool that is not there would make every default check report a
    #: failure nobody asked for. Still core's own vocabulary: these are pass NAMES, never a
    #: provider id, and a name core does not know is reported as unavailable rather than obeyed.
    passes: tuple[str, ...] | None = None

    def to_dict(self) -> dict:
        out = {
            "out_of_plane_tol": self.out_of_plane_tol,
            "point_tol": self.point_tol,
            "root": self.root,
            "include_plate_joints": self.include_plate_joints,
        }
        if self.passes is not None:
            out["passes"] = list(self.passes)
        return out

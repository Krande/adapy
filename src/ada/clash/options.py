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

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["ClashOptions"]


@dataclass(frozen=True)
class ClashOptions:
    """Core's options, and only core's: tolerances and a subtree scope.

    No provider's OWN option rides in this document -- ``geometry_provider`` names a provider id
    as data, the way a manifest does, and core never branches on which one. ``root`` exists because
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
    #: Which registered CHECKER runs the check (``ada.clash.passes.ClashChecker``). ``None`` is
    #: core's own. A checker owns a set of passes and a pool, so naming one picks both; an explicit
    #: ``passes`` still narrows within it.
    checker: str | None = None
    #: The checker's own settings, as its advertised form describes them (security margin, minimum
    #: contact area, ...). Opaque to core: carried to the checker's passes and folded into the
    #: derived key, never interpreted.
    checker_options: Mapping[str, Any] = field(default_factory=dict)
    #: Which asset PROVIDER the members' geometry is read from (``ada.clash.geometry_source``).
    #: ``None`` reads every published node through the provider that published it. A provider id
    #: names a provider that can read its nodes into objects; a node published by another provider
    #: in the same collection is matched to this one's node by its NAME before anything is read.
    #: Part of the options -- not of the target -- because it changes what was checked, so it has
    #: to move the derived key and reach a detail job that rebuilds the model.
    geometry_provider: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "out_of_plane_tol": self.out_of_plane_tol,
            "point_tol": self.point_tol,
            "root": self.root,
            "include_plate_joints": self.include_plate_joints,
        }
        # Each written only when set, so a default check keeps the options token -- and with it
        # the derived key of every result cached before these fields existed.
        if self.passes is not None:
            out["passes"] = list(self.passes)
        if self.checker is not None:
            out["checker"] = self.checker
        if self.checker_options:
            out["checker_options"] = dict(self.checker_options)
        if self.geometry_provider is not None:
            out["geometry_provider"] = self.geometry_provider
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ClashOptions":
        """The inverse of :meth:`to_dict`, and the ONE place a request's options are read.

        Every route, job handler and queue-less engine reads options through this, so a field added
        here reaches all of them -- the alternative, a hand-written constructor per call site, is
        how ``passes`` was once dropped by all five of them at once.
        """
        raw = raw or {}
        if not isinstance(raw, Mapping):
            raise TypeError(f"clash options must be an object, got {type(raw).__name__}")
        passes = raw.get("passes")
        if passes is not None:
            if isinstance(passes, (str, bytes)) or not isinstance(passes, (list, tuple)):
                raise TypeError("'passes' must be a list of pass names")
            passes = tuple(str(p) for p in passes)
        checker_options = raw.get("checker_options") or {}
        if not isinstance(checker_options, Mapping):
            raise TypeError("'checker_options' must be an object")
        geometry_provider = raw.get("geometry_provider")
        if geometry_provider is not None and not isinstance(geometry_provider, str):
            raise TypeError("'geometry_provider' must be a provider id")
        return cls(
            out_of_plane_tol=float(raw.get("out_of_plane_tol", 0.1)),
            point_tol=float(raw.get("point_tol", 1e-5)),
            root=raw.get("root") or None,
            include_plate_joints=bool(raw.get("include_plate_joints", True)),
            passes=passes,
            checker=str(raw["checker"]) if raw.get("checker") else None,
            checker_options=dict(checker_options),
            # Blank is "each member's own", the same as absent -- so a cleared field cannot split one
            # check into two cache entries.
            geometry_provider=(geometry_provider or "").strip() or None,
        )

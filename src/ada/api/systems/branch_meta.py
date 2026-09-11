"""The ``metadata`` a branched :class:`~.base.System` carries, and who writes which half.

Two producers put branch data on a system's metadata, and they must not share a dict:

* The IMPORT side -- ``ada.cadit.dexpi.read.to_procedural._fold_branch_groups`` -- writes
  ``metadata[BRANCH_KEY]`` (``METADATA["branch"]`` on the procedural spec) with the source
  junction and its legs. ``ada.topo_model.compile._wire_systems`` reads it back to rebuild the
  legs as :class:`~.segments.SystemSegment` rows, and the DEXPI writer reads it to split the
  branched system back into the segments the source had.
* The ROUTING side -- ``ada.topology.routing.route_branched_system`` -- writes
  ``metadata[BRANCH_ROUTE_KEY]`` with what the router decided: which two legs form the trunk and
  where the junction hub sits. It is an output of routing, not an input, so it lives under its own
  key rather than being mixed into the import-side dict.
"""

from __future__ import annotations

from typing import Final

#: ``metadata`` key of the import-side branch description.
BRANCH_KEY: Final = "branch"
#: Source id of the junction item the legs meet at (import side).
BRANCH_JUNCTION_ID: Final = "junction_id"
#: The ORIGINAL per-segment name of every leg, in ``CONNECTIONS`` order (import side).
BRANCH_LEGS: Final = "legs"
#: Per-leg source metadata, a list parallel to :data:`BRANCH_LEGS` (import side).
BRANCH_LEG_METADATA: Final = "leg_metadata"

#: ``metadata`` key of the routing-side branch result.
BRANCH_ROUTE_KEY: Final = "branch_route"
#: The two leg names whose concatenated paths make ``system.routed_path`` (routing side).
BRANCH_ROUTE_TRUNK: Final = "trunk"
#: Where the junction hub sits, as an ``(x, y, z)`` tuple (routing side).
BRANCH_ROUTE_JUNCTION_POINT: Final = "junction_point"


def branch_leg_names(metadata: dict | None) -> list[str] | None:
    """The leg names an import-side ``metadata`` dict declares, or ``None`` when it declares no
    branch (a plain two-ended system)."""
    branch = (metadata or {}).get(BRANCH_KEY) or {}
    legs = branch.get(BRANCH_LEGS)
    return list(legs) if legs else None

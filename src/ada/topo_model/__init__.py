"""ada.topo_model — a small, generic demonstration of the ``ada.topology``
procedural engine: space boxes in, a steel structure out.

Start with :func:`build_topo_model` for the one-liner happy path, and read
:class:`SteelStru` as the reference for authoring your own blueprint.
"""

from .blueprint import SteelStru
from .build import (
    build_routing_grid,
    build_topo_model,
    build_topo_model_with_systems,
    make_space_boxes,
)
from .equipment import create_pump, create_tank
from .penetration import PenetrationBlueprintBase, StandardPenetrations

__all__ = [
    "PenetrationBlueprintBase",
    "StandardPenetrations",
    "SteelStru",
    "build_routing_grid",
    "build_topo_model",
    "build_topo_model_with_systems",
    "create_pump",
    "create_tank",
    "make_space_boxes",
]

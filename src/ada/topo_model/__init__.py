"""ada.topo_model — a small, generic demonstration of the ``ada.topology``
procedural engine: space boxes in, a steel structure out.

Start with :func:`build_topo_model` for the one-liner happy path, and read
:class:`SteelStru` as the reference for authoring your own blueprint.
"""

from .blueprint import SteelStru
from .build import build_topo_model, make_space_boxes

__all__ = ["SteelStru", "build_topo_model", "make_space_boxes"]

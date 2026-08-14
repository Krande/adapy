"""Entity-class choice for routed flow segments.

A route pipe carries ``segment_ifc_class`` metadata when it stands in for a
different distribution service (cable tray, duct): the straight segments then
emit that class and elbows the matching fitting class.
"""

from __future__ import annotations

_VALID_SEGMENT_CLASSES = ("IfcPipeSegment", "IfcCableSegment", "IfcDuctSegment")


def segment_entity_class(pipe_seg) -> str:
    """Entity class for a straight segment, from the segment's (or its parent
    pipe's) ``segment_ifc_class`` metadata; defaults to ``IfcPipeSegment``."""
    meta = getattr(pipe_seg, "metadata", None) or {}
    cls = meta.get("segment_ifc_class")
    if cls is None and pipe_seg.parent is not None:
        cls = (getattr(pipe_seg.parent, "metadata", None) or {}).get("segment_ifc_class")
    if cls is None:
        return "IfcPipeSegment"
    if cls not in _VALID_SEGMENT_CLASSES:
        raise ValueError(f"Unsupported segment_ifc_class {cls!r}; expected one of {_VALID_SEGMENT_CLASSES}")
    return cls


def fitting_entity_class(pipe_seg) -> str:
    """Fitting class matching the segment class (IfcPipeFitting/IfcCableFitting/IfcDuctFitting)."""
    return segment_entity_class(pipe_seg).replace("Segment", "Fitting")

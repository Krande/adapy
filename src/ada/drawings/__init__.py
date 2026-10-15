"""Dimensioned 2D drawings of adapy objects, as standalone SVG.

    import ada
    from ada.drawings import Drawing, LinearDimension, Leader, Caption, ScaleBar
    from ada.drawings.anchors import beam_end

    d = Drawing("Portal frame", view="front", scale=50)
    d.add(part)                                   # a Part/Assembly, objects, or a list of them
    d.annotate(
        LinearDimension(beam_end(col, "start"), beam_end(col, "end"), "vertical"),
        Leader(beam_end(rafter, "end"), "HEA300", offset=(10, 8)),
        Caption("FRONT ELEVATION"),
        ScaleBar(),
    )
    d.to_svg("portal.svg")

Modules: :mod:`.view` (cameras, scales, section planes), :mod:`.scene` (objects -> meshes and
lines), :mod:`.linework` (hidden-line removal and cuts, pluggable backends), :mod:`.annotations`,
:mod:`.anchors` (points on objects), :mod:`.svg` (the writer), :mod:`.drawing` (the facade).

The geometry comes from adapy's own CAD backend (``ada.cad.active_backend()``); nothing here
depends on IFC tooling. ``ada.drawings.export`` keeps the older IFC-file helper.
"""

from ada.drawings.annotations import (
    AngleDimension,
    Caption,
    DimensionFormat,
    Leader,
    LinearDimension,
    RadiusDimension,
    ScaleBar,
    SectionSymbol,
    Text,
    TitleBlock,
)
from ada.drawings.drawing import Drawing
from ada.drawings.linework import (
    Linework,
    LineworkBackend,
    available_linework_backends,
    register_linework_backend,
)
from ada.drawings.scene import Polyline
from ada.drawings.view import SectionPlane, View

__all__ = [
    "AngleDimension",
    "Caption",
    "DimensionFormat",
    "Drawing",
    "Leader",
    "LinearDimension",
    "Linework",
    "LineworkBackend",
    "Polyline",
    "RadiusDimension",
    "ScaleBar",
    "SectionPlane",
    "SectionSymbol",
    "Text",
    "TitleBlock",
    "View",
    "available_linework_backends",
    "register_linework_backend",
]

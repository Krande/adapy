from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDF import TDF_Label
from OCC.Core.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

from ada.visit.colors import Color


def set_name(label: TDF_Label, name: str):
    TDataStd_Name.Set(label, name)


def set_color(label: TDF_Label, color: Color, tool):
    r, g, b = color.rgb
    tool.SetColor(label, Quantity_Color(r, g, b, Quantity_TOC_RGB), XCAFDoc_ColorType.XCAFDoc_ColorSurf)


def get_color(color_tool: XCAFDoc_DocumentTool.ColorTool, shape, lab) -> Color:
    c = Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB)  # default color
    color_set = False
    if (
        color_tool.GetInstanceColor(shape, 0, c)
        or color_tool.GetInstanceColor(shape, 1, c)
        or color_tool.GetInstanceColor(shape, 2, c)
    ):
        color_tool.SetInstanceColor(shape, 0, c)
        color_tool.SetInstanceColor(shape, 1, c)
        color_tool.SetInstanceColor(shape, 2, c)
        color_set = True
    if not color_set:
        if color_tool.GetColor(lab, 0, c) or color_tool.GetColor(lab, 1, c) or color_tool.GetColor(lab, 2, c):
            color_tool.SetInstanceColor(shape, 0, c)
            color_tool.SetInstanceColor(shape, 1, c)
            color_tool.SetInstanceColor(shape, 2, c)

    return Color(c.Red(), c.Green(), c.Blue())

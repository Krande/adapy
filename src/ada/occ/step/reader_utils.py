from __future__ import annotations

from typing import TYPE_CHECKING

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape

try:

    HAVE_SVGWRITE = True
except ImportError:
    HAVE_SVGWRITE = False

if TYPE_CHECKING:
    from ada.occ.step.store import StepStore


def read_step_file_with_names_colors(store: StepStore) -> dict[TopoDS_Shape, tuple[str, Quantity_Color]]:
    """Returns list of tuples (topods_shape, label, color) Use OCAF."""
    shape_tool = store.shape_tool
    color_tool = store.color_tool
    output_shapes = dict()
    locs = []

    def _get_sub_shapes(lab, loc):
        l_subss = TDF_LabelSequence()
        shape_tool.GetSubShapes(lab, l_subss)
        l_comps = TDF_LabelSequence()
        shape_tool.GetComponents(lab, l_comps)

        if shape_tool.IsAssembly(lab):
            l_c = TDF_LabelSequence()
            shape_tool.GetComponents(lab, l_c)
            for i in range(l_c.Length()):
                label = l_c.Value(i + 1)
                if shape_tool.IsReference(label):
                    label_reference = TDF_Label()
                    shape_tool.GetReferredShape(label, label_reference)
                    loc = shape_tool.GetLocation(label)

                    locs.append(loc)
                    _get_sub_shapes(label_reference, loc)
                    locs.pop()

        elif shape_tool.IsSimpleShape(lab):
            shape = shape_tool.GetShape(lab)
            loc = TopLoc_Location()
            for l in locs:
                loc = loc.Multiplied(l)

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

            shape_disp = BRepBuilderAPI_Transform(shape, loc.Transformation()).Shape()
            if shape_disp not in output_shapes:
                output_shapes[shape_disp] = [lab.GetLabelName(), c]
            for i in range(l_subss.Length()):
                lab_subs = l_subss.Value(i + 1)
                shape_sub = shape_tool.GetShape(lab_subs)

                c = Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB)  # default color
                color_set = False
                if (
                    color_tool.GetInstanceColor(shape_sub, 0, c)
                    or color_tool.GetInstanceColor(shape_sub, 1, c)
                    or color_tool.GetInstanceColor(shape_sub, 2, c)
                ):
                    color_tool.SetInstanceColor(shape_sub, 0, c)
                    color_tool.SetInstanceColor(shape_sub, 1, c)
                    color_tool.SetInstanceColor(shape_sub, 2, c)
                    color_set = True

                if not color_set:
                    if (
                        color_tool.GetColor(lab_subs, 0, c)
                        or color_tool.GetColor(lab_subs, 1, c)
                        or color_tool.GetColor(lab_subs, 2, c)
                    ):
                        color_tool.SetInstanceColor(shape, 0, c)
                        color_tool.SetInstanceColor(shape, 1, c)
                        color_tool.SetInstanceColor(shape, 2, c)

                shape_to_disp = BRepBuilderAPI_Transform(shape_sub, loc.Transformation()).Shape()
                if shape_to_disp not in output_shapes:
                    output_shapes[shape_to_disp] = [lab_subs.GetLabelName(), c]

    def _get_shapes():
        labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(labels)
        for i in range(labels.Length()):
            root_item = labels.Value(i + 1)
            _get_sub_shapes(root_item, None)

    _get_shapes()
    return output_shapes

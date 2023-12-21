from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Shell

from ada.base.adacpp_interface import adacpp_switch
from ada.config import logger
from ada.occ.xcaf_utils import get_color

try:
    HAVE_SVGWRITE = True
except ImportError:
    HAVE_SVGWRITE = False

if TYPE_CHECKING:
    from ada.cadit.step.store import EntityProps, StepStore
    from ada.occ.store import OccShape


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
                _ = set_color(color_tool, shape, lab, c)

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
                    try:
                        if (
                            color_tool.GetColor(lab_subs, 0, c)
                            or color_tool.GetColor(lab_subs, 1, c)
                            or color_tool.GetColor(lab_subs, 2, c)
                        ):
                            color_tool.SetInstanceColor(shape, 0, c)
                            color_tool.SetInstanceColor(shape, 1, c)
                            color_tool.SetInstanceColor(shape, 2, c)
                    except TypeError as e:
                        logger.warning(f"Could not set color for {lab_subs.GetLabelName()}: {e}")

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


def node_to_step_shape(doc_node, store: StepStore, num_shapes: int):
    """Convert a node from a STEP file to a STEP shape"""
    shape = store.shape_tool.GetShape(doc_node.RefLabel)
    label = doc_node.RefLabel
    name = label.GetLabelName()
    rgb = get_color(store.color_tool, shape, label)
    return OccShape(shape, rgb, num_shapes, name)


def _iter_sub_labels(root_label: TDF_Label, shape_tool) -> Iterable[TDF_Label]:
    root_label.NbChildren()
    if shape_tool.IsAssembly(root_label):
        l_c = TDF_LabelSequence()
        shape_tool.GetComponents(root_label, l_c)
        res2 = l_c.Length()
        for i in range(res2):
            yield from _iter_sub_labels(l_c.Value(i + 1), shape_tool)


def _get_sub_shape_entity_props(lab: TDF_Label | TopoDS_Shape, shape_tool, color_tool, locs):
    l_subss = TDF_LabelSequence()
    shape_tool.GetSubShapes(lab, l_subss)
    l_comps = TDF_LabelSequence()
    shape_tool.GetComponents(lab, l_comps)
    lab.GetLabelName()

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
                yield from _get_sub_shape_entity_props(label_reference, shape_tool, color_tool, locs)
                locs.pop()

    elif shape_tool.IsSimpleShape(lab):
        shape = shape_tool.GetShape(lab)
        color = get_color(color_tool, shape, lab)

        yield EntityProps(hash(shape), lab.GetLabelName(), color)
        for i in range(l_subss.Length()):
            lab_subs = l_subss.Value(i + 1)
            shape_sub = shape_tool.GetShape(lab_subs)
            color = get_color(color_tool, shape_sub, lab)
            yield EntityProps(hash(shape_sub), lab_subs.GetLabelName(), color)


def iter_children(doc_node, store, num_shapes):
    """Iterate over all child nodes of a given node"""
    child_iter = doc_node.ChildIter
    child_iter.Initialize(doc_node.RefLabel)
    while child_iter.More():
        child = child_iter.Value()
        yield node_to_step_shape(child, store, num_shapes)
        child.Next()


def set_color_adacpp(color_tool, shape, label, color):
    """This is an experiment to see if one can alter a SWIG wrapped C++ object using a nanobind wrapped C++ function"""
    from adacpp.cadit import occt as nano_occt

    # Take the pointers from the SWIG wrapped objects
    ctool_pointer = int(color_tool.this)
    lab_pointer = int(label.this)
    shape_pointer = int(shape.this)
    c_pointer = int(color.this)

    # Convert the pointers to nanobind wrapped objects
    if isinstance(shape, TopoDS_Shell):
        adacpp_shape = nano_occt.TopoDS_Shell.from_ptr(shape_pointer)
    elif isinstance(shape, TopoDS_Shape):  # TopoDS_Shape is the base class of TopoDS_Shell
        adacpp_shape = nano_occt.TopoDS_Shape.from_ptr(shape_pointer)
    else:
        raise ValueError(f"Unsupported shape type {type(shape)}")

    adacpp_label = nano_occt.TDF_Label.from_ptr(lab_pointer)
    adacpp_color = nano_occt.Quantity_Color.from_ptr(c_pointer)
    adacpp_color_tool = nano_occt.XCAFDoc_ColorTool.from_ptr(ctool_pointer)

    # Change the color conditionally
    nano_occt.setInstanceColorIfAvailable(adacpp_color_tool, adacpp_label, adacpp_shape, adacpp_color)

    # check if the shape object is not null
    if shape.IsNull():
        raise ValueError("Shape is null")

    # need to return all these objects to avoid garbage collection :(
    return adacpp_shape, adacpp_label, adacpp_color, adacpp_color_tool


@adacpp_switch(alt_function=set_color_adacpp, broken=False)
def set_color(color_tool, shape, label, color):
    """Set the color of a shape"""
    if not (
        color_tool.GetColor(shape, 0, color)
        or color_tool.GetColor(shape, 1, color)
        or color_tool.GetColor(shape, 2, color)
    ):
        return None

    color_tool.SetInstanceColor(shape, 0, color)
    color_tool.SetInstanceColor(shape, 1, color)
    color_tool.SetInstanceColor(shape, 2, color)
    return shape

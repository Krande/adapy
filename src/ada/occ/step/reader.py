import os
from dataclasses import dataclass
from typing import Iterable

from OCC.Core import BRepTools
from OCC.Core.IFSelect import IFSelect_ItemsByEntity, IFSelect_RetDone
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TCollection import TCollection_ExtendedString
from OCC.Core.TDF import TDF_LabelSequence, TDF_Label
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCC.Core.TopExp import topexp_MapShapes
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool_ShapeTool, XCAFDoc_DocumentTool_ColorTool
from OCC.Extend.TopologyUtils import TopologyExplorer, list_of_shapes_to_compound

from ada.base.units import Units
from ada.config import logger
from ada.occ.utils import get_boundingbox


@dataclass
class StepShape:
    shape: TopoDS_Shape
    color: tuple[float, float, float] | None = None
    num_tot_entities: int = 0
    name: str | None = None


class StepReader:
    def __init__(self, filepath, verbosity=True, destination_units: Units = Units.M, include_wires=False):
        self.filepath = filepath
        self.destination_units = destination_units
        self.verbosity = verbosity
        self.include_wires = include_wires

    def create_step_reader(self) -> STEPControl_Reader:
        filename = str(self.filepath)
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"{filename} not found.")

        step_reader = STEPControl_Reader()
        Interface_Static_SetCVal("xstep.cascade.unit", self.destination_units.value.upper())

        logger.info(f"Reading STEP file: {filename} into unit: {self.destination_units.value}")
        status = step_reader.ReadFile(filename)
        logger.info(f"STEP file read status: {status}")

        if status != IFSelect_RetDone:  # check status
            raise AssertionError("Error: can't read file.")

        if self.verbosity:
            failsonly = False
            step_reader.PrintCheckLoad(failsonly, IFSelect_ItemsByEntity)
            step_reader.PrintCheckTransfer(failsonly, IFSelect_ItemsByEntity)

        transfer_result = step_reader.TransferRoots()
        if not transfer_result:
            raise AssertionError("Transfer failed.")

        if self.destination_units == Units.M:
            if step_reader.SystemLengthUnit() != 1000.0:
                raise AssertionError("System unit is not M.")
        elif self.destination_units == Units.MM:
            if step_reader.SystemLengthUnit() != 1.0:
                raise AssertionError("System unit is not MM.")

        return step_reader

    def get_root_shape(self) -> TopoDS_Shape | TopoDS_Compound | None:
        """Get root shape in STEP file."""
        step_reader = self.create_step_reader()
        _nbs = step_reader.NbShapes()
        if _nbs == 0:
            raise AssertionError("No shape to transfer.")
        if _nbs == 1:  # most cases
            return step_reader.Shape(1)
        if _nbs > 1:
            logger.info("Number of shapes:", _nbs)
            shapes = []
            # loop over root shapes
            for k in range(1, _nbs + 1):
                new_shp = step_reader.Shape(k)
                if not new_shp.IsNull():
                    shapes.append(new_shp)
            compound, result = list_of_shapes_to_compound(shapes)
            if not result:
                logger.warning("all shapes were not added to the compound")
            return compound

        return None

    def _iter_all_shapes_including_colors(self) -> Iterable[StepShape]:
        filename = str(self.filepath)
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"{filename} not found.")

        doc = TDocStd_Document(TCollection_ExtendedString("pythonocc-doc"))

        # Get root assembly
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(doc.Main())

        step_reader = STEPCAFControl_Reader()
        step_reader.SetColorMode(True)
        step_reader.SetNameMode(True)

        logger.info(f"Reading STEP file: {filename} into unit: {self.destination_units.value}")
        status = step_reader.ReadFile(str(filename))
        if status == IFSelect_RetDone:
            step_reader.Transfer(doc)

        labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(labels)
        num_labels = labels.Length()

        for i in range(num_labels):
            label = labels.Value(i + 1)
            root_name = label.GetLabelName()
            logger.info(f"Root label name: {root_name}")
            root_shape = shape_tool.GetShape(label)
            for shape, tot_num in self._iter_subshapes(root_shape):
                color = get_color(color_tool, shape, label)
                yield StepShape(shape, color, tot_num)

    def _iter_sub_labels(self, root_label: TDF_Label, shape_tool) -> Iterable[TDF_Label]:
        root_label.NbChildren()
        if shape_tool.IsAssembly(root_label):
            l_c = TDF_LabelSequence()
            shape_tool.GetComponents(root_label, l_c)
            res2 = l_c.Length()
            for i in range(res2):
                yield from self._iter_sub_labels(l_c.Value(i + 1), shape_tool)
        else:
            pass

    def _iter_subshapes(self, root_shape: TopoDS_Shape) -> Iterable[TopoDS_Shape]:
        t = TopologyExplorer(root_shape)
        # Find the total number of shapes to be yielded
        num_solids = t.number_of_solids()
        num_shells = t.number_of_shells()

        num_shapes = num_solids + num_shells
        if self.include_wires:
            num_wires = t.number_of_wires()
            num_shapes += num_wires

        # Yield the shapes
        for solid in t.solids():
            yield solid, num_shapes
        for shell in t.shells():
            yield shell, num_shapes

        if self.include_wires:
            for wire in t.wires():
                yield wire, num_shapes

    def iter_all_shapes(self, include_colors=False) -> Iterable[StepShape]:
        props_map = {}
        if include_colors:
            props_map = get_step_props_map(self.filepath)

        for i, (shape, num_shapes) in enumerate(self._iter_subshapes(self.get_root_shape())):
            props = props_map.get(i, None)
            if props is None:
                yield StepShape(shape, None, num_shapes)
            else:
                yield StepShape(shape, props.color, num_shapes, props.name)

    def get_bbox(self):
        shape = self.get_root_shape()
        return get_boundingbox(shape)


def serialize_shape(shape) -> str:
    shapes = BRepTools.BRepTools_ShapeSet()
    shapes.Add(shape)
    return shapes.WriteToString()


@dataclass
class EntityProps:
    hash: str
    name: str
    color: tuple[float, float, float]


def get_step_props_map(filename) -> dict[int, EntityProps]:
    """Slightly modified version of the example from the pythonocc documentation."""
    doc = TDocStd_Document(TCollection_ExtendedString("pythonocc-doc"))

    # Get root assembly
    shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool_ColorTool(doc.Main())

    step_reader = STEPCAFControl_Reader()
    step_reader.SetColorMode(True)
    step_reader.SetNameMode(True)

    logger.info(f"Reading STEP file: {filename} to generate properties map")
    status = step_reader.ReadFile(str(filename))
    if status == IFSelect_RetDone:
        step_reader.Transfer(doc)

    locs = []

    def _iter_shapes():
        labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(labels)
        num_labels = labels.Length()
        for i in range(num_labels):
            root_item = labels.Value(i + 1)
            for entity_prop in _get_sub_shape_entity_props(root_item, shape_tool, color_tool, locs):
                yield entity_prop

    return {i: x for i, x in enumerate(_iter_shapes())}

def _get_sub_shape_entity_props(lab, shape_tool, color_tool, locs):
    l_subss = TDF_LabelSequence()
    shape_tool.GetSubShapes(lab, l_subss)
    l_comps = TDF_LabelSequence()
    shape_tool.GetComponents(lab, l_comps)
    name = lab.GetLabelName()

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

        yield EntityProps("unique", lab.GetLabelName(), color)
        for i in range(l_subss.Length()):
            lab_subs = l_subss.Value(i + 1)
            shape_sub = shape_tool.GetShape(lab_subs)
            color = get_color(color_tool, shape_sub, lab)
            sub_shape_hash = hash(shape_sub)
            yield EntityProps(sub_shape_hash, lab_subs.GetLabelName(), color)


def get_color(color_tool, shape, lab) -> tuple[float, float, float]:
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
        if (
                color_tool.GetColor(lab, 0, c)
                or color_tool.GetColor(lab, 1, c)
                or color_tool.GetColor(lab, 2, c)
        ):
            color_tool.SetInstanceColor(shape, 0, c)
            color_tool.SetInstanceColor(shape, 1, c)
            color_tool.SetInstanceColor(shape, 2, c)

    return c.Red(), c.Green(), c.Blue()
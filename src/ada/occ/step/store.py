from __future__ import annotations

import math
import os
import pathlib
from dataclasses import dataclass
from typing import Iterable

from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepTools import breptools_Clean, breptools_WriteToString
from OCC.Core.IFSelect import IFSelect_ItemsByEntity, IFSelect_RetDone
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.Message import Message_ProgressRange
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.RWGltf import RWGltf_CafWriter, RWGltf_WriterTrsfFormat
from OCC.Core.RWMesh import RWMesh_CoordinateSystem_Zup
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCC.Core.XCAFDoc import (
    XCAFDoc_DocumentTool_ColorTool,
    XCAFDoc_DocumentTool_ShapeTool,
)
from OCC.Extend.TopologyUtils import TopologyExplorer, list_of_shapes_to_compound

from ada.base.units import Units
from ada.config import logger
from ada.occ.step.reader_utils import read_step_file_with_names_colors
from ada.occ.step.writer import set_color, set_name
from ada.occ.utils import get_boundingbox


@dataclass
class StepShape:
    shape: TopoDS_Shape
    color: tuple[float, float, float] | None = None
    num_tot_entities: int = 0
    name: str | None = None


class StepStore:
    def __init__(self, filepath, verbosity=True, destination_units: Units = Units.M, include_wires=False):
        self.filepath = filepath
        if isinstance(destination_units, str):
            destination_units = Units.from_str(destination_units)
        self.destination_units = destination_units
        self.verbosity = verbosity
        self.include_wires = include_wires
        self.step_reader: STEPControl_Reader | None = None
        # For OCAF
        self.shape_tool: XCAFDoc_DocumentTool_ShapeTool = None
        self.color_tool: XCAFDoc_DocumentTool_ColorTool = None
        self.doc: TDocStd_Document | None = None

    def create_step_reader(self, use_ocaf=False) -> STEPControl_Reader | STEPCAFControl_Reader:
        filename = str(self.filepath)
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"{filename} not found.")

        if not use_ocaf:
            step_reader = STEPControl_Reader()
        else:
            self.doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
            self.shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
            self.color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
            step_reader = STEPCAFControl_Reader()
            step_reader.SetColorMode(True)
            step_reader.SetLayerMode(True)
            step_reader.SetNameMode(True)
            step_reader.SetMatMode(True)
            step_reader.SetGDTMode(True)

        Interface_Static_SetCVal("xstep.cascade.unit", self.destination_units.value.upper())

        logger.info(f"Reading STEP file: '{filename}' [{self.destination_units.value=}] [{use_ocaf=}]")
        status = step_reader.ReadFile(filename)

        if status != IFSelect_RetDone:  # check status
            raise AssertionError("Error: can't read file.")

        if self.verbosity and not use_ocaf:
            failsonly = False
            step_reader.PrintCheckLoad(failsonly, IFSelect_ItemsByEntity)
            step_reader.PrintCheckTransfer(failsonly, IFSelect_ItemsByEntity)

        if not use_ocaf:
            transfer_result = step_reader.TransferRoots()
        else:
            transfer_result = step_reader.Transfer(self.doc)

        if not transfer_result:
            raise AssertionError("Transfer failed.")

        self.step_reader = step_reader
        self.check_units()
        return step_reader

    def check_units(self):
        step_reader = self.step_reader
        if step_reader is None:
            raise AssertionError("step_reader is None")
        if isinstance(step_reader, STEPCAFControl_Reader):
            step_reader = step_reader.Reader()

        if self.destination_units == Units.M:
            if step_reader.SystemLengthUnit() != 1000.0:
                raise AssertionError("System unit is not M.")
        elif self.destination_units == Units.MM:
            if step_reader.SystemLengthUnit() != 1.0:
                raise AssertionError("System unit is not MM.")

    def get_root_shape(self) -> TopoDS_Shape | TopoDS_Compound | None:
        """Get root shape in STEP file."""
        step_reader = self.step_reader
        if step_reader is None:
            step_reader = self.create_step_reader()
            _nbs = step_reader.NbShapes()
        else:
            if isinstance(step_reader, STEPCAFControl_Reader):
                step_reader = step_reader.ChangeReader()
                _nbs = step_reader.NbRootsForTransfer()
            else:
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

    def get_num_shapes(self, root_shape=None):
        if root_shape is None:
            root_shape = self.get_root_shape()

        t = TopologyExplorer(root_shape)

        # Find the total number of shapes to be yielded
        num_solids = t.number_of_solids()
        num_shells = t.number_of_shells()

        num_shapes = num_solids + num_shells
        if self.include_wires:
            num_wires = t.number_of_wires()
            num_shapes += num_wires

        return num_shapes

    def _iter_subshapes(self, root_shape: TopoDS_Shape) -> Iterable[TopoDS_Shape]:
        t = TopologyExplorer(root_shape)
        # Yield the shapes
        for solid in t.solids():
            yield solid
        for shell in t.shells():
            yield shell

        if self.include_wires:
            for wire in t.wires():
                yield wire

    def iter_all_shapes(self, include_colors=False) -> Iterable[StepShape]:
        props_map = {}
        if include_colors:
            self.create_step_reader(True)
            num_shapes = self.get_num_shapes()
            for topods_shape, (label, c_quant) in read_step_file_with_names_colors(self).items():
                color = c_quant.Red(), c_quant.Green(), c_quant.Blue()
                yield StepShape(topods_shape, color, num_shapes, label)
                del topods_shape
        else:
            num_shapes = self.get_num_shapes()
            for i, shape in enumerate(self._iter_subshapes(self.get_root_shape())):
                props = props_map.get(i, None)
                if props is None:
                    yield StepShape(shape, None, num_shapes)
                else:
                    yield StepShape(shape, props.color, num_shapes, props.name)

    def get_bbox(self):
        shape = self.get_root_shape()
        return get_boundingbox(shape)

    def to_gltf(self, gltf_file, line_defl: float = None, angle_def: float = None) -> None:
        if isinstance(gltf_file, str):
            gltf_file = pathlib.Path(gltf_file)

        doc = TDocStd_Document(TCollection_ExtendedString("ada-py"))
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(doc.Main())

        for i, step_shape in enumerate(self.iter_all_shapes(True)):
            shp = step_shape.shape
            tesselate_shape(shp, line_defl, angle_def)

            sub_shape_label = shape_tool.AddShape(shp)
            set_color(sub_shape_label, step_shape.color, color_tool)
            set_name(sub_shape_label, step_shape.name)

        # GLTF options
        a_format = RWGltf_WriterTrsfFormat.RWGltf_WriterTrsfFormat_Compact

        # metadata
        a_file_info = TColStd_IndexedDataMapOfStringString()
        a_file_info.Add(TCollection_AsciiString("Authors"), TCollection_AsciiString("ada-py"))

        #
        # Binary export
        #
        if gltf_file.suffix == ".glb":
            binary = True
            glb_writer = RWGltf_CafWriter(TCollection_AsciiString(str(gltf_file)), binary)
            glb_writer.ChangeCoordinateSystemConverter().SetInputCoordinateSystem(RWMesh_CoordinateSystem_Zup)
            glb_writer.SetTransformationFormat(a_format)
            pr = Message_ProgressRange()  # this is required
            glb_writer.Perform(doc, a_file_info, pr)
        elif gltf_file.suffix == ".gltf":
            binary = False
            gltf_writer = RWGltf_CafWriter(TCollection_AsciiString(str(gltf_file)), binary)
            gltf_writer.ChangeCoordinateSystemConverter().SetInputCoordinateSystem(RWMesh_CoordinateSystem_Zup)
            gltf_writer.SetTransformationFormat(a_format)
            pr = Message_ProgressRange()  # this is required
            gltf_writer.Perform(doc, a_file_info, pr)


def serialize_shape(shape: TopoDS_Shape) -> str:
    breptools_Clean(shape)
    return breptools_WriteToString(shape)


def tesselate_shape(shp, line_defl: float = None, angle_def: float = 20):
    breptools_Clean(shp)

    msh_algo = BRepMesh_IncrementalMesh(shp, True)
    msh_algo.Parameters().InParallel = True

    if line_defl is not None:
        msh_algo.Parameters().Deflection = line_defl

    if angle_def is not None:
        msh_algo.Parameters().Angle = angle_def * math.pi / 180

    # Triangulate
    msh_algo.Perform()

    return shp


@dataclass
class EntityProps:
    hash: str
    name: str
    color: tuple[float, float, float]


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


def get_color(color_tool: XCAFDoc_DocumentTool_ColorTool, shape, lab) -> tuple[float, float, float]:
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

    return c.Red(), c.Green(), c.Blue()


def node_to_step_shape(doc_node, store: StepStore, num_shapes: int):
    """Convert a node from a STEP file to a STEP shape"""
    shape = store.shape_tool.GetShape(doc_node.RefLabel)
    label = doc_node.RefLabel
    name = label.GetLabelName()
    rgb = get_color(store.color_tool, shape, label)
    return StepShape(shape, rgb, num_shapes, name)


def iter_children(doc_node, store, num_shapes):
    """Iterate over all child nodes of a given node"""
    child_iter = doc_node.ChildIter
    child_iter.Initialize(doc_node.RefLabel)
    while child_iter.More():
        child = child_iter.Value()
        yield node_to_step_shape(child, store, num_shapes)
        child.Next()

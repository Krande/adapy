from __future__ import annotations

import pathlib
from enum import Enum
from typing import Any, Iterable

import OCC.Core.Interface as OCCInterface
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeEdge2d,
    BRepBuilderAPI_MakeVertex,
)
from OCC.Core.Geom import Geom_Curve
from OCC.Core.Geom2d import Geom2d_BSplineCurve
from OCC.Core.gp import gp_Pnt, gp_Pnt2d
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
from OCC.Core.STEPControl import STEPControl_AsIs
from OCC.Core.TDocStd import TDocStd_Application, TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.XSControl import XSControl_WorkSession

from ada.base.units import Units
from ada.config import logger
from ada.occ.xcaf_utils import set_color, set_name
from ada.visit.colors import Color


class StepSchema(Enum):
    AP203 = "AP203"
    AP214 = "AP214"
    AP242 = "AP242"


class StepWriter:
    def __init__(self, top_level_name: str = "Assembly", units: Units = Units.M, schema: StepSchema = StepSchema.AP214):
        self.schema = schema
        app = TDocStd_Application()
        doc = TDocStd_Document("XmlOcaf")
        app.InitDocument(doc)

        # The shape tool
        shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
        shape_tool.SetAutoNaming(False)

        # The color tool
        self.color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

        # Set up the compound
        comp = TopoDS_Compound()
        comp_builder = BRep_Builder()
        comp_builder.MakeCompound(comp)
        self.comp_builder = comp_builder

        self.doc = doc
        self.comp = comp

        top_level_label = shape_tool.AddShape(comp, True)
        set_name(top_level_label, top_level_name)
        self.tll = top_level_label
        self.shape_tool = shape_tool
        self.units = units

    def add_shape(self, shape: Any, name: str, rgb_color=None, parent=None):
        if issubclass(shape.__class__, gp_Pnt):
            # if a gp_Pnt is passed, first convert to vertex
            vertex = BRepBuilderAPI_MakeVertex(shape)
            shape = vertex.Shape()
        elif isinstance(shape, gp_Pnt2d):
            vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(shape.X(), shape.Y(), 0))
            shape = vertex.Shape()
        elif isinstance(shape, Geom_Curve):
            edge = BRepBuilderAPI_MakeEdge(shape)
            shape = edge.Shape()
        elif isinstance(shape, Geom2d_BSplineCurve):
            edge2d = BRepBuilderAPI_MakeEdge2d(shape)
            shape = edge2d.Shape()

        self.comp_builder.Add(self.comp, shape)
        parent = self.tll if parent is None else parent
        shape_label = self.shape_tool.AddSubShape(parent, shape)
        if isinstance(rgb_color, Iterable):
            rgb_color = Color(*rgb_color)
        elif isinstance(rgb_color, str):
            rgb_color = Color.from_str(rgb_color)
        elif rgb_color is None:
            rgb_color = Color(1, 0, 0)
        else:
            raise ValueError(f"rgb_color must be iterable, str, or None, not {type(rgb_color)}")

        if shape_label.IsNull():
            shape_label = self.shape_tool.AddShape(shape, False, False)
            logger.info("Adding as SubShape label generated an IsNull label. Adding as shape instead ")
        set_color(shape_label, rgb_color, self.color_tool)
        set_name(shape_label, name)

    def export(self, step_file: pathlib.Path | str):
        if isinstance(step_file, str):
            step_file = pathlib.Path(step_file)
        step_file.parent.mkdir(parents=True, exist_ok=True)

        # Set up the writer
        session = XSControl_WorkSession()

        writer = STEPCAFControl_Writer(session, False)
        writer.SetColorMode(True)
        writer.SetNameMode(True)

        SetCVal = OCCInterface.Interface_Static.SetCVal

        SetCVal("write.step.unit", self.units.value.upper())
        SetCVal("write.step.schema", self.schema.value.upper())

        writer.Transfer(self.doc, STEPControl_AsIs)
        status = writer.Write(str(step_file))

        if status != 1:
            raise Exception("STEP export failed")
        else:
            print(f"STEP export status: {status}")

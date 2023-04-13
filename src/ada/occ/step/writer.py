from __future__ import annotations

import pathlib
from enum import Enum

from OCC.Core.BRep import BRep_Builder
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
from OCC.Core.STEPControl import STEPControl_AsIs
from OCC.Core.TCollection import TCollection_ExtendedString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDF import TDF_Label
from OCC.Core.TDocStd import TDocStd_Application, TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCC.Core.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
from OCC.Core.XSControl import XSControl_WorkSession

from ada.base.units import Units


class StepSchema(Enum):
    AP203 = "AP203"
    AP214 = "AP214"
    AP242 = "AP242"


class StepWriter:
    def __init__(self, top_level_name: str = "Assembly", units: Units = Units.M, schema: StepSchema = StepSchema.AP242):
        self.schema = schema
        app = TDocStd_Application()
        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
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

        top_level_label = shape_tool.AddShape(comp, False)
        set_name(top_level_label, top_level_name)
        self.tll = top_level_label
        self.shape_tool = shape_tool
        self.units = units

    def add_shape(self, shape: TopoDS_Shape | TopoDS_Compound, name: str, rgb_color=None, parent=None):
        self.comp_builder.Add(self.comp, shape)
        parent = self.tll if parent is None else parent
        shape_label = self.shape_tool.AddSubShape(parent, shape)
        rgb_color = (1, 0, 0) if rgb_color is None else rgb_color
        set_color(shape_label, rgb_color, self.color_tool)
        set_name(shape_label, name)

    def export(self, step_file: pathlib.Path):
        # Set up the writer
        session = XSControl_WorkSession()

        writer = STEPCAFControl_Writer(session, False)
        writer.SetColorMode(True)
        writer.SetLayerMode(False)
        writer.SetNameMode(True)

        Interface_Static_SetCVal("write.step.unit", self.units.value.upper())
        Interface_Static_SetCVal("write.step.schema", self.schema.value.upper())

        writer.Transfer(self.doc, STEPControl_AsIs)
        status = writer.Write(str(step_file))

        if not status:
            raise Exception("STEP export failed")
        else:
            print(f"STEP export status: {status}")


def set_name(label: TDF_Label, name: str):
    TDataStd_Name.Set(label, TCollection_ExtendedString(name))


def set_color(label: TDF_Label, color: tuple, tool):
    r, g, b = color
    tool.SetColor(label, Quantity_Color(r, g, b, Quantity_TOC_RGB), XCAFDoc_ColorType.XCAFDoc_ColorSurf)

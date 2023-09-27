import pathlib
from typing import Callable, Iterable

from OCC.Core.Message import Message_ProgressRange
from OCC.Core.RWGltf import RWGltf_CafWriter, RWGltf_WriterTrsfFormat
from OCC.Core.RWMesh import RWMesh_CoordinateSystem_Zup
from OCC.Core.TCollection import TCollection_AsciiString
from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

from ada.base.units import Units
from ada.cadit.ifc.utils import tesselate_shape
from ada.occ.store import OccShape
from ada.occ.xcaf_utils import set_color, set_name


def to_gltf(
    gltf_file,
    occ_shape_iterable: Iterable[OccShape],
    line_defl: float = None,
    angle_def: float = None,
    export_units: Units | str = Units.M,
    progress_callback: Callable[[int, int], None] = None,
    source_units: Units | str = Units.M,
) -> None:
    if isinstance(export_units, str):
        export_units = Units.from_str(export_units)

    if isinstance(gltf_file, str):
        gltf_file = pathlib.Path(gltf_file)

    doc = TDocStd_Document("ada-py")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

    for i, step_shape in enumerate(occ_shape_iterable, start=1):
        shp = step_shape.shape
        if isinstance(shp, TopoDS_Compound):
            continue

        tesselate_shape(shp, line_defl, angle_def)
        if progress_callback is not None:
            progress_callback(i, step_shape.num_tot_entities)

        sub_shape_label = shape_tool.AddShape(shp)
        set_color(sub_shape_label, step_shape.color, color_tool)
        set_name(sub_shape_label, step_shape.name)

    # shp = self.get_root_shape(True)
    # tesselate_shape(shp, line_defl, angle_def)
    # sub_shape_label = shape_tool.AddShape(shp)

    # GLTF options
    a_format = RWGltf_WriterTrsfFormat.RWGltf_WriterTrsfFormat_Compact

    # metadata
    a_file_info = TColStd_IndexedDataMapOfStringString()
    a_file_info.Add(TCollection_AsciiString("Authors"), TCollection_AsciiString("ada-py"))

    # Binary export
    binary = True if gltf_file.suffix == ".glb" else False

    glb_writer = RWGltf_CafWriter(str(gltf_file), binary)
    if export_units == Units.M and source_units == Units.MM:
        glb_writer.ChangeCoordinateSystemConverter().SetInputLengthUnit(0.001)
    elif export_units == Units.MM and source_units == Units.M:
        glb_writer.ChangeCoordinateSystemConverter().SetInputLengthUnit(1000)
    else:  # either store and export units are the same or we don't know the store units
        pass
    glb_writer.ChangeCoordinateSystemConverter().SetInputCoordinateSystem(RWMesh_CoordinateSystem_Zup)
    glb_writer.SetTransformationFormat(a_format)
    pr = Message_ProgressRange()  # this is required
    glb_writer.Perform(doc, a_file_info, pr)

import logging
import os
import pathlib
from typing import Union

from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCC.Core.IFSelect import IFSelect_RetError
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.STEPConstruct import stepconstruct_FindEntity
from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCC.Core.TCollection import TCollection_HAsciiString

from ada.base.physical_objects import BackendGeom
from ada.concepts.levels import Assembly, Part
from ada.concepts.piping import Pipe
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate, Wall
from ada.core.utils import Counter
from ada.fem.shapes import ElemType

# Reference: https://www.opencascade.com/doc/occt-7.4.0/overview/html/occt_user_guides__step.html#occt_step_3

shp_names = Counter(1, "shp")
valid_types = Union[BackendGeom, Beam, Plate, Wall, Part, Assembly, Shape, Pipe]


class StepExporter:
    def __init__(self, schema="AP242", assembly_mode=1):
        self.writer = STEPControl_Writer()
        fp = self.writer.WS().TransferWriter().FinderProcess()
        self.fp = fp

        Interface_Static_SetCVal("write.step.schema", schema)
        # Interface_Static_SetCVal('write.precision.val', '1e-5')
        Interface_Static_SetCVal("write.precision.mode", "1")
        Interface_Static_SetCVal("write.step.assembly", str(assembly_mode))

    def add_to_step_writer(self, obj: valid_types, geom_repr=ElemType.SOLID, fuse_piping=False):
        """Write current assembly to STEP file"""
        valid_geom_repr = [ElemType.SOLID, ElemType.SHELL, ElemType.LINE]
        if geom_repr not in valid_geom_repr:
            raise ValueError(f'Invalid geom_repr: "{geom_repr}". Must be in "{valid_geom_repr}"')

        if issubclass(type(obj), Shape):
            self.add_geom(obj.geom, obj)
        elif type(obj) in (Beam, Plate, Wall):
            self.export_structural(obj, geom_repr)
        elif type(obj) is Pipe:
            self.export_piping(obj, geom_repr, fuse_piping)
        elif type(obj) in (Part, Assembly):
            for obj in obj.get_all_physical_objects():
                if type(obj) in (Plate, Beam, Wall):
                    self.export_structural(obj, geom_repr)
                elif type(obj) in (Pipe,):
                    self.export_piping(obj, geom_repr, fuse_piping)
                elif type(obj) is Shape:
                    self.add_geom(obj.geom, obj)
                else:
                    raise ValueError("Unkown Geometry type")

    def write_to_file(self, destination_file, silent):
        destination_file = pathlib.Path(destination_file).with_suffix(".stp")
        os.makedirs(destination_file.parent, exist_ok=True)

        status = self.writer.Write(str(destination_file))
        if int(status) > int(IFSelect_RetError):
            raise Exception("Error during write operation")
        if silent is False:
            print(f'step file created at "{destination_file}"')

    def add_geom(self, geom, obj):
        name = obj.name if obj.name is not None else next(shp_names)
        Interface_Static_SetCVal("write.step.product.name", name)

        try:
            stat = self.writer.Transfer(geom, STEPControl_AsIs)
        except BaseException as e:
            logging.info(f"Passing {obj} due to {e}")
            return None

        if int(stat) > int(IFSelect_RetError):
            raise Exception("Some Error occurred")

        item = stepconstruct_FindEntity(self.fp, geom)
        if not item:
            logging.debug("STEP item not found for FindEntity")
        else:
            item.SetName(TCollection_HAsciiString(name))

    def export_structural(self, stru_obj: Union[Plate, Beam, Wall], geom_repr):
        if geom_repr == ElemType.SHELL:
            self.add_geom(stru_obj.shell, stru_obj)
        elif geom_repr == ElemType.LINE:
            self.add_geom(stru_obj.line, stru_obj)
        else:
            self.add_geom(stru_obj.solid, stru_obj)

    def export_piping(self, pipe: Pipe, geom_repr, fuse_shapes=False):
        result = None
        for pipe_seg in pipe.segments:
            if geom_repr == ElemType.LINE:
                geom = pipe_seg.line
            elif geom_repr == ElemType.SHELL:
                geom = pipe_seg.shell
            else:
                geom = pipe_seg.solid

            if fuse_shapes is True:
                if result is None:
                    result = geom
                else:
                    result = BRepAlgoAPI_Fuse(result, geom).Shape()
            else:
                self.add_geom(geom, pipe)

        if fuse_shapes is True:
            self.add_geom(result, pipe)

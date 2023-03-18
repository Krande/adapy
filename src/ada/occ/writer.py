from __future__ import annotations

import os
import pathlib
from io import StringIO
from typing import Union

from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCC.Core.IFSelect import IFSelect_RetError
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.STEPConstruct import stepconstruct_FindEntity
from OCC.Core.STEPControl import (
    STEPControl_AsIs,
    STEPControl_ShellBasedSurfaceModel,
    STEPControl_Writer,
)
from OCC.Core.TCollection import TCollection_HAsciiString

from ada import Assembly, Beam, Part, Pipe, Plate, Shape, Wall
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.config import get_logger
from ada.core.utils import Counter

# Reference: https://www.opencascade.com/doc/occt-7.4.0/overview/html/occt_user_guides__step.html#occt_step_3

logger = get_logger()
shp_names = Counter(1, "shp")
valid_types = Union[BackendGeom, Beam, Plate, Wall, Part, Assembly, Shape, Pipe]


class OCCExporter:
    def __init__(self, schema="AP242", assembly_mode=1):
        self.writer = STEPControl_Writer()
        fp = self.writer.WS().TransferWriter().FinderProcess()
        self.fp = fp

        Interface_Static_SetCVal("write.step.schema", schema)
        # Interface_Static_SetCVal('write.precision.val', '1e-5')
        Interface_Static_SetCVal("write.precision.mode", "1")
        Interface_Static_SetCVal("write.step.assembly", str(assembly_mode))

    def add_to_step_writer(self, obj: valid_types, geom_repr: GeomRepr | str = GeomRepr.SOLID, fuse_piping=False):
        """Write current assembly to STEP file"""
        from ada.concepts.connections import JointBase

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        if issubclass(type(obj), Shape):
            self.add_geom(obj.geom(), obj, geom_repr=geom_repr)
        elif isinstance(obj, (Beam, Plate, Wall)):
            self.export_structural(obj, geom_repr)
        elif isinstance(obj, Pipe):
            self.export_piping(obj, geom_repr, fuse_piping)
        elif isinstance(obj, (Part, Assembly)) or issubclass(type(obj), JointBase):
            for sub_obj in obj.get_all_physical_objects(sub_elements_only=False):
                if isinstance(sub_obj, (Plate, Beam, Wall)):
                    self.export_structural(sub_obj, geom_repr)
                elif isinstance(sub_obj, Pipe):
                    self.export_piping(sub_obj, geom_repr, fuse_piping)
                elif issubclass(type(sub_obj), Shape):
                    self.add_geom(sub_obj.geom(), sub_obj, geom_repr=geom_repr)
                else:
                    raise ValueError("Unknown Geometry type")
        else:
            raise ValueError("Unknown Geometry type")

    def add_geom(self, geom, obj, geom_repr: GeomRepr | str = None):
        from ada.concepts.transforms import Placement
        from ada.core.vector_utils import vector_length

        from .utils import transform_shape

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        name = obj.name if obj.name is not None else next(shp_names)
        Interface_Static_SetCVal("write.step.product.name", name)

        # Transform geometry
        res = obj.placement.absolute_placement()
        if vector_length(res - Placement().origin) > 0:
            geom = transform_shape(geom, transform=tuple(res))
        try:
            if geom_repr == GeomRepr.SHELL:
                stat = self.writer.Transfer(geom, STEPControl_ShellBasedSurfaceModel)
            else:
                stat = self.writer.Transfer(geom, STEPControl_AsIs)
        except BaseException as e:
            logger.info(f"Passing {obj} due to {e}")
            return None

        if int(stat) > int(IFSelect_RetError):
            raise Exception("Some Error occurred")

        item = stepconstruct_FindEntity(self.fp, geom)
        if not item:
            logger.debug("STEP item not found for FindEntity")
        else:
            item.SetName(TCollection_HAsciiString(name))

    def export_structural(self, stru_obj: Plate | Beam | Wall, geom_repr):
        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        if geom_repr == GeomRepr.SHELL:
            self.add_geom(stru_obj.shell(), stru_obj)
        elif geom_repr == GeomRepr.LINE:
            self.add_geom(stru_obj.line(), stru_obj)
        else:
            self.add_geom(stru_obj.solid(), stru_obj)

    def export_piping(self, pipe: Pipe, geom_repr, fuse_shapes=False):
        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        result = None
        for pipe_seg in pipe.segments:
            if geom_repr == GeomRepr.LINE:
                geom = pipe_seg.line
            elif geom_repr == GeomRepr.SHELL:
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

    def write_to_file(self, destination_file, silent, return_file_obj=False) -> None | StringIO:
        if return_file_obj:
            logger.warning("returning file objects for STEP is not yet supported. But will be from OCCT v7.7.0.")

        destination_file = pathlib.Path(destination_file).with_suffix(".stp")
        os.makedirs(destination_file.parent, exist_ok=True)

        status = self.writer.Write(str(destination_file))
        if int(status) > int(IFSelect_RetError):
            raise Exception("Error during write operation")
        if silent is False:
            print(f'step file created at "{destination_file}"')

    def to_obj_mesh(self):
        # from ada.visualize.renderer_occ import occ_shape_to_faces

        # model = self.writer.

        # position, indices, normals, _ = occ_shape_to_faces(
        #     self.writer,
        #     export_config.quality,
        #     export_config.render_edges,
        #     export_config.parallel,
        # )
        pass

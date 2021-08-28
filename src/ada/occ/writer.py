import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Union

from OCC.Core.IFSelect import IFSelect_RetError
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.STEPConstruct import stepconstruct_FindEntity
from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCC.Core.TCollection import TCollection_HAsciiString

from ada.concepts.levels import Assembly, Part
from ada.concepts.piping import Pipe
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate, Wall
from ada.core.utils import Counter

# OpenCascade reference: https://www.opencascade.com/doc/occt-7.4.0/overview/html/occt_user_guides__step.html#occt_step_3
shp_names = Counter(1, "shp")

intypes = Union[Beam, Plate, Wall, Part, Assembly, Shape]


class StepExporter:
    def __init__(self):
        writer = STEPControl_Writer()
        fp = writer.WS().TransferWriter().FinderProcess()
        Interface_Static_SetCVal("write.step.schema", schema)
        # Interface_Static_SetCVal('write.precision.val', '1e-5')
        Interface_Static_SetCVal("write.precision.mode", "1")
        Interface_Static_SetCVal("write.step.assembly", str(assembly_mode))
        self.fp = fp

    def to_stp(
        self,
        obj: Union[Beam, Plate, Wall, Part, Assembly, Shape],
        destination_file,
        geom_repr="solid",
        schema="AP242",
        silent=False,
    ):
        """Write current assembly to STEP file"""

        if geom_repr not in ["shell", "solid", "line"]:
            raise ValueError('Geometry representation can only accept either "solid", "shell" or "lines" as input')

        destination_file = pathlib.Path(destination_file).with_suffix(".stp")

        assembly_mode = 1
        shp_names = Counter(1, "shp")

        def add_geom(geo, o):
            name = o.name if o.name is not None else next(shp_names)
            Interface_Static_SetCVal("write.step.product.name", name)
            stat = writer.Transfer(geo, STEPControl_AsIs)
            if int(stat) > int(IFSelect_RetError):
                raise Exception("Some Error occurred")

            item = stepconstruct_FindEntity(fp, geo)
            if not item:
                logging.debug("STEP item not found for FindEntity")
            else:
                item.SetName(TCollection_HAsciiString(name))

        if issubclass(type(obj), Shape):
            assert isinstance(obj, Shape)
            add_geom(obj.geom, obj)
        elif type(obj) in (Beam, Plate, Wall):
            assert isinstance(obj, (Beam, Plate, Wall))
            if geom_repr == "shell":
                add_geom(obj.shell, obj)
            elif geom_repr == "line":
                add_geom(obj.line, obj)
            else:
                add_geom(obj.solid, obj)
        elif type(obj) is Pipe:
            assert isinstance(obj, Pipe)
            for geom in obj.geometries:
                add_geom(geom, obj)
        elif type(obj) in (Part, Assembly):
            assert isinstance(obj, Part)

            for p in obj.get_all_subparts() + [obj]:
                for obj in list(p.plates) + list(p.beams) + list(p.shapes) + list(p.pipes) + list(p.walls):
                    if type(obj) in (Plate, Beam, Wall):
                        try:
                            if geom_repr == "shell":
                                add_geom(obj.shell, obj)
                            else:
                                add_geom(obj.solid, obj)
                        except BaseException as e:
                            logging.info(f'passing pl "{obj.name}" due to {e}')
                            continue
                    elif type(obj) in (Pipe,):
                        assert isinstance(obj, Pipe)
                        for geom in obj.geometries:
                            add_geom(geom, obj)
                    elif type(obj) is Shape:
                        add_geom(obj.geom, obj)
                    else:
                        raise ValueError("Unkown Geometry type")

        os.makedirs(destination_file.parent, exist_ok=True)

        status = writer.Write(str(destination_file))
        if int(status) > int(IFSelect_RetError):
            raise Exception("Error during write operation")
        if silent is False:
            print(f'step file created at "{destination_file}"')


def add_geom(fp, writer, geom, obj):
    # Transfer geom
    stat = writer.Transfer(geom, STEPControl_AsIs)
    if int(stat) > int(IFSelect_RetError):
        raise Exception("Some Error occurred")

    # Try to set name
    name = obj.name if obj.name is not None else next(shp_names)
    Interface_Static_SetCVal("write.step.product.name", name)

    item = stepconstruct_FindEntity(fp, geom)
    if not item:
        logging.debug("STEP item not found for FindEntity")
    else:
        item.SetName(TCollection_HAsciiString(name))


def export_structural(stru: Union[Plate, Beam, Wall], geom_repr):
    try:
        if geom_repr == "shell":
            add_geom(stru.shell, stru)
        else:
            add_geom(stru.solid, stru)
    except BaseException as e:
        logging.info(f'passing structural object "{stru.name}" due to {e}')
        return None


def add_part_obj_to_writer(level_obj: Union[Part, Assembly], geom_repr):
    for obj in level_obj.get_all_physical_objects():
        if type(obj) in (Plate, Beam, Wall):
            try:
                if geom_repr == "shell":
                    add_geom(obj.shell, obj)
                else:
                    add_geom(obj.solid, obj)
            except BaseException as e:
                logging.info(f'passing pl "{obj.name}" due to {e}')
                continue
        elif type(obj) in (Pipe,):
            assert isinstance(obj, Pipe)
            for geom in obj.geometries:
                add_geom(geom, obj)
        elif type(obj) is Shape:
            add_geom(obj.geom, obj)
        else:
            raise ValueError("Unkown Geometry type")

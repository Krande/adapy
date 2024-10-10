import pathlib

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.geom.surfaces import Plane
from ada.procedural_modelling.components_base import app, component_decorator


@component_decorator(export_file_type=FileTypeDC.IFC)
def make_basic_shapes(output_file: pathlib.Path = None):
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    # plane =
    a = ada.Assembly("BasicShapes") / (box,)
    a.to_ifc(output_file)


if __name__ == "__main__":
    app()

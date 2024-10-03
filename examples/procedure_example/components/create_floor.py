import pathlib

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.procedural_modelling.components_base import component_decorator, app


@component_decorator(export_file_type=FileTypeDC.IFC)
def main(
        name: str = 'floor1',
        origin: tuple[float, float, float] = (0, 0, 0),
        width: float = 40,
        length: float = 20,
        thickness: float = 0.01,
        output_file: pathlib.Path = None
) -> None:

    p = ada.Part(name) / ada.Plate("pl1", [(0, 0), (width, 0), (width, length), (0, length)], thickness, origin=origin)
    # The assembly level is to be discarded. Only the part is relevant for merging into another IFC file
    a = ada.Assembly("TempAssembly") / p
    a.to_ifc(output_file)


if __name__ == "__main__":
    app()

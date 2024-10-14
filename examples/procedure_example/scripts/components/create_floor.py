import pathlib

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.procedural_modelling.procedures_base import ComponentDecorator, app


@ComponentDecorator(outputs=dict(output_file=FileTypeDC.IFC))
def main(
    name: str = "floor1",
    origin: tuple[float, float, float] = (0, 0, 0),
    width: float = 5,
    length: float = 20,
    thickness: float = 0.01,
    output_file: pathlib.Path = None,
) -> None:

    pl = ada.Plate(name, [(0, 0), (width, 0), (width, length), (0, length)], thickness, origin=origin)
    a = ada.Assembly("TempAssembly") / pl  # Assembly level to be discarded. Only pl is relevant
    a.to_ifc(output_file)


if __name__ == "__main__":
    app()

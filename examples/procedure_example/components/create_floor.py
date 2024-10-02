import pathlib

import typer

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.comms.procedures import procedure_decorator
from ada.config import Config

app = typer.Typer()


@procedure_decorator(app, export_file_type=FileTypeDC.IFC)
def main(name: str = 'floor1', p0: tuple[float, float, float] = (0, 0, 0), width: float = 40, length: float = 20) -> pathlib.Path:
    temp_dir = Config().websockets_server_temp_dir
    comp_dir = temp_dir / "components"
    p = ada.Part(name) / ada.Plate("pl1", [(0, 0), (width, 0), (width, length), (0, length)], 0.01, origin=p0)
    # The assembly level is to be discarded. Only the part is relevant for merging into another IFC file
    a = ada.Assembly("TempAssembly") / p
    comp_dir.mkdir(parents=True, exist_ok=True)
    new_file = comp_dir / f"{name}.ifc"
    a.to_ifc(new_file)

    return new_file


if __name__ == "__main__":
    app()

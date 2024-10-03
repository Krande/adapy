import pathlib

import typer

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.comms.procedures import procedure_decorator
from ada.config import Config

app = typer.Typer()

@procedure_decorator(app, export_file_type=FileTypeDC.IFC, export_file_var="output_file")
def main(
        name: str = 'floor1',
        origin: tuple[float, float, float] = (0, 0, 0),
        width: float = 40,
        length: float = 20,
        thickness: float = 0.01,
        output_file: pathlib.Path = None
) -> pathlib.Path:

    temp_dir = Config().websockets_server_temp_dir
    this_file_name = pathlib.Path(__file__).stem
    output_dir = temp_dir / "components" / this_file_name

    p = ada.Part(name) / ada.Plate("pl1", [(0, 0), (width, 0), (width, length), (0, length)], thickness, origin=origin)
    # The assembly level is to be discarded. Only the part is relevant for merging into another IFC file
    a = ada.Assembly("TempAssembly") / p
    output_dir.mkdir(parents=True, exist_ok=True)
    new_file = output_dir / f"{this_file_name}.ifc"
    if new_file.exists():
        new_file.unlink()
    a.to_ifc(new_file)

    return new_file


if __name__ == "__main__":
    app()

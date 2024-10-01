import typer

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.comms.procedures import procedure_decorator

app = typer.Typer()


@procedure_decorator(app, export_file_type=FileTypeDC.IFC)
def main(name: str, p0: tuple[float, float, float], width: float, length: float):
    p = ada.Part(name) / ada.Plate("pl1", [(0, 0), (width, 0), (width, length), (0, length)], 0.01, origin=p0)
    # The assembly level is to be discarded. Only the part is relevant for merging into another IFC file
    a = ada.Assembly("TempAssembly") / p
    a.to_ifc(f"{name}.ifc")





if __name__ == "__main__":
    app()
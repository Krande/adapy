import pathlib

import pandas as pd

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.procedural_modelling.procedures_base import ComponentDecorator, app


@ComponentDecorator(inputs=dict(csv_file=FileTypeDC.CSV), outputs=dict(output_file=FileTypeDC.IFC))
def make_basic_shapes_from_csv(output_file: pathlib.Path = None, csv_file: pathlib.Path = None) -> None:
    boxes = []
    if csv_file is not None:
        df = pd.read_csv(csv_file)
        for _, row in df.iterrows():
            p1 = (row["X"], row["Y"], row["Z"])
            p2 = (row["X"] + row["DX"], row["Y"] + row["DY"], row["Z"] + row["DZ"])
            name = str(row["ID"])
            box = ada.PrimBox(name, p1, p2)
            boxes.append(box)
    else:
        box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
        boxes.append(box)

    a = ada.Assembly("BasicShapes") / boxes
    a.to_ifc(output_file)


if __name__ == "__main__":
    app()

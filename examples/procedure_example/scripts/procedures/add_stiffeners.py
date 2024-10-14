import pathlib

import numpy as np

import ada
from ada.comms.fb_model_gen import FileTypeDC
from ada.procedural_modelling.procedures_base import app, ProcedureDecorator

THIS_FILE = pathlib.Path(__file__).resolve().absolute()


def add_stiffeners(pl: ada.Plate, spacing, stiffener_section) -> list[ada.Beam]:
    stiffeners = []

    points = np.asarray([n.p for n in pl.nodes])

    # For now only care for the global x and y coordinates
    # get the max length of the plate in the y direction
    max_y = np.max(points[:, 1])
    min_y = np.min(points[:, 1])
    length_y = max_y - min_y
    # get the max length of the plate in the x direction
    min_x = np.min(points[:, 0])
    max_x = np.max(points[:, 0])
    length_x = max_x - min_x

    z = points[0][0]
    if length_y > length_x:
        num_stiffeners = int(length_y / spacing)
        # Add stiffeners in the x direction
        beam_coords = [((min_x, i * spacing, z), (max_x, i * spacing, z)) for i in range(0, num_stiffeners)]
    else:
        num_stiffeners = int(length_x / spacing)
        # Add stiffeners in the y direction
        beam_coords = [((i * spacing, min_y, z), (i * spacing, max_y, z)) for i in range(0, num_stiffeners)]

    for i, (start, stop) in enumerate(beam_coords, start=1):
        # Create a beam with the same length as the plate
        beam = ada.Beam(f"{pl.name}_stiff_{i}", start, stop, sec=stiffener_section)
        stiffeners.append(beam)

    return stiffeners


@ProcedureDecorator(
    inputs=dict(input_file=FileTypeDC.IFC),
    outputs=dict(output_file=FileTypeDC.IFC),
    options={"hp_section": ["HP180x8", "HP200x10", "HP220x12"]},
)
def main(
    input_file: pathlib.Path = None,
    output_file: pathlib.Path = None,
    hp_section: str = "HP180x8",
    stiff_spacing: float = 1.0,
) -> None:
    """A procedure to add stiffeners to all plates in the IFC file"""

    a = ada.from_ifc(input_file)
    for pl in a.get_all_physical_objects(by_type=ada.Plate):
        pl.parent / add_stiffeners(pl, stiff_spacing, hp_section)

    a.to_ifc(output_file)


if __name__ == "__main__":
    app()

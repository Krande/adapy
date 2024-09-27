import numpy as np
import pathlib
import typer

import ada

app = typer.Typer()
THIS_FILE = pathlib.Path(__file__).resolve().absolute()


def add_stiffeners(pl: ada.Plate) -> list[ada.Beam]:
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

    spacing = 1
    z = points[0][0]
    stiffener_section = 'HP180x8'
    if length_y > length_x:
        num_stiffeners = int(length_y / spacing)
        # Add stiffeners in the x direction
        beam_coords = [((min_x, i, z), (max_x, i, z)) for i in range(0, num_stiffeners)]
    else:
        num_stiffeners = int(length_x / spacing)
        # Add stiffeners in the y direction
        beam_coords = [((i, min_y, z), (i, max_y, z)) for i in range(0, num_stiffeners)]

    for i, (start, stop) in enumerate(beam_coords, start=1):
        # Create a beam with the same length as the plate
        beam = ada.Beam(f"{pl.name}_stiff_{i}", start, stop, sec=stiffener_section)
        stiffeners.append(beam)

    return stiffeners


@app.command()
def main(ifc_file: pathlib.Path) -> pathlib.Path:
    a = ada.from_ifc(ifc_file)
    for pl in a.get_all_physical_objects(by_type=ada.Plate):
        new_stiffeners = add_stiffeners(pl)
        pl.parent / new_stiffeners

    modified_ifc_file = (ifc_file.parent / "procedural" / ifc_file.stem / THIS_FILE.stem).with_suffix(".ifc")
    modified_ifc_file.parent.mkdir(parents=True, exist_ok=True)
    a.to_ifc(modified_ifc_file)

    return modified_ifc_file


if __name__ == '__main__':
    app()

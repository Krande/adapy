from __future__ import annotations

import os
import pathlib

import ifcopenshell
import ifcopenshell.draw as draw


def create_svg_from_drawings(files: list[str], svg_output: str | pathlib.Path):
    settings = draw.draw_settings()
    if isinstance(svg_output, str):
        svg_output = pathlib.Path(svg_output)

    result = draw.main(settings, [ifcopenshell.open(f) for f in files], merge_projection=True)

    os.makedirs(svg_output.parent, exist_ok=True)
    with open(svg_output, "wb") as f:
        f.write(result)

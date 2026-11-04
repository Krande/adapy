"""``use_quads=True`` meshes a plate of any outline.

It made every plate a transfinite surface. gmsh accepts that setting on any surface and then fails the whole mesh in
``generate()`` -- "Surface 1 is transfinite but has 6 corners" -- so one L-shaped plate cost the model its mesh. Found
meshing a param_models bulkhead topside. A four-sided plate is still transfinite; any other is recombined into a
quad-dominant mesh.
"""

from __future__ import annotations

import ada
from ada.fem.shapes.definitions import ShellShapes


def _shell_types(fem) -> dict:
    out = {}
    for el in fem.elements:
        out[el.type] = out.get(el.type, 0) + 1
    return out


def test_l_shaped_plate_meshes_quad_dominant():
    outline = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    pl = ada.Plate("L", outline, 0.01)

    fem = pl.to_fem_obj(0.1, "shell", use_quads=True)

    types = _shell_types(fem)
    assert types.get(ShellShapes.QUAD, 0) > 0
    assert types.get(ShellShapes.QUAD, 0) > 10 * types.get(ShellShapes.TRI, 0)


def test_rectangular_plate_is_still_all_quads():
    pl = ada.Plate("R", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01)

    fem = pl.to_fem_obj(0.1, "shell", use_quads=True)

    assert set(_shell_types(fem)) == {ShellShapes.QUAD}

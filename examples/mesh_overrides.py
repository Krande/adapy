"""Mesh overrides: per-object meshing rules (docs/meshing.md).

The docs page quotes the sections between the snippet markers and embeds each `*_figure` mesh as a 3D view
(scripts/docs/docs_notebooks.py, FIGURES).
"""

import ada
from ada.fem.meshing import GmshSession
from ada.fem.meshing.overrides import boolean_curves, refine_curves


# --8<-- [start:overrides]
def refine_holes(gs: GmshSession, obj, geom_repr) -> None:
    """Elements 5x finer along every cutout of `obj`, graded back to the mesh size over 3 element lengths."""
    curves = boolean_curves(gs, obj)  # the curves each cutout left behind
    if not curves:
        return
    size = gs.options.Mesh_MeshSizeMax  # the size this mesh is made at
    refine_curves(gs, curves, size=size / 5, max_size=size, dist_min=size / 5, dist_max=3 * size)


def half_size(gs: GmshSession, obj, geom_repr) -> None:
    """Half the mesh size on `obj` and its boundary; neighbours grade to it along shared edges."""
    size = gs.options.Mesh_MeshSizeMax / 2
    field = gs.model.mesh.field
    constant = field.add("Constant")
    field.setNumber(constant, "VIn", size)
    field.setNumbers(constant, "SurfacesList", [tag for dim, tag in gs.model_map[obj].entities if dim == 2])
    field.setNumber(constant, "IncludeBoundary", 1)
    gs.add_size_field(constant, size)


# --8<-- [end:overrides]

# --8<-- [start:plate]
pl = ada.Plate("pl1", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01)
pl.add_boolean(ada.PrimCyl("hole1", (0.5, 0.5, -0.1), (0.5, 0.5, 0.1), 0.15))
pl.add_boolean(ada.PrimCyl("hole2", (1.4, 0.5, -0.1), (1.4, 0.5, 0.1), 0.2))
pl.mesh_override = refine_holes

plate_before = pl.to_fem_obj(0.1, "shell", use_quads=True, use_mesh_override=False)
plate_after = pl.to_fem_obj(0.1, "shell", use_quads=True)
# --8<-- [end:plate]

# --8<-- [start:beam]
bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE400")
bm.add_boolean(ada.PrimCyl("web_hole", (1.0, -0.5, 0.0), (1.0, 0.5, 0.0), 0.1))
bm.mesh_override = refine_holes

beam_before = bm.to_fem_obj(0.05, "shell", use_quads=True, use_mesh_override=False)
beam_after = bm.to_fem_obj(0.05, "shell", use_quads=True)
# --8<-- [end:beam]

# --8<-- [start:part]
deck = ada.Plate("deck", [(0, 0), (3, 0), (3, 2), (0, 2)], 0.01)
deck.add_boolean(ada.PrimCyl("hole", (2.2, 0.5, -0.1), (2.2, 0.5, 0.1), 0.2))
deck.mesh_override = refine_holes
girder = ada.Beam("girder", (0, 1, 0), (3, 1, 0), "IPE300")  # its web crosses the deck along y=1
girder.mesh_override = half_size

p = ada.Part("p") / [deck, girder]
part_before = p.to_fem_obj(0.1, bm_repr="shell", pl_repr="shell", use_quads=True, use_mesh_override=False)
part_after = p.to_fem_obj(0.1, bm_repr="shell", pl_repr="shell", use_quads=True)
# --8<-- [end:part]


def plate_before_figure():
    return plate_before


def plate_after_figure():
    return plate_after


def beam_before_figure():
    return beam_before


def beam_after_figure():
    return beam_after


def part_before_figure():
    return part_before


def part_after_figure():
    return part_after


if __name__ == "__main__":
    for name in ("plate", "beam", "part"):
        before, after = globals()[f"{name}_before"], globals()[f"{name}_after"]
        print(f"{name}: {len(before.elements)} -> {len(after.elements)} elements")

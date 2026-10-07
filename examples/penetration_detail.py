"""A pipe through a shared wall, and a penetration detail written as one function.

Two cells share one wall; a pipe routed from a pump in one cell to a tank in the other has
to cross it. The detail function cuts a circular hole in the wall and reinforces it with a
thin-walled sleeve. Shown on docs/procedural_modelling.md, which includes the code below.

    python examples/penetration_detail.py     # opens the model in the viewer

The docs page embeds ``figure()`` as an interactive 3D view (scripts/docs_notebooks.py).
"""

# --8<-- [start:model]
import ada
from ada.topo_model import SteelStru, create_pump, create_tank
from ada.topology import CellGrid, DesignRules, TopologyBuilder, run_design

# Two 5 x 5 x 3 m cells sharing one (stiffened) wall at x = 5
cells = [ada.PrimBox("Cell1", (0, 0, 0), (5, 5, 3)), ada.PrimBox("Cell2", (5, 0, 0), (10, 5, 3))]
builder = TopologyBuilder.from_prim_boxes(cells, blueprint=SteelStru(reinforce_internal_walls=True))
structure = builder.build()

# A pump in one cell, a tank in the other, and a pipe between them
pump = create_pump("Pump", origin=(2.5, 2.5, 0.0))
tank = create_tank("Tank", origin=(7.5, 2.5, 0.0))
pipe = ada.PipingSystem("Service", medium="water").connect(pump, "discharge").connect(tank, "inlet")


# The penetration detail: a circular cut through the wall, reinforced with a thin-walled sleeve
def penetration_detail(pen, name, clearance=0.02, wall_thickness=0.008, length=0.3):
    r = pen.system.pipe_radius + clearance
    p1, p2 = pen.point - pen.normal * length / 2, pen.point + pen.normal * length / 2

    for plate in pen.face.associated_part.get_all_physical_objects(by_type=ada.Plate):
        plate.add_boolean(ada.PrimCyl(f"{name}_cut", p1, p2, r))

    sleeve = ada.PrimCyl(f"{name}_sleeve", p1, p2, r + wall_thickness, color="red")
    sleeve.add_boolean(ada.PrimCyl(f"{name}_bore", p1, p2, r))
    return ada.Part(name) / sleeve


# Route the pipe through the cells, find where it crosses a wall, and detail each crossing
grid = CellGrid.from_bounds((0, 0, 0), (10, 5, 3), spacing=0.5)
rules = DesignRules(model_penetration=penetration_detail)
result = run_design([pipe], cell_graph=builder.cell_graph, grid=grid, rules=rules)

piping = ada.Part("Piping") / result.route_geometry[pipe.name]
a = ada.Assembly("Penetration") / [structure, ada.Part("Equipment") / [pump, tank], piping, *result.penetration_parts]
# --8<-- [end:model]


def figure() -> ada.Assembly:
    """What the docs figure shows: the crossed wall, the equipment, the pipe and the detail,
    without the roof deck in the way of the default camera."""
    wall = result.penetrations[0].face.associated_part
    return ada.Assembly("PenetrationDetail") / [
        ada.Part("Wall") / list(wall.get_all_physical_objects()),
        ada.Part("Equipment") / [pump, tank],
        ada.Part("Piping") / result.route_geometry[pipe.name],
        *result.penetration_parts,
    ]


if __name__ == "__main__":
    a.show()

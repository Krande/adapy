"""Box impact on a single procedural steel cell, solved with OpenCourant (explicit dynamics).

* Structure: one ``SteelStru`` topo_model cell (columns, girders, stiffened floor and
  roof decks) meshed SHELL-ONLY — every beam becomes web/flange shells, fragmented
  against the deck plates so the joints are conformal.
* Impactor: a closed shell box dropped onto the roof with an initial velocity.
* Contact: symmetric shell-to-shell contact between box and structure.
* Supports: column feet (all nodes at the lowest level) fixed in all DOFs.

Run (needs the ``opencourant`` conda package on PATH):

    pixi run -e fem python examples/opencourant_cell_impact.py

Results land in ``temp/opencourant_cell_impact/``; ``cell_impact.radanim`` is the
single file to upload to the viewer (it streams as a time-stepped FEA result).
"""

from __future__ import annotations

import pathlib

import numpy as np

import ada
from ada.fem import FemSet, Interaction, InteractionProperty, PredefinedField, Surface
from ada.fem.interactions import ContactTypes
from ada.fem.meshing import mesh_shell_bodies
from ada.fem.steps import StepExplicit
from ada.materials.metals import Metal
from ada.topo_model import SteelStru
from ada.topology import TopologyBuilder

NAME = "cell_impact"
SCRATCH = pathlib.Path("temp") / "opencourant_cell_impact"

CELL = ((0.0, 0.0, 0.0), (5.0, 5.0, 3.0))
MESH_SIZE = 0.1

BOX_SIZE = (1.4, 1.4, 0.8)  # x, y, z [m]
BOX_THICK = 0.02
BOX_DENSITY = 2 * 7850.0  # a heavy, stiff impactor (~2.6 t)
BOX_GAP = 0.02  # clearance above the highest structural point
IMPACT_VELOCITY = 8.0  # m/s, downwards (~84 kJ)

T_END = 0.06
OUTPUT_INTERVAL = 0.0015


def build_cell() -> list:
    builder = TopologyBuilder.from_prim_boxes(
        [ada.PrimBox("Cell1", *CELL)],
        blueprint=SteelStru(girder_sec="IPE200", column_sec="HEB200", stringer_sec="HP140x8"),
    )
    builder.build()
    return list(builder.get_output_assembly("Cell").get_all_physical_objects())


def build_box(top_of_structure: float) -> list[ada.Plate]:
    """Six plates forming a closed box centred over the cell, bottom face just above the roof."""
    mat = ada.Material(
        "Impactor", Metal(E=210e9, rho=BOX_DENSITY, sig_y=None, sig_u=None, v=0.3, zeta=0.0, alpha=1.2e-5)
    )
    (x0, y0, _), (x1, y1, _) = CELL
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy, dz = (s / 2 for s in BOX_SIZE)
    z0 = top_of_structure + BOX_GAP + BOX_THICK / 2
    z1 = z0 + BOX_SIZE[2]
    xa, xb, ya, yb = cx - dx, cx + dx, cy - dy, cy + dy
    faces = {
        "bottom": [(xa, ya, z0), (xb, ya, z0), (xb, yb, z0), (xa, yb, z0)],
        "top": [(xa, ya, z1), (xb, ya, z1), (xb, yb, z1), (xa, yb, z1)],
        "south": [(xa, ya, z0), (xb, ya, z0), (xb, ya, z1), (xa, ya, z1)],
        "north": [(xa, yb, z0), (xb, yb, z0), (xb, yb, z1), (xa, yb, z1)],
        "west": [(xa, ya, z0), (xa, yb, z0), (xa, yb, z1), (xa, ya, z1)],
        "east": [(xb, ya, z0), (xb, yb, z0), (xb, yb, z1), (xb, ya, z1)],
    }
    return [ada.Plate.from_3d_points(f"Box_{k}", pts, BOX_THICK, mat=mat) for k, pts in faces.items()]


def top_z(objs) -> float:
    zmax = -np.inf
    for obj in objs:
        bbox = obj.bbox()
        zmax = max(zmax, float(bbox.p2[2]))
    return zmax


def main(execute: bool = True, cpus: int = 8):
    structure = build_cell()
    box = build_box(top_z(structure))

    fem = mesh_shell_bodies([structure, box], MESH_SIZE, name="CellImpact")
    box_objs = set(box)
    box_els = [el for el in fem.elements if el.refs and el.refs[0] in box_objs]
    stru_els = [el for el in fem.elements if not (el.refs and el.refs[0] in box_objs)]

    # Object bounding boxes overestimate the roof height (section envelopes), so close the
    # gap on the mesh itself: drop the impactor until it hovers BOX_GAP above the structure
    # beneath its footprint.
    box_node_set = {n for el in box_els for n in el.nodes}
    bx = [n.x for n in box_node_set]
    by = [n.y for n in box_node_set]
    under_box = [n.z for el in stru_els for n in el.nodes if min(bx) <= n.x <= max(bx) and min(by) <= n.y <= max(by)]
    stru_top = max(under_box)
    box_bottom = min(n.z for n in box_node_set)
    drop = box_bottom - (stru_top + BOX_GAP)
    for n in box_node_set:
        n.p = ada.Point(n.x, n.y, n.z - drop)
    z_min = min(n.z for n in fem.nodes)
    feet = [n for n in fem.nodes if abs(n.z - z_min) < 1e-6]
    print(
        f"mesh: {len(fem.nodes)} nodes, {len(stru_els)} structure + {len(box_els)} impactor shells, {len(feet)} fixed nodes"
    )

    part = ada.Part("CellImpact")
    part.fem = fem
    a = ada.Assembly("CellImpactModel") / part

    box_set = fem.add_set(FemSet("impactor_els", box_els, FemSet.TYPES.ELSET))
    stru_set = fem.add_set(FemSet("structure_els", stru_els, FemSet.TYPES.ELSET))
    box_nodes = fem.add_set(FemSet("impactor_nodes", list(box_node_set), FemSet.TYPES.NSET))
    feet_set = fem.add_set(FemSet("column_feet", feet, FemSet.TYPES.NSET))

    fem.add_bc(ada.fem.Bc("fixed_feet", feet_set, [1, 2, 3, 4, 5, 6]))
    fem.add_predefined_field(
        PredefinedField(
            "impact_velocity", PredefinedField.TYPES.VELOCITY, box_nodes, dofs=[3], magnitude=[-IMPACT_VELOCITY]
        )
    )
    s_box = fem.add_surface(Surface("impactor", Surface.TYPES.ELEMENT, box_set))
    s_stru = fem.add_surface(Surface("structure", Surface.TYPES.ELEMENT, stru_set))
    contact = InteractionProperty("steel_on_steel", friction=0.2)
    fem.add_interaction(Interaction("box_structure", ContactTypes.SURFACE, s_stru, s_box, contact))

    a.fem.add_step(StepExplicit("impact", total_time=T_END, output_interval=OUTPUT_INTERVAL))

    res = a.to_fem(NAME, "opencourant", scratch_dir=SCRATCH, overwrite=True, execute=execute, cpus=cpus)
    print("radanim:", (SCRATCH / NAME / f"{NAME}.radanim").resolve())
    return res


if __name__ == "__main__":
    main()

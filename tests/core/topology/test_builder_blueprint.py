"""Phase 6: the generic TopologyBuilder + BlueprintBase plumbing.

A trivial blueprint turns each cell into a part (one plate per external face)
and registers it under the cell's area; the builder runs the lifecycle and folds
the area map into the output part.
"""

from __future__ import annotations

import ada
from ada.topology import BlueprintBase, TopologyBuilder
from ada.topology.entities import TopoSpace


class _PlateBlueprint(BlueprintBase):
    """Minimal blueprint: each external face becomes a thin plate, grouped by area."""

    def _group_prefix(self) -> str:
        return "S"

    def build(self) -> ada.Part:
        self.output_part = ada.Part("out")
        cg = self.builder.cell_graph
        for face in cg.get_external_faces():
            area = face.get_area_name() or "NoArea"
            plate = ada.Plate.from_3d_points(face.name, face.get_points(), 0.01)
            self.area_map.setdefault(area, []).append(ada.Part(face.name) / plate)
        self.load_parts_from_area_map()
        return self.output_part


def _boxes():
    return [
        ada.PrimBox("RoomA", (0, 0, 0), (1, 1, 1), metadata={"NAME": "RoomA"}),
        ada.PrimBox("RoomB", (1, 0, 0), (2, 1, 1), metadata={"NAME": "RoomB"}),
    ]


def test_builder_runs_blueprint_lifecycle():
    builder = TopologyBuilder.from_prim_boxes(_boxes(), blueprint=_PlateBlueprint())
    # wiring both ways
    assert builder.blueprint.builder is builder
    assert builder.cell_graph.builder is builder

    out = builder.build()
    assert isinstance(out, ada.Part)
    # 10 external faces -> 10 plates across the area parts.
    plates = list(out.get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) == 10


def test_blueprint_area_grouping_and_output_assembly():
    # Drive areas via TopoSpace metadata so the area grouping is exercised.
    from ada.topology import CellGraph

    pairs = [
        (
            ada.PrimBox("RoomA", (0, 0, 0), (1, 1, 1)).solid_occ(),
            TopoSpace(NAME="RoomA", X=0, Y=0, Z=0, DX=1, DY=1, DZ=1, AREA="DeckA"),
        ),
        (
            ada.PrimBox("RoomB", (1, 0, 0), (2, 1, 1)).solid_occ(),
            TopoSpace(NAME="RoomB", X=1, Y=0, Z=0, DX=1, DY=1, DZ=1, AREA="DeckB"),
        ),
    ]
    cg = CellGraph.from_cell_solids(pairs, merge=True)
    builder = TopologyBuilder(blueprint=_PlateBlueprint(), cell_graph=cg)
    out = builder.build()

    # Two area parts named after the TopoSpace AREA values.
    area_parts = {p.name for p in out.get_all_parts_in_assembly()}
    assert {"DeckA", "DeckB"} <= area_parts

    # Output assembly caches and wraps the blueprint output part.
    a = builder.get_output_assembly("MyModel")
    assert a.name == "MyModel"
    assert builder.get_output_assembly() is a

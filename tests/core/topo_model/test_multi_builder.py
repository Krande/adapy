"""ProceduralBuilder multi-structure support: several topology models, grouped
by STRUCTURE_NAME and placed at their origins, with one shared equipment/systems
layer (no per-structure duplication)."""

from __future__ import annotations

from ada.topo_model import ProceduralBuilder

DOC = {
    "structures": [
        {"NAME": "S1", "X": 0, "Y": 0, "Z": 0},
        {"NAME": "S2", "X": 20, "Y": 0, "Z": 0},
    ],
    "spaces": [
        {"NAME": "S1_Cell1", "STRUCTURE_NAME": "S1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "S1_Cell2", "STRUCTURE_NAME": "S1", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "S2_Cell1", "STRUCTURE_NAME": "S2", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
}


def _is_glb(b: bytes) -> bool:
    return b[:4] == b"glTF"


def test_structures_parsed_and_placed():
    pb = ProceduralBuilder.from_dict(DOC)
    assert [s.NAME for s in pb.structures] == ["S1", "S2"]
    assert {s.NAME: s.origin() for s in pb.structures} == {"S1": (0.0, 0.0, 0.0), "S2": (20.0, 0.0, 0.0)}


def test_build_groups_topologies_by_structure():
    pb = ProceduralBuilder.from_dict(DOC)
    pb.build_structure()
    assert set(pb.topologies) == {"S1", "S2"}
    # the primary (first) topology is also exposed as .topology / .cell_graph
    assert pb.topology is pb.topologies["S1"]
    assert pb.cell_graph is not None
    # each structure's cell graph only saw its own spaces (2 for S1, 1 for S2)
    assert len(pb.topologies["S1"].cell_graph.get_external_floors()) > len(
        pb.topologies["S2"].cell_graph.get_external_floors()
    )


def test_placed_structures_wrapped_in_assembly():
    pb = ProceduralBuilder.from_dict(DOC)
    pb.build_structure()
    parts = pb.assembly.parts
    assert {"S1", "S2"} <= set(parts)
    # S2 carries its placement origin
    s2 = parts["S2"]
    assert tuple(float(v) for v in s2.placement.origin) == (20.0, 0.0, 0.0)


def test_multi_structure_compiles():
    assert _is_glb(ProceduralBuilder.from_dict(DOC).compile())


def test_excluded_structure_skipped():
    pb = ProceduralBuilder.from_dict(DOC)
    pb.structures[1].INCLUDE = False  # exclude S2
    pb.build_structure()
    assert set(pb.topologies) == {"S1"}


def test_no_structures_is_single_unchanged():
    doc = {"spaces": [{"NAME": "C1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}]}
    pb = ProceduralBuilder.from_dict(doc, name="Solo")
    assert pb.structures == []
    pb.build_structure()
    assert pb.topology is not None and set(pb.topologies) == {"Solo"}
    assert _is_glb(ProceduralBuilder.from_dict(doc, name="Solo").compile())


def test_to_doc_roundtrips_structures():
    pb = ProceduralBuilder.from_dict(DOC)
    back = ProceduralBuilder.from_dict(pb.to_doc())
    assert [s.NAME for s in back.structures] == ["S1", "S2"]
    assert [s.STRUCTURE_NAME for s in back.spaces] == ["S1", "S1", "S2"]


def test_excel_roundtrip_multi(tmp_path):
    pb = ProceduralBuilder.from_dict(DOC)
    xlsx = tmp_path / "multi.xlsx"
    pb.to_excel(xlsx)
    back = ProceduralBuilder.from_excel(xlsx)
    assert {s.NAME for s in back.structures} == {"S1", "S2"}
    assert {s.NAME for s in back.spaces} == {"S1_Cell1", "S1_Cell2", "S2_Cell1"}
    # STRUCTURE_NAME survives the round-trip so the regroup on build works
    assert {s.NAME: s.STRUCTURE_NAME for s in back.spaces} == {
        "S1_Cell1": "S1",
        "S1_Cell2": "S1",
        "S2_Cell1": "S2",
    }
    assert _is_glb(back.compile())

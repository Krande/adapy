"""The quantity take-off that rides inside the GLB (``asset.extras.model_stats``).

The hosted viewer gets its Stats panel from a ``.stats.json`` sidecar written by
the REST compile worker. The local ``assembly.show()`` path has no server, so
without this the panel is structurally empty for every locally shown model. The
take-off is therefore embedded in the GLB itself, where the frontend's websocket
stats capability reads it back out.
"""

from __future__ import annotations

import json
import struct

import pytest
import trimesh

import ada
from ada.visit.render_params import RenderParams
from ada.visit.scene_converter import SceneConverter


def glb_json(glb: bytes) -> dict:
    """The JSON chunk of a binary glTF."""
    assert glb[:4] == b"glTF"
    (json_len,) = struct.unpack("<I", glb[12:16])
    return json.loads(glb[20 : 20 + json_len].decode("utf-8"))


def asset_extras(glb: bytes) -> dict:
    return (glb_json(glb).get("asset") or {}).get("extras") or {}


@pytest.fixture
def small_assembly() -> ada.Assembly:
    bm1 = ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300")
    bm2 = ada.Beam("bm2", (5, 0, 0), (5, 5, 0), "IPE300")
    pl = ada.Plate("pl1", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01, origin=(0, 0, 1))
    return ada.Assembly("StatsDemo") / (ada.Part("Deck") / [bm1, bm2, pl])


def test_glb_carries_the_take_off_for_a_part_source(small_assembly):
    extras = asset_extras(SceneConverter(source=small_assembly).build_glb())

    stats = extras.get("model_stats")
    assert stats is not None, "a locally shown model must carry its own take-off"
    assert stats["total_mass"] > 0
    assert stats["objects"] == 3
    assert stats["source_name"] == "StatsDemo"
    # The document the viewer's panel expects, identical in shape to the REST
    # sidecar (ada.topo_model.takeoff.model_takeoff).
    assert {"schema_version", "disciplines", "total_cog", "structural", "piping"} <= set(stats)
    structural = next(d for d in stats["disciplines"] if d["key"] == "structural")
    assert structural["mass"] > 0
    assert structural["count"] == 3


def test_take_off_is_a_small_addition_to_the_payload(small_assembly):
    # It is metadata riding along with triangles; keep it obviously cheap so
    # nobody has to think about turning it off.
    stats = asset_extras(SceneConverter(source=small_assembly).build_glb())["model_stats"]
    assert len(json.dumps(stats)) < 20_000


def test_embed_model_stats_false_leaves_the_glb_untouched(small_assembly):
    params = RenderParams(embed_model_stats=False)
    assert "model_stats" not in asset_extras(SceneConverter(source=small_assembly, params=params).build_glb())


def test_explicit_asset_extras_still_win(small_assembly):
    # An explicitly supplied extras dict must not be clobbered by the take-off,
    # and must still be able to override it.
    params = RenderParams(gltf_asset_extras_dict={"custom": 1})
    extras = asset_extras(SceneConverter(source=small_assembly, params=params).build_glb())
    assert extras["custom"] == 1
    assert "model_stats" in extras

    params = RenderParams(gltf_asset_extras_dict={"model_stats": None})
    extras = asset_extras(SceneConverter(source=small_assembly, params=params).build_glb())
    assert extras["model_stats"] is None


def test_non_part_sources_get_no_take_off():
    # A raw trimesh scene / FEA result has no structured model to take off from.
    scene = trimesh.Scene(trimesh.creation.box(extents=(1, 1, 1)))
    converter = SceneConverter(source=scene)
    assert converter.build_model_stats() is None
    assert "model_stats" not in asset_extras(converter.build_glb())


def test_a_failing_take_off_never_breaks_the_render(small_assembly, monkeypatch):
    # Statistics are a nicety; a raising take-off must degrade to "no stats",
    # not to "no model".
    import ada.topo_model.takeoff as takeoff_mod

    def boom(*args, **kwargs):
        raise RuntimeError("take-off exploded")

    monkeypatch.setattr(takeoff_mod, "model_takeoff", boom)

    glb = SceneConverter(source=small_assembly).build_glb()
    assert len(glb) > 0
    assert "model_stats" not in asset_extras(glb)


def test_the_take_off_is_computed_once_per_converter(small_assembly):
    converter = SceneConverter(source=small_assembly)
    calls = {"n": 0}
    import ada.topo_model.takeoff as takeoff_mod

    real = takeoff_mod.model_takeoff

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    takeoff_mod.model_takeoff = counting
    try:
        converter.build_glb()
        converter.build_model_stats()
    finally:
        takeoff_mod.model_takeoff = real

    assert calls["n"] == 1


# -- the procedural document ----------------------------------------------------------------------
#
# The viewer's "Procedural equipment"/"Procedural system" panels read the cellbuilder store, which
# was only ever filled by a REST call. On the websocket path they rendered nothing, even though the
# document that answers them is what the assembly was compiled from. It rides in the GLB now.


def _procedural_assembly() -> ada.Assembly:
    """A tiny procedural model, compiled the way any caller would."""
    from ada.topo_model.compile import build_procedural_assembly

    doc = {
        "spaces": [{"NAME": "Deck1", "X": 0, "Y": 0, "Z": 0, "DX": 12, "DY": 8, "DZ": 4}],
        "equipments": [
            {
                "NAME": "P-101",
                "DESCRIPTION": "pump",
                "SPACE_NAME": "Deck1",
                "SPACE_LOC": "FLOOR",
                "X": 2,
                "Y": 2,
                "Z": 0,
                "LX": 1,
                "LY": 1,
                "LZ": 1,
                "COGx": 0,
                "COGy": 0,
                "COGz": 0.5,
                "massDry": 100,
                "massCont": 0,
            }
        ],
        "systems": [],
    }
    return build_procedural_assembly(doc, name="ProceduralModel")


def test_the_compiler_keeps_the_document_it_built_from():
    """Stamped at the one entry every procedural build goes through, so it is not a favour to any
    one importer."""
    assembly = _procedural_assembly()

    doc = assembly.metadata["procedural_doc"]
    assert [row["NAME"] for row in doc["equipments"]] == ["P-101"]


def test_glb_carries_the_procedural_document():
    """What the panels need -- which equipment a body belongs to, its space, size and masses -- is
    only in the document; the GLB is triangles and names."""
    glb = SceneConverter(_procedural_assembly(), RenderParams()).build_glb()

    doc = asset_extras(glb).get("procedural_doc")
    assert doc is not None, "the panels have nothing to read"
    assert [row["NAME"] for row in doc["equipments"]] == ["P-101"]
    assert doc["equipments"][0]["SPACE_NAME"] == "Deck1"


def test_embed_procedural_doc_false_leaves_it_out():
    glb = SceneConverter(_procedural_assembly(), RenderParams(embed_procedural_doc=False)).build_glb()

    assert "procedural_doc" not in asset_extras(glb)


def test_an_assembly_with_no_procedural_provenance_carries_no_document(small_assembly):
    """An IFC import or a hand-built model has none, and must not grow an empty one."""
    glb = SceneConverter(small_assembly, RenderParams()).build_glb()

    assert "procedural_doc" not in asset_extras(glb)


def test_a_document_that_will_not_serialise_never_breaks_the_render(small_assembly):
    """Panels are a nicety; a render is not allowed to fail for one."""

    class Unserialisable:
        pass

    small_assembly.metadata["procedural_doc"] = {"equipments": [Unserialisable()]}

    glb = SceneConverter(small_assembly, RenderParams()).build_glb()

    # It renders, and the un-encodable document is simply absent rather than half-written.
    assert glb_json(glb) is not None

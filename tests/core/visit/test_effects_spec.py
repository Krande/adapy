"""Visual effects as plain data in the GLB's scene extras (``ada.visit.gltf.effects``).

The native viewport hands these extras to the adacpp renderer as they are. So the JSON written
here must carry the renderer's keys (camelCase) and leave out whatever was not set, so the
renderer's own defaults apply.
"""

from __future__ import annotations

import json
import struct

import pytest

import ada
from ada.visit.gltf.effects import (
    EXTRAS_KEY,
    EffectSpec,
    EmissiveRamp,
    Emitter,
    EnvironmentSettings,
    PointLight,
    PostSettings,
)
from ada.visit.render_params import RenderParams
from ada.visit.scene_converter import SceneConverter


def glb_json(glb: bytes) -> dict:
    assert glb[:4] == b"glTF"
    (json_len,) = struct.unpack("<I", glb[12:16])
    return json.loads(glb[20 : 20 + json_len].decode("utf-8"))


def spec() -> EffectSpec:
    return EffectSpec(
        lights=[PointLight(position=(1, 2, 3), intensity=4.0, flicker=0.5, flicker_rate=12, seed=7, start=1, end=9)],
        emitters=[
            Emitter(origin=(1, 2, 3), direction=(0, 0, 1), rate=300, start=1, end=9),
            Emitter(origin=(1, 2, 3.2), preset="smoke", start=1),
        ],
        emissive=[EmissiveRamp(targets=["bm1"], hot_intensity=10, start=9, duration=6, curve=0.5)],
        post=PostSettings(tone_mapping="aces", bloom=True),
        environment=EnvironmentSettings(intensity=0.8),
    )


def test_keys_are_the_renderers_and_unset_fields_are_left_out():
    d = spec().to_dict()
    assert d["version"] == 1
    light = d["lights"][0]
    assert light == {
        "position": [1.0, 2.0, 3.0],
        "intensity": 4.0,
        "flicker": 0.5,
        "flickerRate": 12,
        "seed": 7,
        "start": 1,
        "end": 9,
    }
    assert d["emitters"][1] == {"origin": [1.0, 2.0, 3.2], "preset": "smoke", "start": 1}
    assert d["emissive"][0] == {"targets": ["bm1"], "hotIntensity": 10, "start": 9, "duration": 6, "curve": 0.5}
    assert d["post"] == {"hdr": True, "toneMapping": "aces", "bloom": True}
    assert d["environment"] == {"enabled": True, "intensity": 0.8}
    json.dumps(d)  # plain JSON all the way down


def test_round_trip():
    s = spec()
    back = EffectSpec.from_dict(json.loads(json.dumps(s.to_dict())))
    assert back == s


def test_unknown_keys_are_ignored_and_newer_versions_refused():
    d = spec().to_dict()
    d["lights"][0]["somethingNew"] = 1
    assert EffectSpec.from_dict(d).lights[0].intensity == 4.0
    d["version"] = 99
    with pytest.raises(ValueError, match="version"):
        EffectSpec.from_dict(d)


@pytest.mark.parametrize(
    "make",
    [
        lambda: PointLight(position=(1, 2)),
        lambda: PostSettings(tone_mapping="filmic"),
        lambda: Emitter(blend="sideways"),
        lambda: Emitter(preset="fire"),
        lambda: Emitter(rate=-1),
        lambda: EmissiveRamp(hot_color=(1, 0)),
    ],
)
def test_invalid_values_are_refused(make):
    with pytest.raises(ValueError):
        make()


def test_tree_round_trip_keeps_other_extras():
    tree = {"scene": 0, "scenes": [{"nodes": [0], "extras": {"id_hierarchy": {"a": ["a", "*"]}}}]}
    spec().write_to_gltf_tree(tree)
    extras = tree["scenes"][0]["extras"]
    assert set(extras) == {"id_hierarchy", EXTRAS_KEY}
    assert EffectSpec.from_gltf_tree(tree) == spec()
    assert EffectSpec.from_gltf_tree({"scenes": [{}]}) is None


def test_scene_converter_writes_the_effects_beside_the_object_ids():
    bm1 = ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300")
    assembly = ada.Assembly("Effects") / (ada.Part("P") / [bm1])
    effects = spec()
    effects.emissive = [EmissiveRamp(targets=[bm1], duration=3)]  # an object: its guid
    glb = SceneConverter(source=assembly, params=RenderParams(effects=effects)).build_glb()
    tree = glb_json(glb)
    scene = tree["scenes"][tree.get("scene", 0)]
    extras = scene["extras"]
    written = extras[EXTRAS_KEY]
    assert written["lights"] == effects.to_dict()["lights"]
    # The emissive target is the object's id in the hierarchy the viewer resolves it against.
    (target,) = written["emissive"][0]["targets"]
    assert extras["id_hierarchy"][target][0] == "bm1"


def test_no_effects_writes_nothing():
    bm1 = ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300")
    assembly = ada.Assembly("Plain") / (ada.Part("P") / [bm1])
    for params in (RenderParams(), RenderParams(effects=EffectSpec())):
        tree = glb_json(SceneConverter(source=assembly, params=params).build_glb())
        assert EXTRAS_KEY not in (tree["scenes"][tree.get("scene", 0)].get("extras") or {})

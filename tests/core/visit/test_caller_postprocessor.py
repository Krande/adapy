"""A caller's gltf postprocessor survives SceneConverter.

``RenderParams.set_gltf_*_postprocessor`` is the only public way to hook the
glTF export, and ``SceneConverter.build_scene`` takes those slots over for its
own postprocessors. The caller's must still run -- and ONLY the caller's: a
converter's own bound method left in a reused ``RenderParams`` is not a
caller's hook and must not be chained into the next render.
"""

from __future__ import annotations

import json
import struct

import pytest

import ada
from ada.visit.render_params import RenderParams
from ada.visit.scene_converter import SceneConverter


def glb_json(glb: bytes) -> dict:
    assert glb[:4] == b"glTF"
    (json_len,) = struct.unpack("<I", glb[12:16])
    return json.loads(glb[20 : 20 + json_len].decode("utf-8"))


@pytest.fixture
def beam_assembly() -> ada.Assembly:
    return ada.Assembly("PostDemo") / (ada.Part("P") / ada.Beam("bm1", (0, 0, 0), (2, 0, 0), "IPE200"))


def test_a_caller_set_tree_postprocessor_is_chained_into_the_glb(beam_assembly):
    calls: list[str] = []

    def stamp(tree):
        calls.append("tree")
        tree.setdefault("asset", {}).setdefault("extras", {})["caller_stamp"] = "seen"

    def stamp_buffer(buffer_items, tree):
        calls.append("buffer")

    params = RenderParams()
    params.set_gltf_tree_postprocessor(stamp)
    params.set_gltf_buffer_postprocessor(stamp_buffer)

    glb = SceneConverter(source=beam_assembly, params=params).build_glb()

    assert calls == ["buffer", "tree"]
    extras = glb_json(glb)["asset"]["extras"]
    assert extras["caller_stamp"] == "seen"
    # The converter's own tree work still happened alongside the caller's.
    assert "model_stats" in extras


def test_a_reused_render_params_does_not_run_the_first_converters_postprocessor(beam_assembly):
    tree_runs: list[int] = []
    buffer_runs: list[int] = []

    params = RenderParams()
    params.set_gltf_tree_postprocessor(lambda tree: tree_runs.append(1))
    params.set_gltf_buffer_postprocessor(lambda items, tree: buffer_runs.append(1))

    first = SceneConverter(source=beam_assembly, params=params)
    first.build_glb()
    assert (len(tree_runs), len(buffer_runs)) == (1, 1)
    # build_scene left the FIRST converter's bound methods in the shared slots,
    # which is where a caller's hook would otherwise be read from.
    assert params.gltf_tree_postprocessor == first.tree_postprocessor

    second = SceneConverter(source=beam_assembly, params=params)
    second.build_glb()

    # A stale converter's method is not a caller's hook: chaining it would have
    # run the first converter's whole tree pass (its chained caller hook
    # included) on the second tree.
    assert second._caller_tree_postprocessor is None
    assert second._caller_buffer_postprocessor is None
    assert (len(tree_runs), len(buffer_runs)) == (1, 1)
    assert params.gltf_tree_postprocessor == second.tree_postprocessor

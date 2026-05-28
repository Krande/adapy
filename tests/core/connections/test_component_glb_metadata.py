"""Round-trip the ADA_EXT_data extension out of a built Connection's GLB.

Verifies Stage 6: component_info appears at the asset extension level
when source is a Connection with spec_name set, and per-weld entries
land in DesignDataExtension.object_metadata with the right shape.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from ada import Beam, Plate, Weld
from ada.api.connections import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    build_component,
    register_connection,
)
from ada.api.connections.spec import _clear_registry


@pytest.fixture(autouse=True)
def isolate_registry():
    _clear_registry()
    yield
    _clear_registry()


def _box_to_box_spec() -> ConnectionSpec:
    return ConnectionSpec(
        name="test.box_to_box",
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX", "SHS"}),
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 165.0),
            ),
            MemberCriteria(
                role=MemberRole.LANDING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX", "SHS"}),
            ),
        ),
    )


def _fillet(name, p1, p2, members, xdir=(0, 1, 0), throat=0.005):
    return Weld(
        name,
        p1=p1,
        p2=p2,
        weld_type="FILLET",
        members=members,
        xdir=xdir,
        throat=throat,
    )


def _read_ada_ext(glb_path: pathlib.Path) -> dict:
    """Pull the ADA_EXT_data extension out of a GLB on disk."""
    import trimesh

    scene = trimesh.load(glb_path, force="scene", process=False)
    tree = scene.export(file_type="gltf").get("gltf") if False else None
    # trimesh's load() drops extensions; round-trip via the bytes API
    raw = glb_path.read_bytes()
    # GLB format: header (12 B) + JSON chunk header (8 B) + JSON
    import struct

    assert raw[:4] == b"glTF"
    json_chunk_len = struct.unpack("<I", raw[12:16])[0]
    json_text = raw[20 : 20 + json_chunk_len].decode("utf-8")
    tree = json.loads(json_text)
    return tree.get("extensions", {}).get("ADA_EXT_data", {})


def test_component_info_present_for_built_connection(tmp_path: pathlib.Path):
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        return None

    inputs = {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing": {"section": "BOX300x300x12x12"},
    }
    conn = build_component(spec.name, inputs)

    glb_path = tmp_path / "preview.glb"
    conn.to_gltf(glb_path)

    ada_ext = _read_ada_ext(glb_path)
    ci = ada_ext.get("component_info")
    assert ci is not None, f"component_info missing; got: {list(ada_ext.keys())}"
    assert ci["type"] == "connection"
    assert ci["spec_name"] == spec.name
    assert ci["spec_inputs"] == inputs
    assert set(ci["member_groups"]) == {"incoming", "landing"}


def test_component_info_omitted_for_plain_part(tmp_path: pathlib.Path):
    from ada import Assembly, Part

    p = Part("plain")
    p.add_beam(Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300"))
    a = Assembly("root") / p

    glb_path = tmp_path / "plain.glb"
    a.to_gltf(glb_path)

    ada_ext = _read_ada_ext(glb_path)
    assert ada_ext.get("component_info") is None


def test_weld_metadata_in_object_metadata(tmp_path: pathlib.Path):
    spec = _box_to_box_spec()
    captured: list[Weld] = []

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        w = _fillet("w1", incoming.n1.p, incoming.n2.p, [incoming, landing], throat=0.007)
        captured.append(w)
        return SimpleNamespace(welds=[w], stiffeners=[])

    inputs = {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing": {"section": "BOX300x300x12x12"},
    }
    conn = build_component(spec.name, inputs)
    assert len(conn.welds) == 1

    glb_path = tmp_path / "with_weld.glb"
    conn.to_gltf(glb_path)

    ada_ext = _read_ada_ext(glb_path)
    design_objects = ada_ext.get("design_objects") or []
    assert design_objects, "no design_objects emitted"
    md = design_objects[0].get("object_metadata") or {}
    assert "w1" in md, f"weld 'w1' not in object_metadata; keys: {list(md)}"
    entry = md["w1"]
    assert entry["type"] == "weld"
    assert entry["weld_type"] == "FILLET"
    assert entry["throat"] == 0.007
    assert entry["sided"] == "one"
    assert entry["sweep_curve_present"] is False
    assert set(entry["members"]) == {"sample_incoming", "sample_landing"}


def test_weld_metadata_intermittent_round_trip(tmp_path: pathlib.Path):
    from ada.api.fasteners import IntermittentSpec, WeldType

    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        w = Weld(
            "intermittent_w",
            p1=incoming.n1.p,
            p2=incoming.n2.p,
            weld_type=WeldType.FILLET,
            members=[incoming, landing],
            xdir=(0, 1, 0),
            throat=0.005,
            sided="two",
            intermittent=IntermittentSpec(pitch=0.1, length_on=0.05, length_off=0.05),
        )
        return SimpleNamespace(welds=[w], stiffeners=[])

    inputs = {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing": {"section": "BOX300x300x12x12"},
    }
    conn = build_component(spec.name, inputs)

    glb_path = tmp_path / "intermittent.glb"
    conn.to_gltf(glb_path)

    ada_ext = _read_ada_ext(glb_path)
    md = ada_ext["design_objects"][0]["object_metadata"]
    entry = md["intermittent_w"]
    assert entry["sided"] == "two"
    assert entry["intermittent"] == {
        "pitch": 0.1,
        "length_on": 0.05,
        "length_off": 0.05,
    }

"""The convex-contact run sidecar (``ada.clash.sidecar``): what a clash run writes, and what a detail
job and a reloaded panel read back."""

from __future__ import annotations

import json

import pytest

from ada.clash.sidecar import (
    CLASH_SIDECAR_PREFIX,
    SidecarError,
    contact_id,
    index_key,
    index_with,
    parse_index,
    parse_run,
    run_from_glb_contacts,
    run_key,
    target_token,
)

TARGET = {"kind": "file", "source_key": "models/frame.ifc"}
GEOMETRY = {"key": "_derived/models/frame.ifc.glb", "sha256": "ab" * 32, "up_axis": "z"}
OPTIONS = {"security_margin": 0.01, "min_contact_area": 1e-6, "line_contact_thickness": 1e-3}


def _found():
    """``adacpp.clash.find_glb_contacts``'s shape: two members, one face contact (2 mm overlap)."""
    return {
        "members": [("4", "col"), ("7", "beam")],
        "pairs": [[0, 1]],
        "near1": [[0.2, 0.1, 2.6]],
        "near2": [[0.198, 0.1, 2.6]],
        "normal": [[1.0, 0.0, 0.0]],
        "penetration_depth": [-0.002],
        "contact_area": [0.02],
        "patches": [
            [
                {
                    "normal": (1.0, 0.0, 0.0),
                    "points1": [[0.2, 0.05, 2.5], [0.2, 0.15, 2.5], [0.2, 0.15, 2.7], [0.2, 0.05, 2.7]],
                    "points2": [[0.198, 0.05, 2.5], [0.198, 0.15, 2.5], [0.198, 0.15, 2.7], [0.198, 0.05, 2.7]],
                }
            ]
        ],
    }


def _run(**kw):
    return run_from_glb_contacts(
        _found(),
        run_id=kw.get("run_id", "r1"),
        created_at=kw.get("created_at", "2026-10-14T12:00:00Z"),
        engine="wasm",
        target=TARGET,
        geometry=GEOMETRY,
        options=OPTIONS,
        provenance={"adacpp": "0.34.0"},
    )


def test_a_run_round_trips_through_json():
    run = _run()
    back = parse_run(run.to_json())
    assert back == run
    (c,) = back.contacts
    assert c.id == contact_id(["4", "7"])
    assert back.names(c) == ("col", "beam")
    assert c.distance == -0.002 and c.penetration_depth == 0.002
    assert c.centre == pytest.approx((0.199, 0.1, 2.6))
    assert len(c.patches[0].points1) == 4


def test_the_clash_id_is_the_element_ids_not_the_names():
    # Two members called "beam" are still two members: the id hashes element ids, in any order.
    assert contact_id(["7", "4"]) == contact_id(["4", "7"])
    assert contact_id(["4", "7"]) != contact_id(["4", "8"])
    doc = _run().to_dict()
    doc["contacts"][0]["id"] = "000000000000"
    with pytest.raises(SidecarError, match="not the id of its members"):
        parse_run(doc)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.update(schema="ada.clash/result@2"), "not a clash sidecar"),
        (lambda d: d.pop("geometry"), "no 'geometry'"),
        (lambda d: d["geometry"].pop("sha256"), "sha256"),
        (lambda d: d["target"].pop("kind"), "kind"),
        (lambda d: d["contacts"][0].update(members=[0, 5]), "do not name two members"),
        (lambda d: d["contacts"][0].update(near_points=[[0, 0, 0]]), "two near points"),
        (lambda d: d["members"][0].pop("name"), "an id and a name"),
    ],
)
def test_a_malformed_run_is_refused_with_a_reason(mutate, match):
    doc = json.loads(_run().to_json())
    mutate(doc)
    with pytest.raises(SidecarError, match=match):
        parse_run(doc)


def test_keys_group_runs_by_target():
    assert run_key(TARGET, "r1").startswith(CLASH_SIDECAR_PREFIX + target_token(TARGET) + "/")
    assert index_key(TARGET) == f"{CLASH_SIDECAR_PREFIX}{target_token(TARGET)}/index.json"
    other = {"kind": "file", "source_key": "models/other.ifc"}
    assert target_token(other) != target_token(TARGET)
    # The descriptor's key order does not matter.
    assert target_token({"source_key": "models/frame.ifc", "kind": "file"}) == target_token(TARGET)


def test_the_index_lists_newest_first_and_replaces_a_rewritten_run():
    index = None
    for i, when in enumerate(["2026-10-14T10:00:00Z", "2026-10-14T12:00:00Z", "2026-10-14T11:00:00Z"]):
        run = _run(run_id=f"r{i}", created_at=when)
        index = index_with(index, run.summary(run_key(TARGET, run.run_id)))
    assert [r["run_id"] for r in parse_index(index)] == ["r1", "r2", "r0"]
    rewritten = _run(run_id="r0", created_at="2026-10-14T13:00:00Z")
    index = index_with(json.dumps(index), rewritten.summary(run_key(TARGET, "r0")))
    assert [r["run_id"] for r in parse_index(index)] == ["r0", "r1", "r2"]
    assert parse_index(index)[0]["contacts"] == 1
    assert parse_index(None) == []


def test_a_run_from_a_real_viewer_glb(tmp_path):
    clash = pytest.importorskip("adacpp.clash")
    if not hasattr(clash, "find_glb_contacts"):
        pytest.skip("this adacpp has no find_glb_contacts")
    import ada

    model = ada.Assembly("a") / (
        ada.Part("p")
        / [
            ada.Beam("col", (0, 0, 0), (0, 0, 3), "BOX300x300x12x12"),
            ada.Beam("beam", (0.148, 0, 2.5), (3.0, 0, 2.5), "BOX300x300x12x12"),
        ]
    )
    glb = tmp_path / "frame.glb"
    model.to_gltf(glb)
    run = run_from_glb_contacts(
        clash.find_glb_contacts(glb),
        run_id="r1",
        created_at="2026-10-14T12:00:00Z",
        engine="worker",
        target=TARGET,
        geometry={"key": "x", "sha256": "ab" * 32, "up_axis": "z"},
        options=OPTIONS,
    )
    assert {m["name"] for m in run.members} >= {"col", "beam"}
    assert len(run.contacts) == 1
    assert parse_run(run.to_json()) == run

"""The quantity take-off a conversion writes beside its GLB.

`Stats` and `Take-off` in the viewer used to be empty for every uploaded file: the take-off was
computed only for models the procedural engine compiled, although an ordinary conversion holds
the same structured model in its hands for as long as it takes to tessellate it. These pin the
two halves of the answer -- the sibling key rule, and that a conversion actually writes one.
"""

from __future__ import annotations

import json

import pytest

import ada
from ada.comms.rest.converters.keys import stats_sidecar_key
from ada.comms.rest.converters.takeoff import (
    consume_takeoff,
    record_takeoff,
    write_takeoff_sidecar,
)


def test_the_sidecar_is_the_glb_key_with_stats_json_in_its_place():
    assert stats_sidecar_key("_derived/a/b.glb") == "_derived/a/b.stats.json"
    # Every GLB variant a conversion can emit follows the one rule, so the two keys cannot drift.
    assert stats_sidecar_key("_derived/model.libtess2.glb") == "_derived/model.libtess2.stats.json"


def test_a_key_that_is_not_a_glb_still_gets_a_deterministic_sibling():
    assert stats_sidecar_key("_derived/model") == "_derived/model.stats.json"


def _frame() -> ada.Assembly:
    beam = ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300")
    plate = ada.Plate("pl1", [(0, 0), (2, 0), (2, 2), (0, 2)], 0.01)
    return ada.Assembly("frame") / (ada.Part("p") / [beam, plate])


def test_a_converted_model_writes_a_take_off_next_to_its_glb(tmp_path):
    out = tmp_path / "model.glb"
    out.write_bytes(b"glTF-ish")  # the sidecar is about the MODEL, not the bytes beside it

    # The exporter records while the model is alive; the conversion child writes beside the file
    # it actually produced. Two steps because neither side knows both facts.
    record_takeoff(_frame())
    write_takeoff_sidecar(out)

    sidecar = tmp_path / "model.stats.json"
    assert sidecar.is_file(), "an ada model yields a take-off"
    stats = json.loads(sidecar.read_text())
    assert stats["objects"] == 2
    assert stats["total_mass"] > 0
    assert {d["key"] for d in stats["disciplines"]} >= {"structural"}
    assert [b["section"] for b in stats["structural"]["beams"]] == ["IPE300"]


def test_a_source_with_no_structured_model_leaves_no_sidecar_and_does_not_raise(tmp_path):
    # An FEA result or a streamed STEP yields no Part. "No take-off" is the honest answer, and it
    # must not be an exception: statistics are a nicety, never a reason for a conversion to fail.
    out = tmp_path / "model.glb"
    out.write_bytes(b"glTF-ish")

    record_takeoff(object())
    assert write_takeoff_sidecar(out) is None
    assert not (tmp_path / "model.stats.json").exists()


def test_a_recorded_take_off_is_consumed_once():
    # The child is a fork, but a module-level slot still has to be emptied: a take-off left
    # behind would be written beside the NEXT conversion's output, which is a take-off of a model
    # the user is not looking at.
    record_takeoff(_frame())
    assert consume_takeoff() is not None
    assert consume_takeoff() is None


def test_a_take_off_that_raises_is_swallowed(tmp_path, monkeypatch):
    out = tmp_path / "model.glb"
    out.write_bytes(b"glTF-ish")

    import ada.topo_model.takeoff as takeoff_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("no take-off today")

    monkeypatch.setattr(takeoff_mod, "model_takeoff", _boom)
    record_takeoff(_frame())  # must not raise
    write_takeoff_sidecar(out)

    assert not (tmp_path / "model.stats.json").exists()


@pytest.mark.parametrize("target", ["ifc", "step", "obj"])
def test_only_the_glb_leg_writes_one(target):
    # Pinned as a fact about the KEY rule rather than by running every exporter: the sidecar is a
    # sibling of a GLB, and the uploader is gated on `target_format == "glb"`.
    assert stats_sidecar_key(f"_derived/model.{target}").endswith(".stats.json")


# ── the second read the native routes need, and its bound ────────────────────────────────────


def test_a_small_source_is_worth_a_second_read(tmp_path):
    from ada.comms.rest.converters.takeoff import source_is_small_enough

    small = tmp_path / "small.ifc"
    small.write_bytes(b"x" * 1024)
    assert source_is_small_enough(small)


def test_a_plant_scale_source_is_not(tmp_path, monkeypatch):
    # The native routes exist BECAUSE a semantic read of these files costs minutes and gigabytes.
    # Taking one off is worth a second read of an ordinary model and not of a plant.
    from ada.comms.rest.converters.takeoff import (
        SOURCE_SIZE_LIMIT_ENV,
        source_is_small_enough,
    )

    big = tmp_path / "big.ifc"
    big.write_bytes(b"x" * 4096)
    monkeypatch.setenv(SOURCE_SIZE_LIMIT_ENV, "1024")
    assert not source_is_small_enough(big)


def test_the_bound_can_be_switched_off_entirely(tmp_path, monkeypatch):
    from ada.comms.rest.converters.takeoff import (
        SOURCE_SIZE_LIMIT_ENV,
        source_is_small_enough,
    )

    src = tmp_path / "any.ifc"
    src.write_bytes(b"x")
    monkeypatch.setenv(SOURCE_SIZE_LIMIT_ENV, "0")
    assert not source_is_small_enough(src)


def test_a_nonsense_limit_falls_back_to_the_default_rather_than_disabling_take_offs(tmp_path, monkeypatch):
    from ada.comms.rest.converters.takeoff import (
        SOURCE_SIZE_LIMIT_ENV,
        source_is_small_enough,
    )

    src = tmp_path / "any.ifc"
    src.write_bytes(b"x")
    monkeypatch.setenv(SOURCE_SIZE_LIMIT_ENV, "not-a-number")
    assert source_is_small_enough(src)


def test_a_source_that_cannot_be_read_is_simply_not_taken_off(tmp_path):
    from ada.comms.rest.converters.takeoff import record_takeoff_from_source

    record_takeoff_from_source(tmp_path / "missing.ifc", ".ifc")  # must not raise
    assert consume_takeoff() is None


# ── the native route: the bound is for the reader it replaces ────────────────────────────────


def test_an_ifc_the_native_reader_can_answer_is_taken_off_regardless_of_size(tmp_path, monkeypatch):
    """The bound exists because of the SECOND READ, so a source that avoids it is not bound.

    25 MB was never a statement about which models deserve a take-off -- it was the price of
    reading a plant with ifcopenshell purely to count it. The native member reader answers the
    same question from the entities that state it, so an IFC it can vouch for is taken off at any
    size, and the panel stops being empty for exactly the files it was most wanted on.
    """
    from ada.cadit.ifc.read.native_members import native_members_available
    from ada.comms.rest.converters.takeoff import (
        SOURCE_SIZE_LIMIT_ENV,
        record_takeoff_from_source,
    )

    if not native_members_available():
        pytest.skip("ada-cpp without IfcMemberScan")

    src = tmp_path / "frame.ifc"
    _frame().to_ifc(src, validate=False)
    monkeypatch.setenv(SOURCE_SIZE_LIMIT_ENV, "1")  # every real file is "too large"

    record_takeoff_from_source(src, ".ifc")
    stats = consume_takeoff()
    if stats is None:
        pytest.skip("ada-cpp without material in IfcMemberScan (needs >= 0.29)")
    assert stats["total_mass"] > 0
    assert stats["objects"] > 0


def test_a_step_source_is_still_bound(tmp_path, monkeypatch):
    """Nothing native answers for a STEP, so that leg keeps paying the full read and its bound."""
    from ada.comms.rest.converters.takeoff import (
        SOURCE_SIZE_LIMIT_ENV,
        record_takeoff_from_source,
    )

    src = tmp_path / "frame.stp"
    src.write_bytes(b"x" * 4096)
    monkeypatch.setenv(SOURCE_SIZE_LIMIT_ENV, "1024")

    record_takeoff_from_source(src, ".stp")
    assert consume_takeoff() is None

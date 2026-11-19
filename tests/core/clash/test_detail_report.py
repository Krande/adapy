"""What a detail run says about what did not survive, and how far it has got.

``build_detail`` reports, beside the joints it built: each joint it did NOT build as a record (a
decline told apart from a failure), each seam a builder could not build (the builder's own
``conn.metadata["weld_failures"]``), and each weld a connection lists that has no geometry in the
GLB. And it reports progress per joint, so a run of hundreds of joints does not sit at one stage for
ten minutes looking hung.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import ada
from ada.api.connections.spec import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    NotApplicable,
    _clear_registry,
    get_registered,
    register_connection,
)
from ada.clash.detail import build_detail, stage_progress


@pytest.fixture(autouse=True)
def isolate_registry():
    _clear_registry()
    yield
    _clear_registry()


SPEC = ConnectionSpec(
    name="provider.spec",
    roles=(
        MemberCriteria(
            role=MemberRole.INCOMING,
            kind=MemberKind.BEAM,
            section_in=frozenset({"BOX"}),
            angle_to_role=MemberRole.LANDING,
            angle_range=AngleRange(20.0, 165.0),
        ),
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
    ),
    priority=10,
    capability="pool-x",
)


def _joint(tag: str):
    column = ada.Beam(f"column_{tag}", (0, 0, 0), (2, 0, 0), "BOX300x300x12x12")
    brace = ada.Beam(f"brace_{tag}", (1, 0, 0), (1, 2, 0), "BOX300x300x12x12")
    return SimpleNamespace(members=[brace, column], landing=column, centre=(1.0, 0.0, 0.0), contact=None)


def _register_builder():
    """j1 is declined, j2 fails, j3 is built with one seam it could not make, one weld whose solid
    cannot be built, and one good weld."""

    @register_connection(SPEC)
    def builder(*, landing, incoming, name, **_):
        if name.endswith("_j1"):
            raise NotApplicable("takes an incoming at 60 to 120 degrees; this one is at 49.2")
        if name.endswith("_j2"):
            raise ValueError("no room for the gusset")
        conn = ada.Connection(name=name, spec_name="provider.spec")
        conn.add_weld(
            ada.Weld("w_good", p1=(0, 0, 0), p2=(0.3, 0, 0), xdir=(0, 0, 1), throat=0.005, members=(incoming, landing))
        )
        bad = ada.Weld(
            "w_bad", p1=(0, 0.1, 0), p2=(0.3, 0.1, 0), xdir=(0, 0, 1), throat=0.005, members=(incoming, landing)
        )

        def _broken_solid():
            raise ValueError("degenerate weld profile")

        bad.solid_geom = _broken_solid
        conn.add_weld(bad)
        conn.metadata["weld_failures"] = [
            {"place": "flange top", "weld_type": "fillet", "reason": "the seam runs off the plate"},
            "not a record",  # a provider's document: what is not a dict is ignored
        ]
        return conn


def _run(**kw):
    by_id = {jid: _joint(jid) for jid in ("j1", "j2", "j3")}
    return build_detail(
        get_registered("provider.spec"),
        spec_name="provider.spec",
        joint_ids=["j1", "j2", "j3"],
        by_id=by_id,
        gen_options={},
        **kw,
    )


def test_joints_not_built_are_records_a_decline_told_from_a_failure():
    _register_builder()
    glb, stats = _run()
    assert glb
    assert stats["built_by"] == {"provider.spec": 1}
    # The sentences stay for older readers.
    assert len(stats["skipped"]) == 2 and stats["skipped"][1] == "j2: ValueError: no room for the gusset"

    declined, failed = stats["not_built"]
    assert declined == {
        "joint_id": "j1",
        "kind": "declined",
        "reason": "takes an incoming at 60 to 120 degrees; this one is at 49.2",
        "declined": [
            {"spec": "provider.spec", "reason": "takes an incoming at 60 to 120 degrees; this one is at 49.2"}
        ],
    }
    assert failed == {
        "joint_id": "j2",
        "kind": "failed",
        "spec": "provider.spec",
        "reason": "ValueError: no room for the gusset",
        "declined": [],
    }


def test_seams_that_failed_are_reported_from_the_builder_and_from_the_glb():
    _register_builder()
    _, stats = _run()
    reported, no_geometry = stats["weld_failures"]
    assert reported == {
        "joint_id": "j3",
        "connection": "provider.spec_j3",
        "place": "flange top",
        "weld_type": "fillet",
        "reason": "the seam runs off the plate",
    }
    assert no_geometry["joint_id"] == "j3" and no_geometry["place"] == "w_bad"
    assert no_geometry["reason"].startswith("no geometry: ValueError: degenerate weld profile")
    # The good weld drew, and the take-off still counts both welds the connection owns.
    assert stats["joints"]["items"][0]["welds"] == 2


def test_a_run_with_nothing_lost_has_empty_records():
    @register_connection(SPEC)
    def builder(*, landing, incoming, name, **_):
        conn = ada.Connection(name=name, spec_name="provider.spec")
        conn.add_weld(
            ada.Weld("w1", p1=(0, 0, 0), p2=(0.3, 0, 0), xdir=(0, 0, 1), throat=0.005, members=(incoming, landing))
        )
        return conn

    _, stats = build_detail(
        get_registered("provider.spec"),
        spec_name="provider.spec",
        joint_ids=["j1"],
        by_id={"j1": _joint("j1")},
        gen_options={},
    )
    assert stats["not_built"] == [] and stats["weld_failures"] == [] and "skipped" not in stats


def test_the_bodies_are_prebuilt_in_one_batch_before_the_glb(monkeypatch):
    """Everything the GLB writer tessellates -- the welds among it -- goes to one prebuild call; a
    body that fails there is still reported once, by the writer."""
    import ada.cad.shape_cache as shape_cache

    batches: list[list[str]] = []
    real = shape_cache.prebuild_solids

    def spy(objects, threads=0):
        objects = list(objects)
        batches.append([o.name for o in objects])
        return real(objects, threads)

    monkeypatch.setattr(shape_cache, "prebuild_solids", spy)
    _register_builder()
    _, stats = _run()
    assert len(batches) == 1
    assert {"w_good", "w_bad"} <= set(batches[0])
    assert [f["place"] for f in stats["weld_failures"]].count("w_bad") == 1


def test_progress_is_reported_before_the_first_joint_and_after_each():
    _register_builder()
    calls: list[tuple[int, int]] = []
    _run(on_progress=lambda done, total: calls.append((done, total)))
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_stage_progress_moves_through_the_stage_share_at_most_once_a_second():
    now = [100.0]
    reports: list[tuple[str, float]] = []
    on_progress = stage_progress(lambda stage, frac: reports.append((stage, frac)), clock=lambda: now[0])

    on_progress(0, 4)  # the first is always reported
    now[0] += 0.2
    on_progress(1, 4)  # too soon: dropped
    now[0] += 1.0
    on_progress(2, 4)
    now[0] += 0.1
    on_progress(4, 4)  # the last is always reported
    assert reports == [("detail 0/4", 0.55), ("detail 2/4", 0.7), ("detail 4/4", 0.85)]

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


def test_what_was_built_is_recorded_per_connection_with_its_welds():
    _register_builder()
    _, stats = _run()
    (conn,) = stats["connections"]
    assert conn["joint_id"] == "j3" and conn["connection"] == "provider.spec_j3"
    assert conn["spec"] == "provider.spec"
    assert conn["member_roles"] == {"landing": ["column_j3"], "incoming": ["brace_j3"]}
    assert conn["centre"] == [1.0, 0.0, 0.0]
    good, bad = conn["welds"]
    assert good["name"] == "w_good" and good["weld_type"] == "FILLET"
    assert good["throat"] == 0.005 and good["sided"] == "one"
    assert good["length"] == pytest.approx(0.3)
    assert sorted(good["members"]) == ["brace_j3", "column_j3"]
    # A weld whose solid failed is still a weld of the connection (and also a weld failure).
    assert bad["name"] == "w_bad"
    import json

    json.dumps(stats)  # the record is plain JSON


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


def test_every_phase_after_the_joints_is_reported_in_order_with_its_count():
    """bodies, tessellating, writing GLB, stats -- after the joints -- each first and last at least,
    moving through its share of the fraction; the output is what it is without a reporter."""
    from ada.clash.detail import DETAIL_SHARES, detail_progress

    _register_builder()
    reports: list[tuple[str, float]] = []
    glb, stats = _run(on_progress=detail_progress(lambda stage, frac: reports.append((stage, frac))))
    stages = [s for s, _ in reports]
    # "bodies 2/2" -> "bodies"; "writing GLB" stays itself. The phase names, in order, each once in a row.
    phases = [s.rsplit(" ", 1)[0] if "/" in s else s for s in stages]
    order = [p for i, p in enumerate(phases) if i == 0 or phases[i - 1] != p]
    assert order == ["detail", "bodies", "tessellating", "writing GLB", "stats"]
    assert stages[0] == "detail 0/3" and "detail 3/3" in stages
    # Two objects carry bodies here (the connection's two welds); each phase's last report is its total.
    bodies = [s for s in stages if s.startswith("bodies ")]
    assert bodies[0] == "bodies 0/2" and bodies[-1] == "bodies 2/2"
    tess = [s for s in stages if s.startswith("tessellating ")]
    total = int(tess[-1].split("/")[1])
    assert tess[0] == f"tessellating 0/{total}" and tess[-1] == f"tessellating {total}/{total}"
    # The fraction never goes back, and each phase ends where its share ends.
    fracs = [f for _, f in reports]
    assert fracs == sorted(fracs)
    for name in ("detail", "bodies", "tessellating"):
        assert [f for s, f in reports if s.startswith(f"{name} ")][-1] == DETAIL_SHARES[name][1]

    # The same GLB and stats without anyone listening.
    _clear_registry()
    _register_builder()
    glb_quiet, stats_quiet = _run()
    assert stats_quiet == stats
    assert len(glb_quiet) == len(glb)


def test_a_detail_reporter_throttles_within_a_phase_but_never_drops_a_phase_s_first_or_last():
    from ada.clash.detail import detail_progress, upload_stage

    now = [0.0]
    reports: list[tuple[str, float]] = []
    p = detail_progress(lambda stage, frac: reports.append((stage, frac)), clock=lambda: now[0])
    p(0, 4)
    now[0] += 0.1
    p(1, 4)  # too soon: dropped
    now[0] += 0.1
    p(4, 4)  # the last: kept
    p.stage("bodies", 0, 600)  # a new phase's first: kept at once
    for done in (256, 512):
        now[0] += 0.2
        p.stage("bodies", done, 600)  # too soon: dropped
    p.stage("bodies", 600, 600)  # the last: kept
    now[0] += 5
    p.stage("tessellating", 0, 10)
    now[0] += 1.5
    p.stage("tessellating", 5, 10)  # a second later: kept
    p.stage("writing GLB")
    p.stage("stats")
    p.upload("GLB", 12_345_678, index=0, count=2)
    p.upload("stats", index=1, count=2)
    assert reports == [
        ("detail 0/4", 0.55),
        ("detail 4/4", 0.75),
        ("bodies 0/600", 0.75),
        ("bodies 600/600", 0.82),
        ("tessellating 0/10", 0.82),
        ("tessellating 5/10", 0.875),
        ("writing GLB", 0.95),
        ("stats", 0.95),
        ("uploading GLB (12.3 MB)", 0.95),
        ("uploading stats", 0.97),
    ]
    assert upload_stage("GLB", 2_000_000) == ("uploading GLB (2.0 MB)", 0.95)


def test_the_bodies_are_built_in_chunks_and_reported_per_chunk(monkeypatch):
    import ada.cad.shape_cache as shape_cache
    from ada.clash import detail

    calls: list[int] = []
    monkeypatch.setattr(shape_cache, "prebuild_solids", lambda objs, threads=0: calls.append(len(list(objs))))
    monkeypatch.setattr(detail, "BODY_CHUNK", 2)
    part = ada.Part("P")
    for i in range(5):
        part.add_beam(ada.Beam(f"column_{i}", (i, 0, 0), (i, 0, 1), "IPE200"))
    seen: list[tuple[int, int]] = []
    detail._prebuild_bodies(part, on_chunk=lambda done, total: seen.append((done, total)))
    assert calls == [2, 2, 1]
    assert seen == [(0, 5), (2, 5), (4, 5), (5, 5)]

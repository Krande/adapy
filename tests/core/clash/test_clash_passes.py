"""The pass registry: who may contribute a clash search, and what a result says about it.

A check is not one algorithm. Members meeting at a shared node and members whose solids overlap
by two millimetres are different questions, wanted by different people, answered by different
machinery. Core's passes look at axes and at surface distances; a detail model needs a mesh-level
interference test that is too heavy for core and too specific to be core's opinion.

So passes are registered, and a plugin's pass is the same kind of thing as core's -- selected,
reported and filtered by the same machinery. These pin the properties that makes that safe.
"""

from __future__ import annotations

import pytest

import ada
from ada.clash.identify import identify_joints, run_clash_check
from ada.clash.options import ClashOptions
from ada.clash.passes import (
    ClashPass,
    all_passes,
    list_passes,
    register_pass,
    selected_passes,
)


@pytest.fixture
def frame():
    return ada.Assembly("a") / (
        ada.Part("p")
        / [
            ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200"),
            ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200"),
            ada.Beam("c0", (5, 0, 0), (5, 0, 3), "IPE200"),
        ]
    )


@pytest.fixture
def plugin_pass():
    """A pass contributed from outside core, as a plugin would."""
    from ada.clash.identify import _Found

    seen = {}

    def fn(part, options, *, beams, plates):
        seen["called"] = True
        return [
            _Found(
                members=list(beams[:2]),
                centre=(1.0, 2.0, 3.0),
                origin="",  # left empty on purpose: the registry stamps it
                contact={"penetration_depth": 0.002, "normal": [0, 0, 1], "patch_area": 1.5e-4},
            )
        ]

    register_pass(ClashPass(name="test-mesh", label="Solids overlapping", capability="test-cap", fn=fn, priority=99))
    yield seen
    from ada.clash import passes as passes_mod

    passes_mod._REGISTRY.pop("test-mesh", None)


def test_cores_own_passes_are_registered_not_hard_coded():
    # Core's passes and a plugin's have to be the same kind of thing, or "contributing a pass"
    # means extending a special case rather than adding an entry.
    names = {p.name for p in all_passes()}
    assert {"beam-beam", "plate-beam", "plate-plate"} <= names
    assert all(p.capability is None for p in all_passes() if p.name.startswith(("beam-", "plate-")))


def test_a_capability_bearing_pass_is_opt_in(frame, plugin_pass):
    """A default check must not fail because a plugin's pool is not there.

    A pass routed to a capability runs only when it is asked for by name; otherwise every default
    check on every deployment would report a failure for something nobody requested.
    """
    identify_joints(frame, ClashOptions())
    assert "called" not in plugin_pass

    identify_joints(frame, ClashOptions(passes=("beam-beam", "test-mesh")))
    assert plugin_pass["called"] is True


def test_selecting_passes_selects_exactly_those(frame, plugin_pass):
    assert {p.name for p in selected_passes(("beam-beam",))} == {"beam-beam"}
    assert {p.name for p in selected_passes(None)} == {"beam-beam", "plate-beam", "plate-plate"}
    # A name core does not know is not obeyed, and not an error either -- a stale checkbox in a
    # panel must not break a check.
    assert {p.name for p in selected_passes(("beam-beam", "no-such-pass"))} == {"beam-beam"}


def test_every_joint_says_which_pass_found_it(frame, plugin_pass):
    result = run_clash_check(frame, source_key="f.ifc", options=ClashOptions(passes=("beam-beam", "test-mesh")))
    origins = {j.origin for j in result.joints}
    assert origins == {"beam-beam", "test-mesh"}


def test_the_result_says_what_ran_and_what_did_not(frame, plugin_pass):
    """ "Not run" and "found nothing" are different answers, and the panel offers the difference."""
    result = run_clash_check(frame, source_key="f.ifc", options=ClashOptions(passes=("beam-beam",)))
    by_name = {p["name"]: p for p in result.passes}

    assert by_name["beam-beam"]["ran"] is True
    assert by_name["beam-beam"]["found"] >= 1
    for name in ("plate-beam", "plate-plate", "test-mesh"):
        assert by_name[name]["ran"] is False
        assert by_name[name]["reason"] == "not selected"
    # A capability-bearing pass reports its capability, so a panel can say WHY it is unavailable
    # rather than merely that it is.
    assert by_name["test-mesh"]["capability"] == "test-cap"


def test_a_pass_that_fails_does_not_take_the_others_with_it(frame):
    """The passes answer different questions; one that cannot run is no reason to withhold the rest."""
    from ada.clash import passes as passes_mod

    def boom(part, options, *, beams, plates):
        raise RuntimeError("no backend here")

    register_pass(ClashPass(name="test-broken", label="Broken", fn=boom, priority=98))
    try:
        outcome = identify_joints(frame, ClashOptions(passes=("beam-beam", "test-broken")))
        assert outcome.joints, "the healthy pass still produced joints"
        assert any("test-broken pass failed" in w for w in outcome.warnings)
        broken = next(p for p in outcome.passes if p["name"] == "test-broken")
        assert broken["ran"] is False and "no backend here" in broken["reason"]
    finally:
        passes_mod._REGISTRY.pop("test-broken", None)


def test_contact_data_survives_the_round_trip(frame, plugin_pass):
    """The measurement a geometric pass exists to produce must reach the builder.

    It is computed in the pass, carried in the result document, and handed to a connection
    builder as its `clash` argument. If any leg drops it the richer passes are pointless.
    """
    from ada.clash.result import parse_clash_result

    result = run_clash_check(frame, source_key="f.ifc", options=ClashOptions(passes=("test-mesh",)))
    joint = result.joints[0]
    assert joint.contact["penetration_depth"] == pytest.approx(0.002)

    reread = parse_clash_result(result.to_json())
    assert reread.joints[0].contact["penetration_depth"] == pytest.approx(0.002)
    assert reread.joints[0].origin == "test-mesh"


def test_an_axis_pass_carries_no_contact_rather_than_an_invented_one(frame):
    result = run_clash_check(frame, source_key="f.ifc", options=ClashOptions(passes=("beam-beam",)))
    assert all(j.contact is None for j in result.joints)
    assert "contact" not in result.to_dict()["joints"][0]


def test_the_registry_is_listable_for_a_panel_to_offer():
    entries = {e["name"]: e for e in list_passes()}
    assert entries["plate-beam"]["needs_backend"] is True
    assert entries["beam-beam"]["needs_backend"] is False
    assert entries["beam-beam"]["capability"] is None

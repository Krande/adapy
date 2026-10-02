"""Clash CHECKERS: selectable engines, each owning its passes, and the neutral @2 result they write.

A checker is what a person picks ("adapy" or a contributed mesh-interference engine); a pass is one
search it runs. These pin what makes a contributed checker safe to offer next to core's: it runs
only its own passes, it is refused by name where it is not registered, its options reach its passes
and the derived key, and what it measures survives into the result in core's own vocabulary.
"""

from __future__ import annotations

import json

import pytest

import ada
from ada.clash.identify import _Found, identify_joints, run_clash_check
from ada.clash.options import ClashOptions
from ada.clash.passes import (
    BUILTIN_CHECKER,
    ClashChecker,
    ClashPass,
    get_checker,
    list_checkers,
    passes_for,
    register_checker,
    register_pass,
)
from ada.clash.result import ClashContact, ClashResultError, parse_clash_result


@pytest.fixture
def frame():
    return ada.Assembly("a") / (
        ada.Part("p")
        / [
            ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200"),
            ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200"),
        ]
    )


@pytest.fixture
def mesh_checker():
    """A contributed checker with one pass, as a plugin would register it."""
    seen: dict = {}

    def fn(part, options, *, beams, plates):
        seen["options"] = dict(options.checker_options)
        g0, g1 = beams[0], beams[1]
        return [
            _Found(
                members=[g0, g1],
                centre=(5.0, 0.0, 0.0),
                landing=g1,
                ends={g0.name: "end"},
                contact={
                    "normal": [1, 0, 0],
                    "penetration_depth": 0.003,
                    "near_points": [[5, 0, 0], [5.001, 0, 0]],
                    "contact_area": 2.5e-3,
                    "security_margin": 0.01,
                    "incoming_angle_deg": 0.0,
                    "provider_thing": 7,
                },
            )
        ]

    register_pass(ClashPass(name="test-convex", label="Convex contact", capability="test-pool", fn=fn))
    register_checker(
        ClashChecker(
            name="test-engine",
            label="Test engine",
            passes=("test-convex",),
            capability="test-pool",
            options=({"key": "security_margin", "type": "number", "default": 0.01},),
        )
    )
    yield seen
    from ada.clash import passes as passes_mod

    passes_mod._REGISTRY.pop("test-convex", None)
    passes_mod._CHECKERS.pop("test-engine", None)


# ── options ──────────────────────────────────────────────────────────────────────────────────


def test_options_round_trip_every_field():
    """`from_dict` is the one reader; a field it drops is a field every route and job drops."""
    opts = ClashOptions(
        out_of_plane_tol=0.2,
        point_tol=1e-4,
        root="deck",
        include_plate_joints=False,
        passes=("beam-beam",),
        checker="test-engine",
        checker_options={"security_margin": 0.02},
    )
    assert ClashOptions.from_dict(json.loads(json.dumps(opts.to_dict()))) == opts


def test_a_default_check_keeps_its_old_options_document():
    """Unset new fields are left out, so a cached result's derived key does not move."""
    assert set(ClashOptions().to_dict()) == {"out_of_plane_tol", "point_tol", "root", "include_plate_joints"}


@pytest.mark.parametrize("bad", [{"passes": "beam-beam"}, {"checker_options": [1]}, ["not", "a", "dict"]])
def test_malformed_options_are_refused(bad):
    with pytest.raises(TypeError):
        ClashOptions.from_dict(bad)


# ── checkers ─────────────────────────────────────────────────────────────────────────────────


def test_core_checker_is_always_registered_and_owns_core_passes():
    assert get_checker(None).name == BUILTIN_CHECKER
    assert {p.name for p in passes_for(BUILTIN_CHECKER)} >= {"beam-beam", "plate-beam", "plate-plate"}
    listed = {c["name"]: c for c in list_checkers()}
    assert listed[BUILTIN_CHECKER]["capability"] is None


def test_a_checker_runs_only_its_own_passes(frame, mesh_checker):
    outcome = identify_joints(frame, ClashOptions(checker="test-engine", checker_options={"security_margin": 0.02}))
    assert {f.origin for f in outcome.joints} == {"test-convex"}
    # Its settings reached its pass, untouched by core.
    assert mesh_checker["options"] == {"security_margin": 0.02}
    # It reports its own passes, not core's -- those belong to a check that was not run.
    assert {p["name"] for p in outcome.passes} == {"test-convex"}


def test_core_checker_does_not_run_a_contributed_pass(frame, mesh_checker):
    outcome = identify_joints(frame, ClashOptions())
    assert "options" not in mesh_checker
    assert all(f.origin != "test-convex" for f in outcome.joints)


def test_an_unregistered_checker_checks_nothing_and_says_so(frame):
    outcome = identify_joints(frame, ClashOptions(checker="nobody-has-this"))
    assert outcome.joints == []
    assert any("nobody-has-this" in w for w in outcome.warnings)


# ── the @2 result ────────────────────────────────────────────────────────────────────────────


def test_result_carries_checker_roles_ends_and_typed_contact(frame, mesh_checker):
    result = run_clash_check(frame, source_key="f.ifc", options=ClashOptions(checker="test-engine"))
    assert result.schema == "ada.clash/result@2"
    assert result.checker == "test-engine"
    assert result.checker_capability == "test-pool"

    joint = result.joints[0]
    roles = {m.name: m.role for m in joint.members}
    assert roles == {"g0": "incoming", "g1": "landing"}
    assert {m.name: m.end for m in joint.members} == {"g0": "end", "g1": None}

    contact = joint.contact
    assert isinstance(contact, ClashContact)
    assert contact.penetration_depth == pytest.approx(0.003)
    assert contact.contact_area == pytest.approx(2.5e-3)
    assert contact.near_points[1] == (5.001, 0.0, 0.0)
    # Outside core's vocabulary: kept, under extras, and still readable by its own key.
    assert contact.extras == {"provider_thing": 7}
    assert contact["provider_thing"] == 7

    reread = parse_clash_result(result.to_json())
    assert reread.joints[0].contact == contact
    assert reread.joints[0].members == joint.members
    assert reread.checker == "test-engine"


def test_a_result_at_1_still_reads():
    doc = {
        "schema": "ada.clash/result@1",
        "source_key": "f.ifc",
        "options": {},
        "counts": {"joints": 1},
        "joints": [
            {
                "id": "abc",
                "centre": [0, 0, 0],
                "members": [{"name": "a", "kind": "BEAM"}, {"name": "b", "kind": "BEAM"}],
                "type_key": "k",
                "type_label": "K",
                "contact": {"penetration_depth": 0.001, "patch_area": 1e-4},
            }
        ],
        "groups": [],
        "provenance": {},
    }
    result = parse_clash_result(doc)
    assert result.checker is None
    contact = result.joints[0].contact
    assert contact.penetration_depth == pytest.approx(0.001)
    assert contact["patch_area"] == pytest.approx(1e-4)


def test_an_unknown_schema_is_still_refused():
    with pytest.raises(ClashResultError):
        parse_clash_result({"schema": "ada.clash/result@99"})

"""Core-only coverage for the pieces ``clash_detail`` (the REST job layer) leans on:
re-deriving a joint's id from a fresh ``identify_joints`` pass, and building a stats block through
``ada.topo_model.takeoff._joints_takeoff`` from a registered spec's real builder output.

Deliberately imports nothing from ``ada.comms.rest`` -- the REST wiring itself (the job handlers,
the routes, the queue-less transport's engines) is covered end to end in
``tests/comms/rest/test_clash_check_routes.py``; this file exists so that coverage is not the
ONLY thing pinning the pure-python contract the REST layer is built on, and so the contract stays
testable without pulling fastapi into ``tests/core`` (a Phase-4 CI failure came from exactly that
-- keep fastapi imports out of ``tests/core/**``).
"""

from __future__ import annotations

import hashlib

import pytest

import ada
from ada.api.connections.spec import _clear_registry, get_registered
from ada.clash import ClashOptions, describe_member, identify_joints, run_clash_check
from ada.clash.builtin_specs import BUILTIN_SPEC_NAMES, register_builtin_specs
from ada.topo_model.takeoff import _joints_takeoff


@pytest.fixture(autouse=True)
def clean_registry():
    """The spec registry is process-global; every test starts and ends with it cleared, matching
    ``test_builtin_specs.py``'s own convention."""
    _clear_registry()
    yield
    _clear_registry()


def _found_id(found) -> str:
    """The exact id formula ``run_clash_check`` uses (``ada.clash.identify``'s own documented,
    deterministic contract) -- pinned here so a drift in it is caught both where the core suite
    already pins joint ids (via ``run_clash_check``) and where ``clash_detail`` re-derives them
    independently, from a SEPARATE ``identify_joints`` call."""
    names = sorted(describe_member(m).name for m in found.members)
    return hashlib.sha256("|".join([found.origin, *names]).encode("utf-8")).hexdigest()[:12]


def _two_girder_model() -> ada.Assembly:
    """Two horizontal I-beams meeting at right angles -- one girder-to-girder joint, the exact
    shape ``builtin.girder_gusset`` binds to."""
    a = ada.Assembly("ClashTest")
    p = ada.Part("P")
    a.add_part(p)
    p.add_beam(ada.Beam("g1", (0, 0, 0), (2, 0, 0), "IPE200"))
    p.add_beam(ada.Beam("g2", (1, 0, 0), (1, 2, 0), "IPE200"))
    return a


def test_found_id_matches_the_id_run_clash_check_stamped():
    """The exact property ``clash_detail`` depends on to re-derive a selection from nothing but a
    cached result: identifying the SAME source at the SAME options twice, once through
    ``run_clash_check`` and once through a bare ``identify_joints`` call, yields the same id."""
    register_builtin_specs()
    options = ClashOptions(include_plate_joints=False)

    result = run_clash_check(_two_girder_model(), source_key="probe", options=options)
    assert len(result.joints) == 1
    stamped_id = result.joints[0].id

    outcome = identify_joints(_two_girder_model(), options)
    assert len(outcome.joints) == 1
    assert _found_id(outcome.joints[0]) == stamped_id


def test_builtin_spec_builder_output_feeds_joints_takeoff_the_shape_clash_detail_needs():
    """What ``clash_detail`` does with a found joint: split landing/incoming, call the
    registered builder with the REAL members, collect the ``Connection`` under a ``Part``, and
    read ``_joints_takeoff`` -- reproduced here with no REST plumbing at all, so the shape that
    lands in a ``.stats.json`` (and therefore what the ``Joints`` Scene tab renders) is pinned as
    a pure-core property."""
    register_builtin_specs()
    options = ClashOptions(include_plate_joints=False)
    outcome = identify_joints(_two_girder_model(), options)
    assert len(outcome.joints) == 1
    found = outcome.joints[0]

    members = list(found.members)
    landing = found.landing if found.landing is not None else members[0]
    incoming = next((m for m in members if m is not landing), members[-1])

    registered = get_registered("builtin.girder_gusset")
    conn = registered.fn(landing=landing, incoming=incoming, centre=found.centre, name="probe_joint")

    joints_part = ada.Part("Joints")
    joints_part.add_part(conn)

    takeoff = _joints_takeoff(joints_part)
    assert takeoff["count"] == 1
    assert takeoff["items"][0]["members"] == sorted([landing.name, incoming.name])
    assert takeoff["by_type"][0]["count"] == 1


def test_every_builtin_name_is_registered_and_resolvable_after_register_builtin_specs():
    """``get_registered`` is what ``clash_detail`` looks a spec up by name with -- every declared
    built-in must resolve to a callable builder, not merely be listed."""
    register_builtin_specs()
    for name in BUILTIN_SPEC_NAMES:
        registered = get_registered(name)
        assert callable(registered.fn)

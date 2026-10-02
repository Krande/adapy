"""Which SEARCHES a deployment can run, advertised the way everything else here is.

A pass contributed by a plugin lives in that plugin's worker, so the panel cannot know it exists
by reading its own code. Without an advertisement it could only learn of one by SEEING ONE IN A
RESULT -- a checkbox that appears after you have already managed to do the thing it turns on.

So a worker publishes its registry on the heartbeat and the API unions that with core's own, the
same shape the connection specs, the detailing engines and the blueprints already use. What these
pin is the part that is easy to get wrong and invisible when it is: the ATTRIBUTION. Core's passes
must never claim a capability, and a contributed one must never be attributed to a worker that
cannot be named.
"""

from __future__ import annotations

from ada.comms.rest.worker.registration import _clash_passes_for_heartbeat


def test_core_passes_advertise_no_capability():
    # They run wherever core runs. Claiming a capability would make the panel hide them on every
    # deployment whose pool does not happen to advertise that token.
    entries = {e["name"]: e for e in _clash_passes_for_heartbeat(["base", "weld-ish"])}
    for name in ("beam-beam", "plate-beam", "plate-plate"):
        assert entries[name]["capability"] is None


def test_a_contributed_pass_is_attributed_to_this_workers_capability():
    from ada.clash import passes as passes_mod
    from ada.clash.passes import ClashPass, register_pass

    register_pass(ClashPass(name="test-adv", label="Contributed", fn=lambda *a, **k: []))
    try:
        entries = {e["name"]: e for e in _clash_passes_for_heartbeat(["base", "detail-clash"])}
        assert entries["test-adv"]["capability"] == "detail-clash"
    finally:
        passes_mod._REGISTRY.pop("test-adv", None)


def test_a_worker_that_cannot_be_named_attributes_nothing():
    """Several capabilities, or none beyond `base`, cannot be resolved to one pool.

    Reported as None -- registered here, but this worker cannot say which pool serves it --
    rather than guessed at, because a wrong routing token is a job no pool ever picks up, which
    is a spinner that never resolves rather than a failure that surfaces.
    """
    from ada.clash import passes as passes_mod
    from ada.clash.passes import ClashPass, register_pass

    register_pass(ClashPass(name="test-adv2", label="Contributed", fn=lambda *a, **k: []))
    try:
        for caps in (["base"], ["base", "one", "two"], []):
            entries = {e["name"]: e for e in _clash_passes_for_heartbeat(caps)}
            assert entries["test-adv2"]["capability"] is None, caps
    finally:
        passes_mod._REGISTRY.pop("test-adv2", None)


def test_a_pass_that_names_its_own_capability_keeps_it():
    # A plugin that stated the pool it needs knows better than the worker does.
    from ada.clash import passes as passes_mod
    from ada.clash.passes import ClashPass, register_pass

    register_pass(ClashPass(name="test-adv3", label="C", capability="stated", fn=lambda *a, **k: []))
    try:
        entries = {e["name"]: e for e in _clash_passes_for_heartbeat(["base", "something-else"])}
        assert entries["test-adv3"]["capability"] == "stated"
    finally:
        passes_mod._REGISTRY.pop("test-adv3", None)


def test_the_advertisement_describes_a_pass_well_enough_to_offer_it():
    entries = {e["name"]: e for e in _clash_passes_for_heartbeat(["base"])}
    beam = entries["beam-beam"]
    assert beam["slug"] == "beam-beam"  # merge_catalog_specs keys on this
    assert beam["label"]
    assert beam["needs_backend"] is False
    assert entries["plate-beam"]["needs_backend"] is True


def test_checkers_advertise_core_as_capability_free_and_keep_a_stated_pool():
    """A checker is what a check is ROUTED by, so its attribution decides where the job goes."""
    from ada.clash import passes as passes_mod
    from ada.clash.passes import ClashChecker, register_checker
    from ada.comms.rest.worker.registration import _clash_checkers_for_heartbeat

    register_checker(ClashChecker(name="test-chk", label="C", capability="stated"))
    register_checker(ClashChecker(name="test-chk2", label="C2"))
    try:
        # A worker serving several pools -- the combined dev-stack worker -- cannot attribute an
        # unstated checker, but one that names its pool keeps it.
        entries = {e["name"]: e for e in _clash_checkers_for_heartbeat(["base", "one", "two"])}
        assert entries["adapy"]["capability"] is None
        assert entries["test-chk"]["capability"] == "stated"
        assert entries["test-chk2"]["capability"] is None
        assert entries["test-chk"]["slug"] == "test-chk"
        single = {e["name"]: e for e in _clash_checkers_for_heartbeat(["base", "one"])}
        assert single["test-chk2"]["capability"] == "one"
    finally:
        passes_mod._CHECKERS.pop("test-chk", None)
        passes_mod._CHECKERS.pop("test-chk2", None)


def test_concept_readers_are_advertised_with_their_availability_and_this_workers_pool():
    """What the panel's "geometry from" choice is offered from: only a provider some live worker can
    READ into objects can be the source of a check's members."""
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts
    from ada.comms.rest.worker.registration import _asset_concept_readers_for_heartbeat

    register_asset_concepts("member-reader", lambda: object(), label="Members")
    register_asset_concepts("missing-dep", lambda: object(), available=lambda: False)
    try:
        entries = {e["id"]: e for e in _asset_concept_readers_for_heartbeat(["base", "one"])}
        assert entries["member-reader"]["slug"] == "member-reader"  # merge_catalog_specs keys on this
        assert entries["member-reader"]["readable"] is True
        assert entries["member-reader"]["label"] == "Members"
        assert entries["member-reader"]["capability"] == "one"
        assert entries["missing-dep"]["readable"] is False
        unnamed = {e["id"]: e for e in _asset_concept_readers_for_heartbeat(["base", "one", "two"])}
        assert unnamed["member-reader"]["capability"] is None
    finally:
        clear_asset_concepts()

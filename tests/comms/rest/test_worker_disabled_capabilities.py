"""Taking a capability out of service without rebuilding the image.

``ADA_WORKER_CAPABILITIES`` normally comes from the IMAGE, which is the only
place that can state the set correctly -- it is what carries the packages behind
each capability. A deployment repeating that list keeps a second copy of a fact
it does not own, and it drifts: one such copy sat a capability behind its image
for weeks, and nothing surfaced it, because a job published to a pool nobody
subscribes to is accepted and then simply never runs.

``ADA_WORKER_DISABLED_CAPABILITIES`` is the subtractive half a deployment should
reach for instead. These cover what it does to the advertised set -- pure list
arithmetic, no NATS.
"""

import logging

import pytest

from ada.comms.rest import worker


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ADA_WORKER_CAPABILITIES", raising=False)
    monkeypatch.delenv("ADA_WORKER_DISABLED_CAPABILITIES", raising=False)


@pytest.fixture
def warnings_visible(monkeypatch, caplog):
    """Let caplog see adapy's warnings.

    `ada.config` sets ``propagate = False`` on the ``ada`` logger, so records
    never reach the root handler caplog installs -- an assertion on
    ``caplog.text`` therefore fails against a warning that WAS emitted. Re-enable
    propagation for the test only; monkeypatch restores it.
    """
    monkeypatch.setattr(worker.logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger=worker.logger.name):
        yield caplog


def test_absent_disable_list_leaves_the_declared_set_untouched(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity,abaqus")
    assert worker._declared_capabilities() == ["base", "capacity", "abaqus"]


def test_unset_capabilities_still_defaults_to_base():
    assert worker._declared_capabilities() == ["base"]


def test_a_disabled_capability_is_dropped(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity,abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "abaqus")
    assert worker._declared_capabilities() == ["base", "capacity"]


def test_order_of_the_survivors_is_preserved(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity,pm-engine,abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "capacity")
    assert worker._declared_capabilities() == ["base", "pm-engine", "abaqus"]


def test_several_can_be_disabled_at_once(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity,pm-engine,abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "capacity,abaqus")
    assert worker._declared_capabilities() == ["base", "pm-engine"]


def test_matching_ignores_case_and_surrounding_space(monkeypatch):
    # An operator typing a capability under incident pressure should not have to
    # match the case the image happened to use.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,Abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", " ABAQUS , ")
    assert worker._declared_capabilities() == ["base"]


def test_a_name_that_matches_nothing_changes_nothing(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "typo")
    assert worker._declared_capabilities() == ["base", "capacity"]


def test_an_unmatched_name_is_warned_about(monkeypatch, warnings_visible):
    # A typo here is silent in its effect -- the pool it was meant to stop stays
    # up -- so the log line is the only way anyone finds out.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "abacus")
    worker._declared_capabilities()
    assert "abacus" in warnings_visible.text


def test_disabling_is_logged_at_warning(monkeypatch, warnings_visible):
    # A deliberate reduction in service, whose symptom if left behind is jobs
    # that queue forever. Not an info-level detail.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "abaqus")
    worker._declared_capabilities()
    assert "abaqus" in warnings_visible.text
    assert any(r.levelno == logging.WARNING for r in warnings_visible.records)


def test_disabling_everything_falls_back_to_base(monkeypatch, warnings_visible):
    # A worker advertising nothing is not a configuration anyone wants; scaling
    # to zero is how a pool is idled. `base` was declared here, so it is this
    # worker's to fall back to.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity,abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "base,capacity,abaqus")
    assert worker._declared_capabilities() == ["base"]
    assert "falling back to 'base'" in warnings_visible.text


def test_a_worker_that_never_served_base_does_not_acquire_it_by_subtraction(monkeypatch, warnings_visible):
    """The fallback must not hand `base` to a worker that never had it.

    An off-cluster machine joins for one capability it alone can serve. If
    disabling that capability made it fall back to `base`, an
    independently-installed adapy would start pulling ordinary conversion jobs
    from the cluster's queue -- the hazard ADA_WORKER_BASE_CONVERSIONS exists to
    prevent, reached through an incident switch.
    """
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "cad")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "cad")

    assert worker._declared_capabilities() == []
    assert "does not serve 'base'" in warnings_visible.text


def test_disabling_a_bare_token_leaves_its_shards_and_says_so(monkeypatch, warnings_visible):
    """A sharded capability is only partly disabled, and silently.

    One plugin can address several pools by suffixing the capability with an
    option value, so a worker holds `cad` and `cad-alpha`. Those are distinct
    tokens: disabling `cad` leaves `cad-alpha` serving, and the "names nothing
    this worker advertises" warning does not fire because `cad` did match. Under
    incident pressure the operator gets a confirmation line and believes the
    pool is out of service while half of it still pulls jobs.

    Prefix-matching by default would be worse -- `web3d` must not vanish because
    somebody disabled `web` -- so the shard is named and left to be disabled
    deliberately.
    """
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "cad,cad-alpha")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "cad")

    assert worker._declared_capabilities() == ["cad-alpha"]
    assert "cad-alpha is still advertised" in warnings_visible.text


def test_an_unrelated_capability_sharing_a_prefix_is_not_reported_as_a_shard(monkeypatch, warnings_visible):
    # `web3d` is not a shard of `web`; only a `<token>-` prefix counts.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "web3d,base")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "web")

    assert worker._declared_capabilities() == ["web3d", "base"]
    assert "still advertised" not in warnings_visible.text


def test_a_blank_capability_list_still_means_unset(monkeypatch):
    # An empty ADA_WORKER_CAPABILITIES is "unset", not "serve nothing", so that
    # an empty set further down can only ever be a deliberate verdict.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "")
    assert worker._declared_capabilities() == ["base"]


def test_the_subtraction_reaches_the_subscribed_pools(monkeypatch):
    # The point of subtracting before advertising: the set a worker announces
    # and the set it consumes must not disagree, or the API routes to a pool
    # nothing serves and the job waits out its timeout looking merely slow.
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base,capacity,abaqus")
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "abaqus")
    declared = worker._declared_capabilities()
    assert worker._pool_capabilities(declared) == ["base", "capacity"]

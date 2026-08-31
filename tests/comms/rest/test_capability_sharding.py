"""Sharded capability pools, and the spec union that makes them visible.

A capability pool is ONE durable consumer: every worker in it competes for the
same messages. That is right when the workers are interchangeable and wrong when
they are not — several workers each holding a different licence, dataset or
device cannot share a pool, because the job goes to whichever pulls first.

Routing them apart by making the wrong worker NAK is the design this replaces;
`pull_subscribe`'s own docstring records that it burned the per-message delivery
budget and dead-lettered valid jobs. Subject routing does it instead:
`capability_option` on the plugin spec turns one option's value into a subject
suffix, and the worker subscribes to that suffix.

Two properties carry the whole thing:

* the API and the worker must normalise a capability IDENTICALLY, or jobs are
  published where nothing listens;
* several workers advertising one plugin must not erase each other, or the UI
  cannot say which shards are online.
"""

import os
import tempfile

# Importing ada.comms.rest.app evaluates a module-level `create_app()`
# which materializes a local Storage. Point it at a temp dir so the
# import succeeds in environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest  # noqa: E402

from ada.comms.rest.app import _merge_spec  # noqa: E402
from ada.comms.rest.queue import (  # noqa: E402
    MAX_CAPABILITY_TOKEN_LEN,
    capability_token,
)
from ada.comms.rest.worker import _pool_capabilities  # noqa: E402

# --- the token -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("alpha", "alpha"),
        ("  alpha  ", "alpha"),
        ("SITE-A", "site-a"),
        ("mesh-tools", "mesh-tools"),
    ],
)
def test_ordinary_values_survive_intact(raw, expected):
    assert capability_token(raw) == expected


@pytest.mark.parametrize("raw", ["a.b", "a b", "a/b", "a\tb", "a*b", "a>b"])
def test_subject_breaking_characters_become_one_dash(raw):
    # A dot would create a DEEPER subject that no consumer filters on, so the
    # job would sit in the stream looking merely slow rather than failing.
    assert capability_token(raw) == "a-b"


def test_separator_runs_collapse_but_do_not_vanish():
    # `SITE-A/2` and `SITE-A 511` must not both become `site-a511`: dropping
    # separators can merge two pools that were meant to be distinct.
    assert capability_token("SITE-A/2") == "site-a-2"
    assert capability_token("SITE-A   2") == "site-a-2"
    assert capability_token("SITE-A//2") == "site-a-2"


@pytest.mark.parametrize("raw", ["", "   ", None, ".", "...", "***", 0])
def test_nothing_usable_yields_the_empty_token(raw):
    # Callers read "" as "no shard". It must never become a token, or every
    # unqualified request would land on a subject named after punctuation.
    assert capability_token(raw) == ""


def test_a_long_value_is_bounded_and_still_well_formed():
    token = capability_token("x" * 500)
    assert len(token) == MAX_CAPABILITY_TOKEN_LEN
    assert not token.startswith("-") and not token.endswith("-")


def test_truncation_never_leaves_a_trailing_dash():
    # Cutting mid-separator would otherwise give `...-`, and `cap-shard-` is a
    # different subject from `cap-shard`.
    raw = "a" * (MAX_CAPABILITY_TOKEN_LEN - 1) + " " + "b" * 20
    assert not capability_token(raw).endswith("-")


# --- publisher and subscriber must agree -----------------------------------


@pytest.mark.parametrize("raw", ["alpha", "SITE A", "site.a", "  BETA  ", "SITE-A/2"])
def test_the_worker_subscribes_to_exactly_what_the_api_publishes(raw):
    """The one property that makes sharding work at all.

    The API builds the subject from the request; the worker builds it from
    ADA_WORKER_CAPABILITIES. If the two normalised differently the job would be
    published to a subject nothing is subscribed to.
    """
    api_side = f"cad-{capability_token(raw)}"
    worker_side = _pool_capabilities([f"cad-{raw}"])[0]
    assert api_side == worker_side


def test_pool_capabilities_still_dedupes_across_normalisation():
    # Two spellings of one pool must not open two consumers on one subject.
    assert _pool_capabilities(["CAD", "cad", " cad "]) == ["cad"]


def test_a_worker_with_nothing_usable_still_serves_base():
    assert _pool_capabilities(["", "  ", "..."]) == ["base"]


def test_a_worker_can_hold_both_the_bare_and_the_sharded_pool():
    # This is what lets one worker answer unqualified requests as well as its
    # own shard, so a single-worker deployment never has to qualify anything.
    assert _pool_capabilities(["cad", "cad-alpha"]) == ["cad", "cad-alpha"]


# --- the spec union --------------------------------------------------------


def _spec(**over):
    base = {"slug": "cad-export", "version": "1.0.0", "union_fields": ["projects"], "projects": []}
    base.update(over)
    return base


def test_declared_list_fields_are_combined_across_workers():
    a = _spec(projects=["alpha"])
    _merge_spec(a, _spec(projects=["beta"]))
    assert a["projects"] == ["alpha", "beta"]


def test_the_union_preserves_order_and_drops_duplicates():
    a = _spec(projects=["alpha", "beta"])
    _merge_spec(a, _spec(projects=["beta", "gamma"]))
    assert a["projects"] == ["alpha", "beta", "gamma"]


def test_undeclared_fields_are_not_merged():
    # A blanket "merge every list" would combine per-worker facts into a spec
    # describing a worker that does not exist.
    a = _spec(projects=["alpha"], source_exts=[".a"])
    _merge_spec(a, _spec(projects=["beta"], source_exts=[".b"]))
    assert a["source_exts"] == [".a"]


def test_scalars_keep_the_first_workers_value():
    a = _spec(version="1.0.0")
    _merge_spec(a, _spec(version="9.9.9"))
    assert a["version"] == "1.0.0"


def test_union_fields_is_itself_unioned_for_rolling_upgrades():
    # Half the pool on a build that declares the key, half not: the half that
    # does must still get merged rather than the behaviour depending on which
    # worker sorted first.
    a = {"slug": "cad-export", "projects": ["alpha"]}  # older build, no union_fields
    _merge_spec(a, _spec(projects=["beta"]))
    assert a["projects"] == ["alpha", "beta"]
    assert a["union_fields"] == ["projects"]


def test_a_field_absent_from_the_first_worker_is_adopted():
    a = {"slug": "cad-export", "union_fields": ["projects"]}
    _merge_spec(a, _spec(projects=["beta"]))
    assert a["projects"] == ["beta"]


def test_a_non_list_value_on_the_incoming_spec_is_ignored():
    a = _spec(projects=["alpha"])
    _merge_spec(a, _spec(projects="beta"))
    assert a["projects"] == ["alpha"]


def test_dict_entries_dedupe_by_value_not_identity():
    a = _spec(union_fields=["items"], items=[{"k": 1}])
    _merge_spec(a, _spec(union_fields=["items"], items=[{"k": 1}, {"k": 2}]))
    assert a["items"] == [{"k": 1}, {"k": 2}]


def test_union_fields_cannot_name_itself_into_a_loop():
    a = _spec(union_fields=["union_fields", "projects"], projects=["alpha"])
    _merge_spec(a, _spec(union_fields=["union_fields", "projects"], projects=["beta"]))
    assert a["projects"] == ["alpha", "beta"]
    assert a["union_fields"] == ["union_fields", "projects"]


def test_merging_does_not_mutate_the_incoming_spec():
    # `out[slug]` is a copy; the caller's list must not be aliased into it, or
    # one worker's registry row would grow another worker's entries.
    a = _spec(projects=["alpha"])
    b = _spec(projects=["beta"])
    _merge_spec(a, b)
    assert b["projects"] == ["beta"]


# --- existing pools must not move -------------------------------------------


@pytest.mark.parametrize("name", ["base", "weld_gen", "fem_solver", "abaqus", "cad-export", "a1"])
def test_capability_names_already_in_use_are_unchanged(name):
    """The compatibility guard.

    `_pool_capabilities` now normalises, so any name this function REWRITES
    moves a live worker to a different subject. Underscore is the realistic
    case: it is legal in a NATS subject and pools are named with it. If this
    test ever has to change, a deployment somewhere silently stops receiving
    jobs.
    """
    assert capability_token(name) == name
    assert _pool_capabilities([name]) == [name]


def test_the_token_is_idempotent():
    # Applied at both the publish and the subscribe end, so a value that has
    # already been through it must survive a second pass unchanged.
    for raw in ["ALPHA", "site a/2", "weld_gen", "  x  ", "a.b.c", ""]:
        once = capability_token(raw)
        assert capability_token(once) == once

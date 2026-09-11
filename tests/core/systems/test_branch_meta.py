"""The two halves of a branched system's metadata are named in one place."""

from __future__ import annotations

from ada.api.systems.branch_meta import (
    BRANCH_JUNCTION_ID,
    BRANCH_KEY,
    BRANCH_LEG_METADATA,
    BRANCH_LEGS,
    BRANCH_ROUTE_JUNCTION_POINT,
    BRANCH_ROUTE_KEY,
    BRANCH_ROUTE_TRUNK,
    branch_leg_names,
)


def test_the_two_producers_write_to_different_keys():
    assert BRANCH_KEY != BRANCH_ROUTE_KEY
    # The import-side schema the DEXPI round-trip tests read.
    assert (BRANCH_KEY, BRANCH_JUNCTION_ID, BRANCH_LEGS, BRANCH_LEG_METADATA) == (
        "branch",
        "junction_id",
        "legs",
        "leg_metadata",
    )
    assert (BRANCH_ROUTE_KEY, BRANCH_ROUTE_TRUNK, BRANCH_ROUTE_JUNCTION_POINT) == (
        "branch_route",
        "trunk",
        "junction_point",
    )


def test_branch_leg_names_reads_the_import_side_only():
    assert branch_leg_names(None) is None
    assert branch_leg_names({}) is None
    assert branch_leg_names({BRANCH_KEY: {}}) is None
    assert branch_leg_names({BRANCH_KEY: {BRANCH_LEGS: []}}) is None
    assert branch_leg_names({BRANCH_KEY: {BRANCH_LEGS: ("a", "b", "c")}}) == ["a", "b", "c"]
    # A routed-only system declares no legs to wire.
    assert branch_leg_names({BRANCH_ROUTE_KEY: {BRANCH_ROUTE_TRUNK: ["a", "b"]}}) is None

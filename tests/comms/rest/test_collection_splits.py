"""Split collections: what a provider declares, what an admin chose, and what core then offers."""

from __future__ import annotations

import json

import pytest

from ada.comms.rest import collection_splits as cs
from ada.comms.rest.routes.asset_schedules import collections_of
from ada.comms.rest.routes.asset_sweeps import _all_collections

COPIES = {
    "ALPHA": [
        {"collection": "ALPHA-X", "label": "ALPHA (X)", "location": "East", "default": True},
        {"collection": "ALPHA-Y", "label": "ALPHA (Y)"},
        {"collection": "ALPHA-Z", "label": "ALPHA (Z)"},
    ]
}


def _spec(**over) -> dict:
    return {
        "asset_provider_id": "vendor",
        "asset_collections_field": "items",
        "items": ["ALPHA", "BETA"],
        "asset_collection_splits_field": "copies",
        "copies": COPIES,
        **over,
    }


def test_declared_copies_keep_only_usable_entries():
    spec = _spec(copies={"ALPHA": [{"collection": "ALPHA-X"}, {"label": "no id"}, {"collection": "  "}], "BETA": "x"})
    assert cs.declared_copies(spec) == {"ALPHA": [{"collection": "ALPHA-X"}]}
    assert cs.declared_copies({"items": []}) == {}


def test_provider_splits_reads_one_provider_leniently():
    doc = {"vendor": {"ALPHA": ["ALPHA-X", "", 3], "BETA": "nope", "GAMMA": []}, "other": {"A": ["A-1"]}}
    assert cs.provider_splits(doc, "vendor") == {"alpha": ["ALPHA-X"]}
    assert cs.provider_splits(doc, "missing") == {}
    assert cs.provider_splits("not a dict", "vendor") == {}


def test_unsplit_collections_are_offered_as_declared():
    assert collections_of(_spec()) == ["ALPHA", "BETA"]
    assert collections_of(_spec(), {}) == ["ALPHA", "BETA"]


def test_a_split_collection_is_offered_as_its_chosen_copies_in_its_place():
    assert collections_of(_spec(), {"alpha": ["alpha-y", "ALPHA-X"]}) == ["ALPHA-Y", "ALPHA-X", "BETA"]


def test_a_split_whose_copies_are_all_gone_offers_the_collection_again():
    # A worker that stops advertising the copies must not hide the collection.
    assert collections_of(_spec(), {"alpha": ["ALPHA-GONE"]}) == ["ALPHA", "BETA"]
    assert collections_of(_spec(copies={}), {"alpha": ["ALPHA-X"]}) == ["ALPHA", "BETA"]


def test_every_worker_offers_its_split_copies():
    one = _spec(items=["ALPHA"])
    two = _spec(items=["BETA", "ALPHA"])
    assert _all_collections([one, two], {"alpha": ["ALPHA-Z"]}) == ["ALPHA-Z", "BETA"]


def test_copy_names_spell_every_declared_copy():
    assert cs.copy_names(_spec()) == {"alpha-x": "ALPHA-X", "alpha-y": "ALPHA-Y", "alpha-z": "ALPHA-Z"}


@pytest.mark.asyncio
async def test_load_splits_reads_the_setting_and_survives_garbage(monkeypatch):
    from ada.comms.rest import db as db_module

    stored = {"value": json.dumps({"vendor": {"ALPHA": ["ALPHA-X"]}})}

    async def get_setting(pool, key):
        assert key == cs.COLLECTION_SPLITS_SETTING
        return stored["value"]

    monkeypatch.setattr(db_module, "get_setting", get_setting)
    assert await cs.load_splits(object(), "vendor") == {"alpha": ["ALPHA-X"]}
    stored["value"] = "{not json"
    assert await cs.load_splits(object(), "vendor") == {}
    assert await cs.load_splits(None, "vendor") == {}

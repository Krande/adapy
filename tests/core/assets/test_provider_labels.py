"""Provider display names: the per-scope admin alias setting, spec declarations, and resolution."""

import json

import pytest

from ada.assets.provider_labels import (
    MAX_PROVIDER_LABEL_LENGTH,
    ProviderLabelError,
    clean_provider_label,
    declared_provider_labels,
    normalise_provider_aliases,
    parse_provider_aliases,
    resolve_provider_labels,
)
from ada.assets.registry import (
    clear_asset_providers,
    register_asset_provider,
    registered_provider_labels,
)


def test_clean_trims_and_collapses_whitespace():
    assert clean_provider_label("  Fixture\tlines \n v2 ") == "Fixture lines v2"


@pytest.mark.parametrize("bad", ["", "   ", None, 3, ["x"], "x" * (MAX_PROVIDER_LABEL_LENGTH + 1)])
def test_clean_refuses_what_is_not_a_display_name(bad):
    with pytest.raises(ProviderLabelError):
        clean_provider_label(bad)


def test_parse_is_lenient_and_drops_invalid_entries():
    raw = json.dumps(
        {
            "shared": {"fixture-lines": " Lines ", "mesher": "", "builder": 3},
            "project:1": {"mesher": "   "},
            "junk": "nope",
        }
    )
    assert parse_provider_aliases(raw) == {"shared": {"fixture-lines": "Lines"}}


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", b"{", 42])
def test_parse_reads_missing_or_malformed_as_empty(raw):
    assert parse_provider_aliases(raw) == {}


def test_normalise_trims_and_drops_empty_scopes():
    stored = normalise_provider_aliases(json.dumps({"shared": {"mesher": "  Mesher  "}, "project:1": {}}))
    assert json.loads(stored) == {"shared": {"mesher": "Mesher"}}


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1]",
        json.dumps({"shared": ["mesher"]}),
        json.dumps({"shared": {"mesher": ""}}),
        json.dumps({"shared": {"mesher": "x" * (MAX_PROVIDER_LABEL_LENGTH + 1)}}),
        json.dumps({"shared": {"": "Mesher"}}),
        json.dumps({" ": {"mesher": "Mesher"}}),
    ],
)
def test_normalise_refuses_invalid_input(raw):
    with pytest.raises(ProviderLabelError):
        normalise_provider_aliases(raw)


def test_normalise_of_nothing_is_an_empty_map():
    assert json.loads(normalise_provider_aliases("")) == {}


def test_declared_labels_from_specs():
    specs = [
        {"id": "a", "asset_provider_id": "fixture-lines", "asset_provider_label": "Lines"},
        # A second worker of the same provider: the first declaration wins.
        {"id": "a2", "asset_provider_id": "fixture-lines", "asset_provider_label": "Other"},
        # A spec speaking for several providers.
        {"id": "b", "asset_provider_label": {"mesher": " Mesher ", "builder": ""}},
        # No provider id to attach a plain label to.
        {"id": "c", "asset_provider_label": "Orphan"},
        {"id": "d", "asset_provider_id": "plain"},
        "not a spec",
    ]
    assert declared_provider_labels(specs) == {"fixture-lines": "Lines", "mesher": "Mesher"}


def test_resolution_order_is_alias_then_spec_then_registry():
    aliases = {"shared": {"fixture-lines": "Scope name"}, "project:1": {"mesher": "Other scope"}}
    declared = {"fixture-lines": "Spec name", "mesher": "Spec mesher"}
    registered = {"fixture-lines": "Registered", "mesher": "Registered mesher", "builder": "Builder"}
    assert resolve_provider_labels("shared", aliases, declared, registered) == {
        "fixture-lines": "Scope name",
        "mesher": "Spec mesher",
        "builder": "Builder",
    }
    # No scope key (a personal scope): providers' own labels only.
    assert resolve_provider_labels(None, aliases, declared)["fixture-lines"] == "Spec name"


def test_registered_labels_are_only_those_given():
    clear_asset_providers()
    try:
        register_asset_provider("fixture-lines", lambda: object(), label="Lines")
        register_asset_provider("mesher", lambda: object())
        assert registered_provider_labels() == {"fixture-lines": "Lines"}
    finally:
        clear_asset_providers()

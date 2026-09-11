"""``merge_catalog_specs`` is the one rule every catalog listing shares: code
built-ins first, live workers shadow them, DB rows shadow both, and what THIS
process registered only fills gaps. Pinned here so the eight listing routes
cannot drift apart again."""

from ada.comms.rest.catalog import (
    merge_catalog_specs,
    overlay_catalog_rows,
    sort_by_name,
)


def test_live_spec_wins_but_keeps_code_origin_for_a_builtin():
    code = [{"slug": "a", "name": "A", "v": 1}, {"slug": "b", "name": "B"}]
    live = {"a": {"name": "A'", "v": 2}, "c": {"name": "C"}}
    out = merge_catalog_specs(code, live, live_origin="db")
    assert list(out) == ["a", "b", "c"]  # authored order, extras after
    assert out["a"] == {"slug": "a", "name": "A'", "v": 2, "origin": "code"}
    assert out["b"]["origin"] == "code"
    assert out["c"] == {"slug": "c", "name": "C", "origin": "db"}


def test_live_filter_and_projection():
    live = {"x": {"engine": "other"}, "y": {"engine": None}}
    out = merge_catalog_specs(
        [],
        live,
        project=lambda slug, spec, origin: {"slug": slug, "origin": origin, "n": spec.get("name") or slug},
        live_filter=lambda spec: spec.get("engine") in (None, "mine"),
    )
    assert out == {"y": {"slug": "y", "origin": "code", "n": "y"}}


def test_local_specs_only_fill_missing_slugs_and_accept_id():
    out = merge_catalog_specs(
        [{"slug": "a", "name": "A"}],
        {"b": {"name": "B"}},
        local_specs=[{"slug": "a", "name": "shadowed"}, {"id": "l", "name": "L"}, {"name": "no-slug"}],
    )
    assert out["a"]["name"] == "A"
    assert out["l"] == {"id": "l", "name": "L", "slug": "l", "origin": "code"}
    assert set(out) == {"a", "b", "l"}


def test_overlay_rows_shadow_and_sort_by_name():
    by_slug = merge_catalog_specs([{"slug": "pump", "name": "Zed"}], {})
    overlay_catalog_rows(
        by_slug,
        [{"slug": "pump", "name": "alpha", "id": 7}, {"slug": "", "name": "skipped"}, {"name": "no slug"}],
        lambda slug, row: {"slug": slug, "name": row["name"], "origin": "catalog", "id": row["id"]},
    )
    assert by_slug == {"pump": {"slug": "pump", "name": "alpha", "origin": "catalog", "id": 7}}
    assert [e["name"] for e in sort_by_name([{"name": "b"}, {"name": "A"}, {"name": "c"}])] == ["A", "b", "c"]

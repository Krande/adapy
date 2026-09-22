"""``asset-sweep-ifc`` (Phase 4): sweeping a STAGED newer file against a published spine, driven
against the ``plant-a`` corpus's v1 -> v2 delta (one member modified, one removed, one added, one
storey untouched, one site untouched -- ``tests/core/assets/corpus/make_plant_a.py``).

Pins the change-feed honesty Decision 4 demands: the three touched members (and every ancestor
above them, up to and including their site) read as evidence of a change; the untouched site gets
exactly one row that does NOT advance past what is published (`current`); a `--root`-scoped sweep
never touches the other site at all (`not-recorded`, by absence); and `no-feed` -- no database --
is a third, distinct "cannot say" state that must never be answered as `current`.
"""

from __future__ import annotations

import pathlib

import ifcopenshell
import pytest
from fastapi import HTTPException

from ada.assets.ifc.publish import publish_ifc
from ada.assets.ifc.sweep import IFC_SOURCE_ID, IfcSweepError, run_ifc_sweep, sweep_ifc
from ada.comms.rest.routes.source_nodes import _source_nodes_pool

from .fake_store import FakeStore, FakeSyncStorageFacade

CORPUS_DIR = pathlib.Path(__file__).parents[1] / "corpus"
V1 = CORPUS_DIR / "plant-a_v1.ifc"
V2 = CORPUS_DIR / "plant-a_v2.ifc"

V1_STAGED = "assets/_staging/v1/source.ifc"
V2_STAGED = "assets/_staging/v2/source.ifc"

V1_INSTANT = "2026-01-01T00:00:00Z"
V2_INSTANT = "2026-01-02T00:00:00Z"


def _raw(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def _names(raw: bytes) -> dict[str, str]:
    f = ifcopenshell.file.from_string(raw.decode())
    return {p.Name: p.GlobalId for p in f.by_type("IfcProduct") if p.Name}


@pytest.fixture
def published_v1() -> tuple[FakeStore, dict[str, str]]:
    """v1 published whole-file (2 sites). Returns the store and name -> guid for v1."""
    store = FakeStore()
    store.put_bytes(V1_STAGED, _raw(V1))
    publish_ifc(store, collection="plant-a", staged_key=V1_STAGED, extracted_at=V1_INSTANT)
    store.put_bytes(V2_STAGED, _raw(V2))
    return store, _names(_raw(V1))


def _sweep(store: FakeStore, **kwargs) -> "object":
    return sweep_ifc(store, collection="plant-a", staged_key=V2_STAGED, extracted_at=V2_INSTANT, **kwargs)


# --- the whole-file sweep: three touched members, their ancestors, and the untouched site ---------


def test_touched_members_carry_action_exactly_the_three(published_v1):
    store, names1 = published_v1
    result = _sweep(store)

    verdicts = {r.node_ref: r.action for r in result.rows if r.action is not None}
    assert len(verdicts) == 3
    assert verdicts[names1["aa-bm0"]] == "modified"
    assert verdicts[names1["aa-bm1"]] == "deleted"

    # aa-bm6 is v2-only -- not in `names1` (a v1 name -> guid map) -- so it is found by exclusion:
    # the one verdict that is neither the modified nor the deleted member.
    added = [n for n, a in verdicts.items() if n not in (names1["aa-bm0"], names1["aa-bm1"])]
    assert len(added) == 1
    assert verdicts[added[0]] == "added"
    assert added[0] not in names1.values()  # a genuinely new guid, not a v1 product

    # every touched-member row is stamped with the SWEEP's own instant, not the published one
    for guid in verdicts:
        row = next(r for r in result.rows if r.node_ref == guid)
        assert row.last_changed_at == V2_INSTANT


def test_touched_ancestors_are_bumped_without_their_own_action(published_v1):
    store, names1 = published_v1
    result = _sweep(store)
    by_ref = {r.node_ref: r for r in result.rows}

    for name in ("SiteA", "StoreyA2", "AssemblyAA"):
        guid = names1[name]
        assert guid in by_ref, f"{name} should have a rolled-up row"
        row = by_ref[guid]
        assert row.action is None  # only the touched product itself carries a verdict
        assert row.last_changed_at == V2_INSTANT  # pulled forward -- this root reads 'behind'


def test_untouched_storey_and_its_members_get_no_row_at_all(published_v1):
    """StoreyA1 (and everything under it) is byte-identical between v1 and v2 -- a row's absence
    IS 'no change' (Decision 7); this is the corpus's 'one storey untouched' claim, checked."""
    store, names1 = published_v1
    result = _sweep(store)
    by_ref = {r.node_ref for r in result.rows}

    assert names1["StoreyA1"] not in by_ref
    for i in range(6):
        assert names1[f"a1-bm{i}"] not in by_ref
    for i in range(4):
        assert names1[f"a1-pl{i}"] not in by_ref


def test_untouched_site_reads_current_not_behind(published_v1):
    """SiteB's whole subtree is byte-identical between v1 and v2. It gets exactly ONE row -- an
    administrative 'still current' stamp that RE-AFFIRMS the published produced_at rather than
    advancing to the sweep's own instant (Decision 7: no per-node NOCHANGE value is ever stored,
    so this one row per covered-but-unchanged root is what lets a reader tell `current` apart from
    `not-recorded`)."""
    store, names1 = published_v1
    result = _sweep(store)

    site_b_rows = [r for r in result.rows if r.node_ref == names1["SiteB"]]
    assert len(site_b_rows) == 1
    row = site_b_rows[0]
    assert row.action is None
    assert row.last_changed_at == V1_INSTANT  # the PUBLISHED produced_at, not the sweep's instant
    assert row.last_changed_at != V2_INSTANT

    # and nothing under SiteB shows up at all
    by_ref = {r.node_ref for r in result.rows}
    for name in ("StoreyB1", "StoreyB2"):
        assert names1[name] not in by_ref
    for i in range(6):
        assert names1[f"b1-bm{i}"] not in by_ref
        assert names1[f"b2-bm{i}"] not in by_ref


def test_covered_roots_are_both_declared_sites(published_v1):
    store, names1 = published_v1
    result = _sweep(store)
    assert set(result.covered_roots) == {names1["SiteA"], names1["SiteB"]}
    assert result.source == IFC_SOURCE_ID == "ifc"


# --- --root-scoped sweep: the other site is left `not-recorded` (by absence) ----------------------


def test_root_scoped_sweep_never_touches_the_other_site(published_v1):
    store, names1 = published_v1
    result = _sweep(store, root=names1["SiteA"])

    assert result.covered_roots == (names1["SiteA"],)
    by_ref = {r.node_ref for r in result.rows}
    assert names1["SiteB"] not in by_ref  # not-recorded, by absence -- never 'current'
    for name in ("StoreyB1", "StoreyB2"):
        assert names1[name] not in by_ref


def test_unknown_root_is_refused(published_v1):
    store, _names1 = published_v1
    with pytest.raises(IfcSweepError, match="no published manifest"):
        _sweep(store, root="not-a-real-guid")


# --- no-feed is a third, distinct state -- never answered as `current` ----------------------------


def test_no_database_is_no_feed_never_current():
    """`_source_nodes_pool` (`routes/source_nodes.py`) is the read side's only source of truth for
    whether a feed exists at all: no `db_pool` on `app.state` is refused with 503, not answered
    with an empty-but-200 'nothing changed'. A sweep's rows are meaningless without somewhere to
    record them -- this is the third 'cannot say' state Decision 4 lists alongside `behind` /
    `current` / `not-recorded`, and it must never collapse into `current`."""

    class _State:
        db_pool = None

    class _App:
        state = _State()

    class _Request:
        app = _App()

    with pytest.raises(HTTPException) as exc_info:
        _source_nodes_pool(_Request())
    assert exc_info.value.status_code == 503


# --- the worker entry: sweep + hand rows to an injected recorder ----------------------------------


def test_run_ifc_sweep_records_through_the_injected_recorder(published_v1):
    store, names1 = published_v1
    facade = FakeSyncStorageFacade(store)

    recorded: list[tuple[str, list]] = []

    def _record(source: str, rows: list) -> int:
        recorded.append((source, rows))
        return len(rows)

    written = run_ifc_sweep(
        storage=facade,
        record=_record,
        collection="plant-a",
        staged_key=V2_STAGED,
        extracted_at=V2_INSTANT,
    )

    assert written > 0
    assert len(recorded) == 1
    source, rows = recorded[0]
    assert source == "ifc"
    node_refs = {r["node_ref"] for r in rows}
    assert names1["aa-bm0"] in node_refs
    assert names1["SiteB"] in node_refs
    # migration 030's contract: action is present only where the sweep has a verdict
    modified_row = next(r for r in rows if r["node_ref"] == names1["aa-bm0"])
    assert modified_row["action"] == "modified"
    site_b_row = next(r for r in rows if r["node_ref"] == names1["SiteB"])
    assert "action" not in site_b_row

"""Procedural-model names that carry a folder path.

A model is a database row, not a blob, so it has no natural place in the
storage tree — but the browser shows it beside real files and an operator
reasonably wants it filed with them. Rather than a second hierarchy (a
``folder`` column only these rows have, which every listing and move would then
special-case), the NAME carries the path and the existing tree builder does the
rest.

That is only safe because a model is addressed by UUID everywhere: no route
interpolates the name, so a ``/`` in it escapes nothing. These tests pin both
halves — the normalisation, and that a move is a rename.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.procedural import (  # noqa: E402
    MAX_MODEL_NAME_DEPTH,
    model_name_folder,
    normalize_model_name,
)

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests"
)


# ── normalisation (no database needed) ─────────────────────────────


def test_a_plain_name_is_unchanged():
    assert normalize_model_name("module-a") == "module-a"


def test_a_path_is_kept():
    assert normalize_model_name("decks/level-3/module-a") == "decks/level-3/module-a"


def test_the_shapes_people_actually_type_are_normalised_not_refused():
    # Each of these has one obvious intended meaning; refusing them would be
    # pedantry dressed as validation.
    assert normalize_model_name("  decks/level-3/  ") == "decks/level-3"
    assert normalize_model_name("/decks/module-a") == "decks/module-a"
    assert normalize_model_name("decks//module-a") == "decks/module-a"
    assert normalize_model_name("decks / level-3 / module-a") == "decks/level-3/module-a"
    # Pasted from a Windows path.
    assert normalize_model_name("decks\\level-3\\module-a") == "decks/level-3/module-a"


def test_an_empty_name_is_refused():
    for bad in ("", "   ", "/", "///"):
        with pytest.raises(ValueError):
            normalize_model_name(bad)


def test_dot_segments_are_refused():
    # Not a traversal risk — this is not a path — but "../x" as a tree label
    # reads as navigation and is not.
    for bad in ("../escape", "decks/../x", "decks/./x"):
        with pytest.raises(ValueError):
            normalize_model_name(bad)


def test_absurd_nesting_is_refused():
    ok = "/".join(f"d{i}" for i in range(MAX_MODEL_NAME_DEPTH))
    assert normalize_model_name(ok) == ok
    with pytest.raises(ValueError):
        normalize_model_name("/".join(f"d{i}" for i in range(MAX_MODEL_NAME_DEPTH + 1)))


def test_folder_part_is_derivable():
    assert model_name_folder("decks/level-3/module-a") == "decks/level-3"
    assert model_name_folder("module-a") == ""


# ── moving is renaming (live Postgres) ─────────────────────────────


@pytest.fixture
def db():
    loop = asyncio.new_event_loop()

    def run(coro):
        return loop.run_until_complete(coro)

    p = run(db_module.init_pool(POSTGRES_URL))
    assert p is not None
    run(p.execute("DELETE FROM procedural_models"))
    try:
        yield p, run
    finally:
        run(p.close())
        loop.close()


def _make(run, pool, name):
    return run(db_module.create_procedural_model(pool, scope_kind="shared", scope_id=None, name=name, created_by=None))


@needs_postgres
def test_a_move_is_a_rename(db):
    pool, run = db
    m = _make(run, pool, "module-a")
    out = run(db_module.rename_procedural_model(pool, m["id"], "decks/level-3/module-a"))
    assert out["name"] == "decks/level-3/module-a"
    assert out["id"] == m["id"], "a move must not create a new model"


@needs_postgres
def test_two_models_cannot_occupy_one_path(db):
    """The scope-unique index is the same collision a filesystem would report."""
    pool, run = db
    _make(run, pool, "decks/module-a")
    other = _make(run, pool, "module-b")
    assert run(db_module.rename_procedural_model(pool, other["id"], "decks/module-a")) is False


@needs_postgres
def test_renaming_an_unknown_model_is_not_a_silent_success(db):
    pool, run = db
    missing = "00000000-0000-0000-0000-000000000000"
    assert run(db_module.rename_procedural_model(pool, missing, "x")) is None


@needs_postgres
def test_the_same_leaf_name_can_live_in_two_folders(db):
    """The point of the feature: uniqueness is on the PATH, not the leaf."""
    pool, run = db
    a = _make(run, pool, "decks/a/model")
    b = _make(run, pool, "decks/b/model")
    assert a is not None and b is not None
    names = {m["name"] for m in run(db_module.list_procedural_models(pool, scope_kind="shared", scope_id=None))}
    assert names == {"decks/a/model", "decks/b/model"}

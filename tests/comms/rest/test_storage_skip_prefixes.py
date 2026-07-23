"""storage.list(skip_prefixes=...) — don't enumerate namespaces the caller is about to discard.

The file browser's default listing hides ``_derived/`` / ``_overlays/`` / ``_reconvert/``. It used
to get them anyway and filter afterwards, which costs the whole namespace: the audit corpus holds
~28k derived blobs against 141 real files, so listing it took ~1.6 s to return 9 KB while a scope
with the same file count but no audit history answered in 30 ms — an O(audit history) cost on a UI
click.

The risk in skipping rather than filtering is a real file disappearing from the browser, so these
assert BOTH directions: the hidden namespace is skipped, and every genuine file still arrives.
"""

from __future__ import annotations

import pytest
from obstore.store import LocalStore

from ada.comms.rest.converter import HIDDEN_PREFIXES, is_hidden_key
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage


def _scope() -> Scope:
    # A corpus scope: the one this actually bites, since audit runs are what fill _derived/.
    return Scope(kind="corpus", id="basic")


async def _seed(storage: Storage, scope: Scope) -> None:
    for key in (
        "cad/step/a.stp",
        "cad/step/b.stp",
        "fem/model.rmed",
        "root_level.stp",  # a file directly at the scope root
        "_derived/cad/step/a.stp/glb/model.glb",
        "_derived/cad/step/b.stp/glb/model.glb",
        "_overlays/a.diff.glb",
        "_reconvert/a.json",
        "_procedural/model-1/r3.glb",
    ):
        await storage.put_bytes(scope, key, b"x")


@pytest.mark.asyncio
async def test_skip_prefixes_hides_internal_namespaces_and_keeps_everything_else(tmp_path):
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = _scope()
    await _seed(storage, scope)

    keys = {f.key for f in await storage.list(scope, skip_prefixes=HIDDEN_PREFIXES)}

    assert keys == {"cad/step/a.stp", "cad/step/b.stp", "fem/model.rmed", "root_level.stp"}
    # the root-level file is the easy one to lose in a delimiter walk (it is an `object`, not a
    # `common_prefix`), so pin it explicitly
    assert "root_level.stp" in keys
    assert not any(is_hidden_key(k) for k in keys)


@pytest.mark.asyncio
async def test_default_list_is_unchanged_and_still_sees_everything(tmp_path):
    """No skip_prefixes => the old behaviour, byte for byte. storage_ops' orphan cleanup builds its
    live-key set from this call and MUST still see the derived blobs it reaps."""
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = _scope()
    await _seed(storage, scope)

    keys = {f.key for f in await storage.list(scope)}

    assert "_derived/cad/step/a.stp/glb/model.glb" in keys
    assert "_overlays/a.diff.glb" in keys
    assert "_reconvert/a.json" in keys
    assert "_procedural/model-1/r3.glb" in keys
    assert len(keys) == 9


@pytest.mark.asyncio
async def test_skip_and_filter_agree(tmp_path):
    """The skipped set and the filtered set must be the same set — if skip_prefixes drifted from
    is_hidden_key, a real file would silently vanish from the browser."""
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = _scope()
    await _seed(storage, scope)

    skipped = {f.key for f in await storage.list(scope, skip_prefixes=HIDDEN_PREFIXES)}
    filtered = {f.key for f in await storage.list(scope) if not is_hidden_key(f.key)}

    assert skipped == filtered


@pytest.mark.asyncio
async def test_entries_carry_size_and_key_through_the_walk(tmp_path):
    """The delimiter walk rebuilds entries per prefix; the shape must survive it."""
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = _scope()
    await storage.put_bytes(scope, "cad/step/a.stp", b"hello")

    (entry,) = await storage.list(scope, skip_prefixes=HIDDEN_PREFIXES)
    assert entry.key == "cad/step/a.stp"
    assert entry.size == 5

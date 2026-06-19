"""Worker SIF reduced-source path (index sidecar + range-fetch).

The worker's ``_try_reduced_sif_source`` range-fetches only one result step of
a SIF deck (using the cached byte-offset index) into a reduced, still-valid
SIF; ``_ensure_sif_index`` builds + caches that index after the first full
conversion. These tests drive both against a LocalStore — the same backend the
no-DB deployments run — and assert the reduced file the worker assembles reads
identically to a full single-step read.
"""

from __future__ import annotations

import asyncio
import pathlib

import numpy as np
import pytest
from obstore.store import LocalStore

from ada.comms.rest import worker as worker_mod
from ada.comms.rest.converter import sif_index_key_for
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage
from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.formats.sesam.results.sif_index import SifStepIndex, build_sif_index

_EIGEN = "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF"  # 20 steps
SRC_KEY = "fea/eigen.SIF"


def _storage(tmp_path):
    return Storage(LocalStore(str(tmp_path)), prefix="")


def _disp(res):
    for r in res.results:
        if getattr(r, "name", None) == "RVNODDIS":
            return np.asarray(r.values)
    return None


def _upload_source(storage, scope, sif):
    asyncio.run(storage.put_bytes(scope, SRC_KEY, sif.read_bytes()))


def test_ensure_sif_index_builds_then_noops(tmp_path, fem_files):
    sif = fem_files / _EIGEN
    storage = _storage(tmp_path)
    scope = Scope.shared()
    _upload_source(storage, scope, sif)
    idx_key = sif_index_key_for(SRC_KEY)

    assert asyncio.run(storage.exists(scope, idx_key)) is False
    asyncio.run(worker_mod._ensure_sif_index(storage, scope, SRC_KEY, sif))
    assert asyncio.run(storage.exists(scope, idx_key)) is True

    idx = SifStepIndex.from_json(asyncio.run(storage.get_bytes(scope, idx_key)))
    assert idx.steps == list(range(1, 21))

    # Second call is a no-op (doesn't error, index unchanged).
    asyncio.run(worker_mod._ensure_sif_index(storage, scope, SRC_KEY, sif))
    idx2 = SifStepIndex.from_json(asyncio.run(storage.get_bytes(scope, idx_key)))
    assert idx2.step_spans == idx.step_spans


def test_reduced_source_no_index_falls_back(tmp_path, fem_files):
    # Without a cached index the worker must report False so the caller does a
    # full download.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    _upload_source(storage, scope, fem_files / _EIGEN)
    dst = tmp_path / "out.SIF"
    ok = asyncio.run(worker_mod._try_reduced_sif_source(storage, scope, SRC_KEY, None, dst))
    assert ok is False


def test_reduced_source_reads_one_step(tmp_path, fem_files):
    sif = fem_files / _EIGEN
    storage = _storage(tmp_path)
    scope = Scope.shared()
    _upload_source(storage, scope, sif)
    # Seed the index sidecar (as the first conversion would).
    asyncio.run(storage.put_bytes(scope, sif_index_key_for(SRC_KEY), build_sif_index(sif).to_json()))

    for step in (None, 4, 20):
        dst = tmp_path / f"reduced_{step}.SIF"
        ok = asyncio.run(worker_mod._try_reduced_sif_source(storage, scope, SRC_KEY, step, dst))
        assert ok is True
        assert dst.stat().st_size < sif.stat().st_size  # genuinely reduced

        want = step if step is not None else 1
        reduced = read_sif_file(dst)
        full = read_sif_file(sif, step=want)
        assert reduced.get_steps() == [want] == full.get_steps()
        assert np.allclose(_disp(reduced), _disp(full))


def test_reduced_source_unknown_step_falls_back(tmp_path, fem_files):
    sif = fem_files / _EIGEN
    storage = _storage(tmp_path)
    scope = Scope.shared()
    _upload_source(storage, scope, sif)
    asyncio.run(storage.put_bytes(scope, sif_index_key_for(SRC_KEY), build_sif_index(sif).to_json()))

    dst = tmp_path / "out.SIF"
    ok = asyncio.run(worker_mod._try_reduced_sif_source(storage, scope, SRC_KEY, 999, dst))
    assert ok is False

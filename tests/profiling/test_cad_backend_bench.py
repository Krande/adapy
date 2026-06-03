"""CAD-backend performance baseline.

Purpose: a regression gate for the OCC -> backend-abstraction migration
(see dap ``plan/v3/notes_occ_backend_abstraction.md``). Phase 1 reroutes
the tessellation pipeline through ``ada.cad.active_backend()``; this file
captures the *pre-Phase-1* cost of the hot paths so any per-shape overhead
the seam introduces shows up as a measurable delta rather than a guess.

Three benchmarks, each isolating a distinct seam:

* ``test_bench_batch_tessellate`` — the Phase 1 hot path: build OCC geometry
  from parametric objects + tessellate to mesh buffers. Fresh objects every
  round so the OCC build is actually exercised (not skipped by the guid
  cache). This is the primary gate.
* ``test_bench_solid_occ_cache_build`` — the Phase 0 seam
  (``ada.occ.geom.cache.get_solid_occ`` -> ``active_backend().build``) on a
  cold cache. Confirms the construction funnel stays flat.
* ``test_bench_solid_occ_cache_hit`` — pure cache-hit cost: the per-access
  ``active_backend()`` call + ``f"{name}:{guid}"`` key build. Should be
  microsecond-scale; this is the overhead Phase 0 actually added.

Run + save a baseline BEFORE starting Phase 1::

    pixi run -e tests bench-cad-save

After Phase 1, compare against it and fail on a >10% mean regression::

    pixi run -e tests bench-cad-compare

(Both wrap pytest-benchmark's --benchmark-save / --benchmark-compare; the
baselines live under tests/profiling/.benchmarks/ — or the repo .benchmarks/.)

Not run by ``pixi run test`` (it ignores tests/profiling), so this never
slows the normal suite or CI.
"""

import pytest

import ada
from ada.occ.geom.cache import clear_all, get_solid_occ
from ada.occ.tessellating import BatchTessellator

# Per-type instance counts. Kept modest so each round is a few seconds:
# enough parts to average out per-shape noise, few enough to stay quick.
N_PER_TYPE = 60

_PLATE_PTS = [(0, 0), (1, 0), (1, 1), (0, 1)]


def _make_objects() -> list:
    """A representative mixed bag exercising the distinct OCC build paths:
    swept I-profile (Beam), planar shell (Plate), boxed prim (PrimBox) and
    a curved prim (PrimSphere). All implement ``solid_geom()``."""
    objs: list = []
    for i in range(N_PER_TYPE):
        objs.append(ada.Beam(f"bm{i}", (i, 0, 0), (i + 1, 0, 0), "IPE300"))
        objs.append(ada.Plate(f"pl{i}", _PLATE_PTS, 0.01, origin=(0, 0, i)))
        objs.append(ada.PrimBox(f"bx{i}", (0, 0, i), (0.5, 0.5, i + 0.5)))
        objs.append(ada.PrimSphere(f"sp{i}", (0, 0, i), 0.2))
    return objs


@pytest.mark.benchmark(group="cad-backend")
def test_bench_batch_tessellate(benchmark):
    """Phase 1 hot path — OCC build + tessellate, fresh objects per round."""

    def setup():
        # Fresh objects (new guids) so the build is not served from cache,
        # and a clean cache so nothing leaks between rounds.
        clear_all()
        return (_make_objects(),), {}

    def run(objects):
        return list(BatchTessellator().batch_tessellate(objects))

    meshes = benchmark.pedantic(run, setup=setup, rounds=5, iterations=1, warmup_rounds=1)
    assert len(meshes) == N_PER_TYPE * 4


@pytest.mark.benchmark(group="cad-backend")
def test_bench_solid_occ_cache_build(benchmark):
    """Phase 0 seam — cold-cache build through active_backend().build()."""

    def setup():
        clear_all()
        return (_make_objects(),), {}

    def run(objects):
        return [get_solid_occ(o) for o in objects]

    solids = benchmark.pedantic(run, setup=setup, rounds=5, iterations=1, warmup_rounds=1)
    assert len(solids) == N_PER_TYPE * 4
    assert all(s is not None for s in solids)


# Each timed cache-hit sample repeats the full lookup pass this many times.
# A single pass over the objects is only ~50 us — too small to hold a 10%
# regression gate under OS scheduling jitter (it flaked 2-in-3). Repeating it
# lifts each sample into the ms range where fixed jitter is <10% relative, so
# the gate is meaningful instead of noisy. (Per-lookup cost = mean / passes.)
_HIT_PASSES = 50


@pytest.mark.benchmark(group="cad-backend")
def test_bench_solid_occ_cache_hit(benchmark):
    """Phase 0 per-access overhead — warm-cache lookups only (the f-string
    key build + memoized active_backend() call), no OCC work."""
    clear_all()
    objects = _make_objects()
    # Prime the cache once; every timed call below is a pure hit.
    for o in objects:
        get_solid_occ(o)

    def run():
        solids = None
        for _ in range(_HIT_PASSES):
            solids = [get_solid_occ(o) for o in objects]
        return solids

    solids = benchmark.pedantic(run, rounds=10, iterations=3, warmup_rounds=2)
    assert len(solids) == N_PER_TYPE * 4

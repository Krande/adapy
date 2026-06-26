from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import trimesh

    from ada.visit.scene_converter import SceneConverter


@dataclass
class StepStreamSource:
    """Marks a :class:`SceneConverter` source as a STEP file to convert by *streaming*
    — one solid at a time, bounded memory — instead of loading the whole Assembly.

    The reader yields one geometry per solid (with its STEP colour); each is
    tessellated and its light mesh buffers are accumulated per material, while the
    heavy B-rep geometry is dropped before the next solid. The result is the same
    merged-by-colour scene + design-graph the normal ``to_gltf`` path produces.

    ``on_progress(stage, frac)`` is called periodically through the (slow) per-solid
    tessellation so a worker can report real progress instead of sitting at one stage.
    """

    path: str | Path
    tolerant: bool = True
    on_progress: Callable[[str, float], None] | None = None
    # Merge a product's redundant same-name nesting (a product instanced N times, or a lone
    # solid under a same-named group) into one node / pickable object (triangles reordered
    # to one contiguous draw-range). Default OFF — one node per solid, byte-identical to the
    # pre-merge output. Opt in here or via env ADA_MERGE_SAME_NAME_SIBLINGS=1.
    merge_same_name_siblings: bool = False


# Below this solid count the ~1 s process-pool spawn overhead outweighs the
# parallelism, so the conversion runs sequentially.
_POOL_MIN_SOLIDS = 500

# Every this many solids a tessellation worker (and the sequential loop) runs an
# explicit gc + glibc ``malloc_trim`` so the native OCC heap freed per solid is returned
# to the OS instead of fragmenting upward across the worker's lifetime (a TopoDS_Shape's
# C++ memory is reclaimed on refcount-0, but glibc keeps the arena — see _maybe_trim).
_TRIM_EVERY = 256


def _malloc_trim_fn():
    """Resolve glibc's ``malloc_trim`` once, or None off Linux/glibc."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=False)
        return libc.malloc_trim
    except Exception:  # noqa: BLE001 - not glibc / not available
        return None


_MALLOC_TRIM = _malloc_trim_fn()


def _maybe_trim() -> None:
    """Force-release per-solid garbage: a Python ``gc`` pass (breaks any OCP refcycles)
    then ``malloc_trim(0)`` to hand the freed native arena back to the OS. Without the
    trim a long-lived tessellation worker's RSS climbs monotonically even though every
    solid's OCC shape is dropped — glibc retains the freed heap."""
    import gc

    gc.collect()
    if _MALLOC_TRIM is not None:
        try:
            _MALLOC_TRIM(0)
        except Exception:  # noqa: BLE001
            pass


# Test-hook sink for ADA_STEP_STREAM_TEST_ALLOC_MB allocations (kept referenced
# so worker RSS genuinely grows; never populated outside tests).
_TEST_ALLOCS: list = []


def _rebuild_stats():
    """This solid's face-repair counters from the OCC backend (param-extent rebuilds,
    re-added/dropped holes, area-gate drops), or None on backends without the module
    (adacpp-only installs have no ada.occ)."""
    try:
        from ada.occ.geom.surfaces import (
            consume_face_coverage_stats,
            consume_param_rebuild_stats,
        )
    except ImportError:
        return None
    stats = consume_param_rebuild_stats()
    # Fold the per-face build coverage (total/built/dropped) in under faces_* keys so
    # it aggregates into the run summary's face_coverage without a second build.
    for k, v in consume_face_coverage_stats().items():
        stats[f"faces_{k}"] = stats.get(f"faces_{k}", 0) + v
    return stats or None


def _maybe_capture_empty_solid(geom) -> None:
    """When ADA_CAPTURE_EMPTY_SOLIDS=<dir> is set, pickle a solid that built but
    tessellated to zero triangles (built-but-unmeshed) for offline diagnosis. No-op by
    default; never raises. Source-derived — keep captures local, drive synthetic fixtures."""
    import os

    out_dir = os.environ.get("ADA_CAPTURE_EMPTY_SOLIDS")
    if not out_dir:
        return
    try:
        import hashlib
        import pickle

        blob = pickle.dumps(geom)
        tag = hashlib.sha1(blob).hexdigest()[:12]
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"empty_{tag}.pkl"), "wb") as fh:
            fh.write(blob)
    except Exception:  # noqa: BLE001 - capture is best-effort
        pass


def _maybe_capture_timeout_solid(geom) -> None:
    """When ADA_CAPTURE_TIMEOUT_SOLIDS=<dir> is set, pickle a solid whose worker
    overran the per-solid timeout (slow OCC build/tessellation) for offline diagnosis.
    No-op by default; never raises. Source-derived — keep captures local."""
    import os

    out_dir = os.environ.get("ADA_CAPTURE_TIMEOUT_SOLIDS")
    if not out_dir or geom is None:
        return
    try:
        import hashlib
        import pickle

        blob = pickle.dumps(geom)
        tag = hashlib.sha1(blob).hexdigest()[:12]
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"timeout_{tag}.pkl"), "wb") as fh:
            fh.write(blob)
    except Exception:  # noqa: BLE001 - capture is best-effort
        pass


def _tessellate_geom_worker(geom):
    """Pool subprocess entry: build + tessellate ONE solid and return raw mesh arrays
    plus the solid's colour — the parent assigns a consistent material id (each
    subprocess has its own material store). Returns
    ``(status, gid, color, positions, indices, normals, transforms, paths, rebuilds)``
    where ``status`` is "ok", "degenerate", "empty" or "error:<Type>"; ``transforms`` is
    the solid's list of world-placement matrices (or None) — the parent meshes once and
    places per matrix — ``paths`` the matching per-instance assembly paths, and
    ``rebuilds`` the per-solid face-repair counters (param-extent rebuilds, dropped
    holes, area-gate drops) so the conversion summary can quantify fidelity loss.
    OCC isn't thread-safe, so the unit of parallelism is a process."""
    import numpy as np

    gid = None
    occ = None
    mesh = None
    try:
        import os

        _hang = os.environ.get("ADA_STEP_STREAM_TEST_HANG_S")  # test hook: simulate an OCC hang
        _alloc = os.environ.get("ADA_STEP_STREAM_TEST_ALLOC_MB")  # test hook: simulate native bloat
        if _alloc:
            # Retained on purpose (module-level) so the worker's RSS stays high
            # AFTER the solid completes — exercises the soft-cap recycle — and
            # DURING the (optionally extended) task for the hard-cap watchdog.
            _TEST_ALLOCS.append(bytearray(int(float(_alloc)) * 1024 * 1024))
        if _hang:
            import time

            time.sleep(float(_hang))

        from ada.cad import active_backend

        be = active_backend()
        gid = str(geom.id) if geom.id not in (None, "") else None

        # OCC-free libtess2 path (ADA_STREAM_TESS_PIPELINE=libtess2|occ|cgal|hybrid): serialize the
        # ada.geom to the NGEOM buffer and tessellate in one adacpp call — no per-solid OCC
        # build/ShapeHandle round-trip. The rest of the streaming export (spill + merge-by-colour +
        # ADA_EXT_data + picking) is unchanged, so it stays memory-bounded and contract-compliant.
        _pipeline = os.environ.get("ADA_STREAM_TESS_PIPELINE")
        if _pipeline and hasattr(be, "tessellate_stream"):
            defl = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", "2.0"))
            ang = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", "20.0"))
            gi = geom.geometry.geometry if hasattr(geom.geometry, "geometry") else geom.geometry
            bm = be.tessellate_stream([(gid or "0", gi)], pipeline=_pipeline, deflection=defl, angular_deg=ang)
            _pos = getattr(bm, "positions", None)
            _idx = getattr(bm, "indices", None)
            if _pos is None or _idx is None or len(_idx) == 0:
                _maybe_capture_empty_solid(geom)
                return ("empty", gid, geom.color, None, None, None, None, None, _rebuild_stats())
            pos = np.ascontiguousarray(_pos, dtype=np.float32)
            idx = np.ascontiguousarray(_idx, dtype=np.uint32)
            # Optional step2glb merge cleanup (ADA_STREAM_SIMPLIFY=1): meshopt_simplify each unique
            # mesh once, border-locked, lossless at target-error 0 (coplanar collapse).
            if os.environ.get("ADA_STREAM_SIMPLIFY") and len(idx) >= 3:
                try:
                    import adacpp.cad as _cad

                    sp, si = _cad.meshopt_simplify_mesh(
                        pos.reshape(-1),
                        idx.reshape(-1),
                        float(os.environ.get("ADA_STREAM_SIMPLIFY_THRESHOLD", "0.75")),
                        float(os.environ.get("ADA_STREAM_SIMPLIFY_TARGET_ERROR", "0.0")),
                    )
                    pos = np.ascontiguousarray(sp, dtype=np.float32)
                    idx = np.ascontiguousarray(si, dtype=np.uint32)
                except Exception:  # noqa: BLE001 - cleanup is best-effort; keep the raw mesh
                    pass
            if len(idx) == 0:
                _maybe_capture_empty_solid(geom)
                return ("empty", gid, geom.color, None, None, None, None, None, _rebuild_stats())
            # libtess2 emits no per-vertex normals (viewer flat-shades / computes its own) — matches
            # step2glb's normal-free merged output and keeps the GLB lean.
            return ("ok", gid, geom.color, pos, idx, None, geom.transforms, geom.instance_paths, _rebuild_stats())

        occ = be.build(geom)
        # Zero-extent solid -> OCC's relative mesher throws an uncatchable terminate.
        try:
            bb = be.bbox(occ)
            diag = ((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2) ** 0.5
        except Exception:
            diag = 0.0
        if diag < 1e-7:
            return ("degenerate", gid, geom.color, None, None, None, None, None, _rebuild_stats())
        mesh = be.tessellate(occ)
        idx = getattr(mesh, "indices", None)
        if idx is None:
            idx = getattr(mesh, "faces", None)
        pos = getattr(mesh, "positions", None)
        if pos is None or idx is None or len(idx) == 0:
            _maybe_capture_empty_solid(geom)
            return ("empty", gid, geom.color, None, None, None, None, None, _rebuild_stats())
        nrm = getattr(mesh, "normals", None)
        # ascontiguousarray(+dtype) COPIES, so pos/idx/nrm no longer reference any OCC
        # buffer — the shape + its triangulation can be dropped immediately below.
        pos = np.ascontiguousarray(pos, dtype=np.float32)
        idx = np.ascontiguousarray(idx, dtype=np.uint32)
        nrm = np.ascontiguousarray(nrm, dtype=np.float32) if nrm is not None else None
        # The shell is tessellated ONCE in its local frame. The world-placement matrices
        # (one per STEP assembly instance) ride back to the parent, which applies each to
        # this single local mesh — so a part instanced N times still meshes once.
        return ("ok", gid, geom.color, pos, idx, nrm, geom.transforms, geom.instance_paths, _rebuild_stats())
    except Exception as exc:  # noqa: BLE001 - report and skip; one bad solid mustn't abort
        return (f"error:{type(exc).__name__}", gid, None, None, None, None, None, None, _rebuild_stats())
    finally:
        # Purge this task's OCC instances now (refcount-0 frees the native shape +
        # triangulation) rather than relying on end-of-call scope cleanup — keeps a
        # long-lived worker from carrying a solid's geometry into the next iteration.
        del occ, mesh


def _pool_worker_loop(worker_id, task_q, result_q, stream_index) -> None:
    """Long-lived pool worker: open the per-process pread pool ONCE from the shared
    (pickled) ``StreamIndex``, then for each ``(seq, rid)`` build the solid's ada.geom AND
    tessellate it — the parse+build now happens HERE, in parallel, not serially in the
    parent. Puts ``(worker_id, result)`` back. ``None`` is the shutdown sentinel.

    A ``"__drop__"`` status means the reader couldn't build that root; the parent keeps it
    out of the stats, exactly as the serial reader silently drops it. The worker_id lets
    the parent free the right slot and — crucially — terminate THIS worker if it overruns
    the per-solid timeout (a tessellation can hang in an uninterruptible C call)."""
    import collections

    from ada.cadit.step.read.stream_reader import build_one_solid

    pool, resolver = stream_index.open_pool()
    skipped: collections.Counter = collections.Counter()  # worker-local reader-skips (not reported)
    _DROP = ("__drop__", None, None, None, None, None, None, None, None)
    try:
        n = 0
        while True:
            task = task_q.get()
            if task is None:
                return
            seq, rid = task
            geom = build_one_solid(stream_index, pool, resolver, rid, seq, skipped=skipped)
            result_q.put((worker_id, _DROP if geom is None else _tessellate_geom_worker(geom)))
            n += 1
            if n % _TRIM_EVERY == 0:  # return the per-solid build+tess heap to the OS periodically
                _maybe_trim()
    finally:
        pool.close()


def _rss_mb(pid: int | None = None) -> float:
    """ANONYMOUS resident memory of ``pid`` (or this process) in MB via /proc; 0.0 where
    /proc is unavailable (non-Linux) — which renders the memory caps inert there.

    Reports ``RssAnon`` (the real, non-reclaimable heap: Python objects, numpy mesh
    buffers, native-allocator fragmentation), NOT ``VmRSS``. Since the build moved into
    the workers, each worker memmaps the shared id/offset index (file-backed, reclaimable,
    counted once physically but in every worker's ``VmRSS``); bounding ``VmRSS`` would
    recycle workers on that reclaimable baseline — which scales with FILE size — rather
    than on real heap growth. ``RssAnon`` is what actually threatens a pod's OOM budget and
    is file-size-independent. Falls back to ``VmRSS`` on kernels without ``RssAnon``."""
    vmrss = None
    try:
        with open(f"/proc/{pid or 'self'}/status") as f:
            for line in f:
                if line.startswith("RssAnon:"):
                    return int(line.split()[1]) / 1024.0
                if line.startswith("VmRSS:"):
                    vmrss = int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return vmrss if vmrss is not None else 0.0


def _env_mb(name: str, default: float) -> float:
    """A memory threshold in MB from the environment. 0 (or any non-positive /
    unparsable value) DISABLES the mechanism — the pool then behaves exactly as
    it did before memory caps existed."""
    import os

    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def _worker_mem_caps() -> tuple[float, float]:
    """(soft_mb, hard_mb) for the tessellation workers.

    Soft cap (``ADA_STEP_STREAM_WORKER_SOFT_MEM_MB``, default 800): a worker that
    still exceeds this BETWEEN solids (after gc + malloc_trim) exits cleanly and
    is respawned fresh on the next dispatch — nothing is lost, the pending solids
    live with the parent and simply go to the next available worker. This bounds
    the native-heap fragmentation that trim alone can't fully return.

    Hard cap (``ADA_STEP_STREAM_WORKER_HARD_MEM_MB``, default 1600): a worker that
    crosses this MID-solid is killed and the in-flight solid is requeued ONCE on a
    fresh worker (fragmentation-driven overruns succeed on retry); if the retry
    crosses the cap again the solid itself needs that memory and is skipped with a
    ``memory`` reason. Keeps one runaway solid from blowing a memory-tight pod's
    whole per-job budget.

    Setting either env to 0 disables that mechanism entirely."""
    return (
        _env_mb("ADA_STEP_STREAM_WORKER_SOFT_MEM_MB", 800.0),
        _env_mb("ADA_STEP_STREAM_WORKER_HARD_MEM_MB", 1600.0),
    )


def _per_solid_timeout_s() -> float:
    """Wall-clock budget for tessellating a single solid before its worker is killed and
    the solid skipped. ``ADA_STEP_STREAM_SOLID_TIMEOUT_S`` overrides; default 120 s is
    far above any healthy solid (sub-second to a few seconds) but bounds a hang."""
    import os

    raw = os.environ.get("ADA_STEP_STREAM_SOLID_TIMEOUT_S")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 120.0


def _cgroup_cpu_quota() -> int | None:
    """The pod's CPU limit from its cgroup (CFS quota), or None. k8s CPU *limits* are
    CFS quota, not cpuset, so ``sched_getaffinity`` would report the whole node — using
    that to size a process pool would oversubscribe the pod. Handles cgroup v2 and v1."""
    try:  # cgroup v2
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota, period = f.read().split()
        if quota != "max" and int(period) > 0:
            return max(1, round(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") as f:
            quota = int(f.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us") as f:
            period = int(f.read())
        if quota > 0 and period > 0:
            return max(1, round(quota / period))
    except (OSError, ValueError):
        pass
    return None


def _stream_workers() -> int:
    """Number of tessellation worker processes. ``ADA_STEP_STREAM_WORKERS`` overrides
    (verbatim); otherwise the pod's cgroup CPU limit (falling back to schedulable CPUs)
    MINUS one, capped at 3.

    The cap is a *memory* bound, not a throughput one: each worker holds a chunk of the
    model's tessellated mesh in flight back to the parent, so peak RSS scales ~linearly
    with worker count. On the crane (26 M tris) 8 workers peaked ~6.2 GB and 4 ~4.7 GB;
    3 keeps it near a ~4 GB pod ceiling (the spill-bounded parent alone is ~2.1 GB). Bump
    ``ADA_STEP_STREAM_WORKERS`` on a roomier pod to trade RAM for speed.

    The ``- 1`` is load-bearing, not just polite: when this runs inside the conversion
    worker, the parent process must keep its asyncio event loop responsive to refresh
    the JetStream ``in_progress`` lease (every 30 s, within a 180 s ``ack_wait``). A pool
    that pins every core starves that loop, the lease expires, JetStream redelivers the
    still-running job, the worker spawns ANOTHER conversion + pool, and it cascades into
    a redelivery storm. Reserving a core keeps the heartbeat alive. (Each worker also
    holds one solid's OCC shape, so the cap bounds memory too.)"""
    import os

    env = os.environ.get("ADA_STEP_STREAM_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    n = _cgroup_cpu_quota()
    if n is None:
        try:
            n = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            n = os.cpu_count() or 1
    return max(1, min(n - 1, 3))


def _leaf_product_name(paths) -> str | None:
    """The STEP product name of a solid's own (deepest) assembly level.

    Each path is a root-first tuple of ``(rep_id, product_name)`` levels from the
    stream reader; the last is the solid's leaf product. Returns the first real
    product name found (ignoring ``asm_<id>`` placeholders for unnamed reps), or
    None when no path carries one."""
    for path in paths or ():
        if path:
            name = path[-1][1]
            if isinstance(name, str) and name and not name.startswith("asm_"):
                return name
    return None


def _merge_siblings_enabled(source: "StepStreamSource") -> bool:
    """Whether redundant same-name product nesting is merged into one node/object.

    Default OFF (one node per solid, pre-merge behaviour). Opt in via the source flag or
    the ``ADA_MERGE_SAME_NAME_SIBLINGS`` env var, which OVERRIDES the source flag when set
    (``1``/``true`` on, ``0``/``false`` off) so a conversion worker can toggle it without
    code changes."""
    import os

    env = os.environ.get("ADA_MERGE_SAME_NAME_SIBLINGS")
    if env is not None and env != "":
        return env.lower() not in ("0", "false", "no")
    return bool(getattr(source, "merge_same_name_siblings", False))


def _tessellate_stream(source: StepStreamSource, graph, bt, sink) -> dict:
    """Shared streaming tessellation loop used by both the trimesh-scene path
    (:func:`scene_from_step_stream`) and the disk-spilled GLB path
    (:func:`convert_step_stream_to_glb`).

    Reads the STEP one solid at a time (pooled or sequential), applies each assembly
    instance's world placement, creates a graph node per instance, resolves its material
    id, and hands ``(mat_id, node_ref, pos, idx, nrm)`` to ``sink``. Returns the stats
    dict ``{"meshed", "total", "skipped", "materials", "reasons"}``."""
    import collections

    import numpy as np

    from ada.cadit.step.read.stream_reader import build_one_solid, prepare_stream_index
    from ada.config import logger
    from ada.core.guid import create_guid
    from ada.occ.geom.cache import clear_all
    from ada.visit.gltf.graph import GraphNode

    # No sibling code path should have left OCC shapes pinned in the process-global
    # caches for this conversion (the streaming build path never populates them, but be
    # explicit so a prior in-process conversion can't leak into this one).
    clear_all()

    # glTF mandates metres; STEP files are very often authored in millimetres. The OCC
    # reader converts via xstep.cascade.unit — mirror that here or a mm model renders
    # 1000x too big, kilometres off-centre, and the viewer's depth precision collapses.
    from ada.cadit.step.read.stream_reader import detect_step_length_unit_scale

    unit_scale = detect_step_length_unit_scale(source.path)
    if unit_scale != 1.0:
        logger.info("scene_from_step_stream: scaling length unit to metres (factor %g)", unit_scale)

    root = graph.top_level
    on_progress = source.on_progress

    reasons: collections.Counter = collections.Counter()
    rebuild_totals: collections.Counter = collections.Counter()
    pool_events = {"worker_recycles": 0, "mem_kills": 0}
    skipped_ids: list[str] = []
    n_total = 0
    n_roots = {"total": 0}

    def _skip(gid: str, reason: str) -> None:
        reasons[reason] += 1
        if len(skipped_ids) < 50:
            skipped_ids.append(gid)
        logger.debug("scene_from_step_stream: skipped %s — %s", gid, reason)

    def _on_total(n: int) -> None:
        n_roots["total"] = n

    # Assembly-tree group nodes, keyed by the path's rep-id prefix (names alone can
    # repeat across branches). Created lazily as instances arrive, so the GLB's
    # id_hierarchy mirrors the STEP product tree and the viewer can fold whole
    # sub-assemblies instead of scrolling a flat list of thousands of solids.
    asm_nodes: dict[tuple, object] = {}

    # Same-name nesting merge: when a solid's product reaches a group node named after the
    # product itself (a product instanced N times, or a lone solid under a same-named
    # group), the mesh attaches to that group node so the redundant gid/k child leaves
    # collapse into one pickable mesh. Disabled -> one node per solid, exactly as before.
    merge_siblings = _merge_siblings_enabled(source)

    def _group_parent(path) -> object:
        parent = root
        if not path:
            return parent
        prefix = ()
        for rep_id, name in path:
            prefix += (rep_id,)
            node_g = asm_nodes.get(prefix)
            if node_g is None:
                node_g = graph.add_node(GraphNode(name, graph.next_node_id(), hash=create_guid(), parent=parent))
                asm_nodes[prefix] = node_g
            parent = node_g
        return parent

    # The parent owns the material store (so colours map to consistent ids across worker
    # processes), creates the graph node from each worker's raw mesh arrays, and hands
    # the result to the caller's sink. Workers (subprocesses) only build + tessellate.
    def _build(gid, color, pos, idx, nrm, transform=None, path=None, collapse_leaf=False, merge_name=None) -> None:
        # Apply this instance's world placement to the local mesh (rigid: rotation +
        # translation on positions, rotation on normals). pos/nrm stay FLAT (N*3,) — the
        # MeshStore/spill path needs flat buffers — so reshape only for the matmul.
        if transform is not None:
            t = np.asarray(transform, dtype=np.float32)
            r = t[:3, :3]
            pos = np.ascontiguousarray((pos.reshape(-1, 3) @ r.T + t[:3, 3]).ravel(), dtype=np.float32)
            if nrm is not None:
                n = nrm.reshape(-1, 3) @ r.T
                # r may carry a uniform unit-scale (mixed-unit parts), which would
                # de-normalize the rotated normals — renormalize so they stay unit
                # (a no-op for a pure rotation).
                ln = np.linalg.norm(n, axis=1, keepdims=True)
                np.divide(n, ln, out=n, where=ln > 1e-12)
                nrm = np.ascontiguousarray(n.ravel(), dtype=np.float32)
        if unit_scale != 1.0:
            # Placement translations and positions are both in file units, so scaling
            # once AFTER the transform keeps them consistent. Normals are unaffected
            # by a uniform scale.
            pos = np.ascontiguousarray(pos * np.float32(unit_scale), dtype=np.float32)
        # Resolve (and lazily create) the group chain BEFORE asking for the next node
        # id — next_node_id() is len(nodes), so the reverse order would hand the leaf
        # an id that the first new group node then claims, silently evicting it.
        # When the solid was named after its own leaf product (collapse_leaf), the
        # deepest path level IS this solid — so group under path[:-1], making the
        # solid node the product node (matches step2glb) instead of nesting it under
        # a redundant same-named group.
        if merge_siblings and merge_name:
            # Merge ONLY the redundant same-name nesting: when a solid's product reaches a
            # group node named after the product itself (a product instanced N times, or a
            # lone solid under a same-named group), attach the mesh to that group node
            # instead of adding a gid/k child — so the group becomes one merged, pickable
            # mesh. Distinct products that merely share a name get distinct group reps, so
            # they are NOT merged; same-prefix instances already share the group node
            # (asm_nodes), so this fuses them automatically.
            parent = _group_parent(path)
            if parent is not root and getattr(parent, "name", None) == merge_name:
                node = parent
            else:
                node = graph.add_node(GraphNode(gid, graph.next_node_id(), hash=create_guid(), parent=parent))
        else:
            parent = _group_parent(path[:-1] if collapse_leaf and path else path)
            node = graph.add_node(GraphNode(gid, graph.next_node_id(), hash=create_guid(), parent=parent))
        mat_id = bt.material_store.get(color, None)
        if mat_id is None:
            mat_id = len(bt.material_store)
            bt.material_store[color] = mat_id
        sink(mat_id, node.hash, pos, idx, nrm)

    def _handle(result) -> None:
        nonlocal n_total
        status, gid, color, pos, idx, nrm, transforms, paths, rebuilds = result
        if rebuilds:
            rebuild_totals.update(rebuilds)
        i = n_total
        n_total += 1
        gid = gid or f"solid_{i}"
        # The stream reader names a solid after its owning STEP product, which is
        # also the deepest assembly path level. For a SINGLE-instance solid that
        # level would be a redundant same-named group above the solid, so collapse
        # it (the solid node becomes the product node, matching step2glb). For a
        # multi-instance solid the product group meaningfully holds its instances,
        # so keep it.
        n_inst = len(transforms) if transforms else 1
        collapse_leaf = n_inst == 1 and bool(gid) and _leaf_product_name(paths) == gid
        if status == "ok":
            # One mesh, N instances: tessellated once, placed per assembly matrix.
            tfs = transforms if transforms else [None]
            paths = paths if paths and len(paths) == len(tfs) else [None] * len(tfs)
            for k, (tf, path) in enumerate(zip(tfs, paths)):
                inst_gid = gid if k == 0 else f"{gid}/{k + 1}"
                # merge_name = the base product name (no /k suffix); when merging is on and
                # the instance's group node is named after the product, the mesh attaches to
                # that group node (the redundant gid/k leaves collapse into one mesh).
                _build(inst_gid, color, pos, idx, nrm, tf, path, collapse_leaf=collapse_leaf, merge_name=gid)
        elif status == "degenerate":
            _skip(gid, "degenerate (zero-extent solid)")
        elif status == "empty":
            _skip(gid, "empty mesh (no triangles)")
        else:
            _skip(gid, status)
        if on_progress is not None and n_roots["total"] and (i % 10 == 0):
            on_progress("tessellating", 0.2 + 0.7 * min(i / n_roots["total"], 1.0))

    # Per-solid tessellation is the slow phase (minutes on a big assembly). Report
    # progress against the total solid count so the worker's bar advances; the
    # tessellation loop is mapped onto 0.2..0.9 of the job.
    if on_progress is not None:
        on_progress("tessellating", 0.2)

    # One-time serial setup: scan the offset index + build the colour/transform/name maps
    # (fires on_total). The per-solid parse+build is then PARALLELISED — each worker gets
    # this picklable index ONCE at spawn and builds solids by id, so only a (seq, rid) int
    # pair crosses the process boundary (no 273 KB ada.geom pickle per solid, and the
    # ~31 ms/solid parse now overlaps across workers instead of running serially here).
    idx = prepare_stream_index(source.path, tolerant=source.tolerant, on_total=_on_total)
    n_workers = _stream_workers()
    use_pool = n_workers > 1 and n_roots["total"] >= _POOL_MIN_SOLIDS

    if not use_pool:
        pool, resolver = idx.open_pool()
        skipped: collections.Counter = collections.Counter()
        try:
            for _seq, _rid in enumerate(idx.roots):
                geom = build_one_solid(idx, pool, resolver, _rid, _seq, skipped=skipped)
                if geom is None:
                    continue
                _handle(_tessellate_geom_worker(geom))
                if (_seq + 1) % _TRIM_EVERY == 0:  # in-process build+tess — trim here too
                    _maybe_trim()
        finally:
            pool.close()
            idx.close()
    else:
        # Self-managed spawn pool (not ProcessPoolExecutor, which can't kill an
        # individual worker): one solid per worker at a time, so a worker that overruns
        # the per-solid timeout — an OCC tessellation hung in an uninterruptible C call —
        # is killed, its solid skipped, and a fresh worker spawned in its place. Without
        # this a single bad solid hangs the whole conversion forever.
        import multiprocessing as _mp
        import queue as _queue
        import time as _time

        ctx = _mp.get_context("spawn")
        timeout_s = _per_solid_timeout_s()
        soft_mb, hard_mb = _worker_mem_caps()

        def _spawn(wid, result_q):
            task_q = ctx.Queue(maxsize=1)
            # The StreamIndex (maps + spilled-index paths) is pickled to the worker ONCE
            # here at spawn; per-solid dispatch then ships only a (seq, rid) int pair.
            proc = ctx.Process(target=_pool_worker_loop, args=(wid, task_q, result_q, idx), daemon=True)
            proc.start()
            return {
                "proc": proc,
                "task_q": task_q,
                "busy": False,
                "gid": None,
                "since": None,
                "task": None,
                "mem_retried": False,
            }

        try:
            result_q = ctx.Queue()
            slots = [_spawn(i, result_q) for i in range(n_workers)]
        except Exception:  # noqa: BLE001 - pool start failure -> sequential fallback
            slots = None

        if slots is None:
            pool, resolver = idx.open_pool()
            skipped = collections.Counter()
            try:
                for _seq, _rid in enumerate(idx.roots):
                    geom = build_one_solid(idx, pool, resolver, _rid, _seq, skipped=skipped)
                    if geom is None:
                        continue
                    _handle(_tessellate_geom_worker(geom))
                    if (_seq + 1) % _TRIM_EVERY == 0:
                        _maybe_trim()
            finally:
                pool.close()
                idx.close()
        else:
            logger.info(
                "scene_from_step_stream: tessellating with %d worker process(es), %.0fs/solid timeout",
                n_workers,
                timeout_s,
            )
            # Dispatch order. Default: index order (arbitrary). With ADA_STEP_STREAM_LPT=1,
            # longest-processing-time-first — sort solids heaviest (most shell faces) first
            # so a few very slow solids (e.g. dense engine blocks, ~70 s each on the crane)
            # overlap the bulk instead of being grabbed last while other workers idle. The
            # weight is a cheap shell-face-count (~2 preads/solid, no full build); the
            # original ``seq`` is preserved for stable solid_N naming regardless of order.
            import os as _os

            _ordered = list(enumerate(idx.roots))
            if _os.environ.get("ADA_STEP_STREAM_LPT"):
                from ada.cadit.step.read.stream_reader import root_face_count

                _wpool, _ = idx.open_pool()
                try:
                    _ordered.sort(key=lambda _sr: root_face_count(_wpool, _sr[1]), reverse=True)
                finally:
                    _wpool.close()
                logger.info("scene_from_step_stream: LPT scheduling on — heaviest solids dispatched first")
            roots_iter = iter(_ordered)
            exhausted = False
            busy = 0
            # Roots requeued by the hard memory cap / crash retry: (seq, rid, is_retry).
            requeue: list = []
            # Parent-loop profiling (ADA_STEP_STREAM_PROFILE=1): split the loop's wall time
            # into result_q.get (idle-wait + IPC/unpickle) vs _handle (transform + per-
            # material spill write = the serial funnel), + the result-queue backlog. avg
            # backlog > ~1 ⇒ results pile up ⇒ the parent can't keep up (A/B would help);
            # backlog ~0 + many idle timeouts ⇒ parent is starved (tail/prep-bound).
            _prof_on = bool(_os.environ.get("ADA_STEP_STREAM_PROFILE"))
            _prof = {"get_s": 0.0, "handle_s": 0.0, "empty": 0, "results": 0, "qmax": 0, "backlog_sum": 0}
            _t_loop0 = _time.monotonic()
            try:
                while True:
                    for i, slot in enumerate(slots):  # feed every idle worker
                        if slot["busy"] or (exhausted and not requeue):
                            continue
                        if requeue:
                            _seq, _rid, is_retry = requeue.pop()
                        else:
                            try:
                                _seq, _rid = next(roots_iter)
                            except StopIteration:
                                exhausted = True
                                break
                            is_retry = False
                        # Replace a worker that died while idle (crash between
                        # dispatches) before handing it work.
                        if not slot["proc"].is_alive():
                            slots[i] = slot = _spawn(i, result_q)
                        slot["busy"] = True
                        # The product name (for logs / skip ids) without building the geom.
                        slot["gid"] = idx.prod_names.get(_rid)
                        slot["since"] = _time.monotonic()
                        slot["task"] = (_seq, _rid)
                        slot["mem_retried"] = is_retry
                        busy += 1
                        slot["task_q"].put((_seq, _rid))
                    if exhausted and busy == 0 and not requeue:
                        break
                    # Collect one result; the 1 s poll bounds how often we re-check timeouts.
                    _t_get = _time.monotonic()
                    try:
                        wid, result = result_q.get(timeout=1.0)
                        if _prof_on:
                            _prof["get_s"] += _time.monotonic() - _t_get
                            _prof["results"] += 1
                            try:
                                _q = result_q.qsize()
                            except (NotImplementedError, OSError):
                                _q = 0
                            _prof["qmax"] = max(_prof["qmax"], _q)
                            _prof["backlog_sum"] += _q
                        slot = slots[wid]
                        if slot["busy"]:
                            slot["busy"] = False
                            slot["gid"] = None
                            slot["since"] = None
                            slot["task"] = None
                            slot["mem_retried"] = False
                            busy -= 1
                            # "__drop__" = the reader couldn't build this root; it never
                            # reaches _handle, exactly as the serial reader drops it (so the
                            # meshed/total/skipped accounting is identical to serial).
                            if result[0] != "__drop__":
                                if _prof_on:
                                    _t_h = _time.monotonic()
                                    _handle(result)
                                    _prof["handle_s"] += _time.monotonic() - _t_h
                                else:
                                    _handle(result)
                            # Soft memory cap: the worker just went idle (its result is
                            # delivered, nothing in flight), so recycling it here is
                            # race-free and loses nothing. Bounds the native-heap
                            # fragmentation that per-solid trims can't fully return;
                            # pending roots simply go to the fresh worker.
                            if soft_mb and _rss_mb(slot["proc"].pid) > soft_mb:
                                slot["proc"].kill()
                                slot["proc"].join(timeout=2)
                                slots[wid] = _spawn(wid, result_q)
                                pool_events["worker_recycles"] += 1
                    except _queue.Empty:
                        if _prof_on:
                            _prof["get_s"] += _time.monotonic() - _t_get
                            _prof["empty"] += 1
                    now = _time.monotonic()
                    for i, slot in enumerate(slots):  # replace dead or over-budget workers
                        if not slot["busy"]:
                            continue
                        # A worker that died mid-solid (OCC segfault/terminate — uncatchable
                        # in-process) will never produce a result; without this liveness
                        # check its slot would sit blocked for the full per-solid timeout,
                        # and a model with many such solids burns hours of wall clock.
                        if not slot["proc"].is_alive():
                            gid = slot["gid"]
                            task_inflight = slot["task"]
                            was_retry = slot["mem_retried"]
                            slot["proc"].join(timeout=2)
                            busy -= 1
                            slots[i] = _spawn(i, result_q)
                            # One retry on a fresh worker: covers both the soft-cap
                            # recycle race (worker exited between delivering its result
                            # and the parent's next dispatch — the root is perfectly
                            # healthy) and one-off native crashes. A second death on the
                            # same root is the root's own doing -> skip it.
                            if task_inflight is not None and not was_retry:
                                requeue.append((task_inflight[0], task_inflight[1], True))
                            else:
                                _handle(
                                    (
                                        "error:WorkerCrashed (native crash in OCC)",
                                        gid,
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                    )
                                )
                            continue
                        # Hard memory cap: a worker ballooning MID-solid is killed
                        # before it can blow a memory-tight pod's per-job budget. The
                        # in-flight root is retried ONCE on a fresh worker (heap-
                        # fragmentation overruns succeed there); a second strike means
                        # the solid itself needs that memory -> skip it, like a timeout.
                        if hard_mb and _rss_mb(slot["proc"].pid) > hard_mb:
                            gid = slot["gid"]
                            task_inflight = slot["task"]
                            was_retry = slot["mem_retried"]
                            slot["proc"].kill()
                            slot["proc"].join(timeout=2)
                            busy -= 1
                            slots[i] = _spawn(i, result_q)
                            pool_events["mem_kills"] += 1
                            if was_retry or task_inflight is None:
                                _handle(
                                    (
                                        f"memory (>{hard_mb:.0f}MB; worker killed twice)",
                                        gid,
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                        None,
                                    )
                                )
                            else:
                                logger.info(
                                    "scene_from_step_stream: worker exceeded %.0fMB on %s — requeueing once",
                                    hard_mb,
                                    gid,
                                )
                                requeue.append((task_inflight[0], task_inflight[1], True))
                            continue
                        if slot["since"] and (now - slot["since"]) > timeout_s:
                            gid = slot["gid"]
                            slot["proc"].kill()
                            slot["proc"].join(timeout=2)
                            busy -= 1
                            slots[i] = _spawn(i, result_q)
                            _handle(
                                (
                                    f"timeout (>{timeout_s:.0f}s; OCC hang, killed)",
                                    gid,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                )
                            )
            finally:
                if _prof_on:
                    _wall = _time.monotonic() - _t_loop0
                    _avg_bl = _prof["backlog_sum"] / max(_prof["results"], 1)
                    logger.warning(
                        "[POOLPROF] loop_wall=%.0fs  result_q.get(wait+unpickle)=%.0fs  "
                        "_handle(xform+spill)=%.0fs  idle_timeouts=%d(~%ds idle)  results=%d  "
                        "avg_backlog=%.2f  qmax=%d  →  %s",
                        _wall,
                        _prof["get_s"],
                        _prof["handle_s"],
                        _prof["empty"],
                        _prof["empty"],
                        _prof["results"],
                        _avg_bl,
                        _prof["qmax"],
                        "PARENT-bound (results pile up)" if _avg_bl > 1.0 else "WORKER/tail-bound (parent starved)",
                    )
                for slot in slots:
                    try:
                        slot["task_q"].put_nowait(None)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        slot["proc"].kill()
                    except Exception:  # noqa: BLE001
                        pass
                idx.close()

    n_skipped = sum(reasons.values())
    if n_skipped:
        more = f" (+{n_skipped - len(skipped_ids)} more)" if n_skipped > len(skipped_ids) else ""
        logger.warning(
            "scene_from_step_stream: %s — skipped %d/%d solids by reason %s; ids: %s%s",
            source.path,
            n_skipped,
            n_total,
            dict(reasons),
            ", ".join(skipped_ids),
            more,
        )
    if rebuild_totals:
        logger.info("scene_from_step_stream: %s — face repairs %s", source.path, dict(rebuild_totals))
    logger.info(
        "scene_from_step_stream: %s — meshed %d/%d solids into %d material group(s)",
        source.path,
        n_total - n_skipped,
        n_total,
        len(bt.material_store),
    )
    f_total = rebuild_totals.get("faces_total", 0)
    f_built = rebuild_totals.get("faces_built", 0)
    face_coverage = {
        "total": f_total,
        "built": f_built,
        "dropped": rebuild_totals.get("faces_dropped", 0),
        "pct": round(100.0 * f_built / f_total, 2) if f_total else 100.0,
    }
    return {
        "meshed": n_total - n_skipped,
        "total": n_total,
        "skipped": n_skipped,
        "materials": len(bt.material_store),
        "reasons": dict(reasons),
        "rebuilds": dict(rebuild_totals),
        "face_coverage": face_coverage,
        "pool": dict(pool_events),
    }


def scene_from_step_stream(source: StepStreamSource, converter: SceneConverter) -> trimesh.Scene:
    """Build a merged-by-colour ``trimesh.Scene`` by streaming a STEP file solid-by-solid
    (used by ``SceneConverter.build_scene`` / interactive rendering). For the worker's
    GLB conversion use :func:`convert_step_stream_to_glb`, which spills the merge to disk
    and never materialises the whole scene."""
    import collections

    import trimesh

    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.meshes import MeshStore, MeshType
    from ada.visit.gltf.optimize import concatenate_stores
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    bt = BatchTessellator()  # parent-side material store + material lookup for the merge
    params = converter.params
    graph = converter.graph
    root = graph.top_level
    scene = trimesh.Scene(base_frame=root.name)

    # mat_id -> [MeshStore]. Mesh buffers are flat float/int arrays (a few tens of MB
    # for a 100k-triangle model), so accumulating them per material — then merging once
    # at the end — keeps memory bounded; the B-rep geometry is freed each iteration.
    by_material: dict[int, list] = collections.defaultdict(list)

    def _sink(mat_id, node_ref, pos, idx, nrm) -> None:
        by_material[mat_id].append(MeshStore(node_ref, None, pos, idx, nrm, mat_id, MeshType.TRIANGLES, node_ref))

    stats = _tessellate_stream(source, graph, bt, _sink)

    # One merged mesh (glTF node) per material/colour — the default GLB shape.
    if source.on_progress is not None:
        source.on_progress("merging", 0.92)
    coalesce = _merge_siblings_enabled(source)
    for mat_id, stores in by_material.items():
        merged = concatenate_stores(stores, graph)
        if merged is None:
            continue
        if coalesce:
            # Reorder this colour's index buffer so a merged node's siblings form one
            # contiguous draw-range (positions untouched -> identical triangles).
            from ada.visit.gltf.optimize import coalesce_groups_by_node

            merged.indices, merged.groups = coalesce_groups_by_node(merged.indices, merged.groups)
        merged_mesh_to_trimesh_scene(
            scene, merged, bt.get_mat_by_id(mat_id), mat_id, graph, apply_transform=params.apply_transform
        )

    scene.metadata["ada_stream_stats"] = stats
    return scene


def convert_step_stream_to_glb(source: StepStreamSource, glb_path: str | Path) -> dict:
    """Stream-convert a STEP file straight to a GLB on disk with bounded memory.

    Replaces the trimesh-scene merge + ``scene.export`` of the default path: each solid's
    mesh is spilled to a per-material temp file as it streams in (the incremental
    concatenate), then the GLB is assembled by streaming those files into the BIN chunk —
    so peak RAM is ~one solid's buffers + a light manifest, not the whole model ×2-3.

    Produces the same merge-by-colour materials + ``ADA_EXT_data`` extension + picking
    metadata (``scenes[0].extras``) as :func:`scene_from_step_stream` + trimesh export.
    Returns ``{"meshed", "total", "skipped", "materials", "reasons"}``."""
    import numpy as np

    from ada.cadit.step.glb_spill import GlbSpillStore, write_glb_from_spill
    from ada.core.guid import create_guid
    from ada.extension.design_and_analysis_extension_schema import (
        AdaDesignAndAnalysisExtension,
    )
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.graph import GraphNode, GraphStore
    from ada.visit.gltf.meshes import MergedMesh, MeshType

    bt = BatchTessellator()
    root = GraphNode("root", 0, hash=create_guid())
    graph = GraphStore(root, {0: root})
    ada_ext = AdaDesignAndAnalysisExtension()
    spill = GlbSpillStore()
    try:
        stats = _tessellate_stream(source, graph, bt, spill.add)

        if source.on_progress is not None:
            source.on_progress("merging", 0.92)

        # Merge same-name siblings: reorder each material's index buffer so a merged
        # node's ranges are contiguous (one pickable draw-range per node). Must run
        # BEFORE the groups are read into the picking metadata + the GLB is written.
        if _merge_siblings_enabled(source):
            spill.coalesce_by_node()

        # Register each material's picking ranges so ``to_json_hierarchy`` emits the
        # ``draw_ranges_node{mat_id}`` sequences. ``create_id_sequence`` only reads
        # ``.groups``, so a groups-only MergedMesh (empty buffers) is enough — the heavy
        # vertex/index data already lives in the spill files.
        empty_pos = np.empty(0, dtype=np.float32)
        empty_idx = np.empty(0, dtype=np.uint32)
        color_by_mat: dict[int, object] = {}
        for m in spill.materials():
            color = bt.get_mat_by_id(m.mat_id)
            color_by_mat[m.mat_id] = color
            if m.index_count > 0:
                graph.add_merged_mesh(
                    m.mat_id, MergedMesh(empty_idx, empty_pos, None, color, MeshType.TRIANGLES, m.groups)
                )

        scene_metadata = dict(graph.to_json_hierarchy())
        scene_metadata["ada_stream_stats"] = stats

        write_glb_from_spill(
            glb_path,
            spill,
            color_by_mat,
            ada_ext.model_dump(mode="json"),
            scene_metadata,
            base_frame=root.name,
        )
        return stats
    finally:
        spill.cleanup()

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


def _tessellate_geom_worker(geom):
    """Pool subprocess entry: build + tessellate ONE solid and return raw mesh arrays
    plus the solid's colour — the parent assigns a consistent material id (each
    subprocess has its own material store). Returns
    ``(status, gid, color, positions, indices, normals, transforms)`` where ``status``
    is "ok", "degenerate", "empty" or "error:<Type>"; ``transforms`` is the solid's list
    of world-placement matrices (or None) — the parent meshes once and places per matrix.
    OCC isn't thread-safe, so the unit of parallelism is a process."""
    import numpy as np

    gid = None
    occ = None
    mesh = None
    try:
        import os

        _hang = os.environ.get("ADA_STEP_STREAM_TEST_HANG_S")  # test hook: simulate an OCC hang
        if _hang:
            import time

            time.sleep(float(_hang))

        from ada.cad import active_backend

        be = active_backend()
        gid = str(geom.id) if geom.id not in (None, "") else None
        occ = be.build(geom)
        # Zero-extent solid -> OCC's relative mesher throws an uncatchable terminate.
        try:
            bb = be.bbox(occ)
            diag = ((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2) ** 0.5
        except Exception:
            diag = 0.0
        if diag < 1e-7:
            return ("degenerate", gid, geom.color, None, None, None, None)
        mesh = be.tessellate(occ)
        idx = getattr(mesh, "indices", None)
        if idx is None:
            idx = getattr(mesh, "faces", None)
        pos = getattr(mesh, "positions", None)
        if pos is None or idx is None or len(idx) == 0:
            return ("empty", gid, geom.color, None, None, None, None)
        nrm = getattr(mesh, "normals", None)
        # ascontiguousarray(+dtype) COPIES, so pos/idx/nrm no longer reference any OCC
        # buffer — the shape + its triangulation can be dropped immediately below.
        pos = np.ascontiguousarray(pos, dtype=np.float32)
        idx = np.ascontiguousarray(idx, dtype=np.uint32)
        nrm = np.ascontiguousarray(nrm, dtype=np.float32) if nrm is not None else None
        # The shell is tessellated ONCE in its local frame. The world-placement matrices
        # (one per STEP assembly instance) ride back to the parent, which applies each to
        # this single local mesh — so a part instanced N times still meshes once.
        return ("ok", gid, geom.color, pos, idx, nrm, geom.transforms)
    except Exception as exc:  # noqa: BLE001 - report and skip; one bad solid mustn't abort
        return (f"error:{type(exc).__name__}", gid, None, None, None, None, None)
    finally:
        # Purge this task's OCC instances now (refcount-0 frees the native shape +
        # triangulation) rather than relying on end-of-call scope cleanup — keeps a
        # long-lived worker from carrying a solid's geometry into the next iteration.
        del occ, mesh


def _pool_worker_loop(worker_id, task_q, result_q) -> None:
    """Long-lived pool worker: pull one geom at a time, tessellate it, and put
    ``(worker_id, result)`` back. The worker_id lets the parent free the right slot and
    — crucially — terminate THIS worker if it overruns the per-solid timeout (an OCC
    tessellation can hang in an uninterruptible C call; only killing the process stops
    it). ``None`` is the shutdown sentinel."""
    n = 0
    while True:
        geom = task_q.get()
        if geom is None:
            return
        result_q.put((worker_id, _tessellate_geom_worker(geom)))
        n += 1
        if n % _TRIM_EVERY == 0:  # return the per-solid OCC heap to the OS periodically
            _maybe_trim()


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
    MINUS one, capped at 8.

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
    return max(1, min(n - 1, 8))


def _tessellate_stream(source: StepStreamSource, graph, bt, sink) -> dict:
    """Shared streaming tessellation loop used by both the trimesh-scene path
    (:func:`scene_from_step_stream`) and the disk-spilled GLB path
    (:func:`convert_step_stream_to_glb`).

    Reads the STEP one solid at a time (pooled or sequential), applies each assembly
    instance's world placement, creates a graph node per instance, resolves its material
    id, and hands ``(mat_id, node_ref, pos, idx, nrm)`` to ``sink``. Returns the stats
    dict ``{"meshed", "total", "skipped", "materials", "reasons"}``."""
    import collections
    import itertools

    import numpy as np

    from ada.cadit.step.read.stream_reader import stream_read_step
    from ada.config import logger
    from ada.core.guid import create_guid
    from ada.occ.geom.cache import clear_all
    from ada.visit.gltf.graph import GraphNode

    # No sibling code path should have left OCC shapes pinned in the process-global
    # caches for this conversion (the streaming build path never populates them, but be
    # explicit so a prior in-process conversion can't leak into this one).
    clear_all()

    root = graph.top_level
    on_progress = source.on_progress

    reasons: collections.Counter = collections.Counter()
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

    # The parent owns the material store (so colours map to consistent ids across worker
    # processes), creates the graph node from each worker's raw mesh arrays, and hands
    # the result to the caller's sink. Workers (subprocesses) only build + tessellate.
    def _build(gid, color, pos, idx, nrm, transform=None) -> None:
        # Apply this instance's world placement to the local mesh (rigid: rotation +
        # translation on positions, rotation on normals). pos/nrm stay FLAT (N*3,) — the
        # MeshStore/spill path needs flat buffers — so reshape only for the matmul.
        if transform is not None:
            t = np.asarray(transform, dtype=np.float32)
            r = t[:3, :3]
            pos = np.ascontiguousarray((pos.reshape(-1, 3) @ r.T + t[:3, 3]).ravel(), dtype=np.float32)
            if nrm is not None:
                nrm = np.ascontiguousarray((nrm.reshape(-1, 3) @ r.T).ravel(), dtype=np.float32)
        node = graph.add_node(GraphNode(gid, graph.next_node_id(), hash=create_guid(), parent=root))
        mat_id = bt.material_store.get(color, None)
        if mat_id is None:
            mat_id = len(bt.material_store)
            bt.material_store[color] = mat_id
        sink(mat_id, node.hash, pos, idx, nrm)

    def _handle(result) -> None:
        nonlocal n_total
        status, gid, color, pos, idx, nrm, transforms = result
        i = n_total
        n_total += 1
        gid = gid or f"solid_{i}"
        if status == "ok":
            # One mesh, N instances: tessellated once, placed per assembly matrix.
            for k, tf in enumerate(transforms if transforms else [None]):
                inst_gid = gid if k == 0 else f"{gid}/{k + 1}"
                _build(inst_gid, color, pos, idx, nrm, tf)
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

    geom_iter = stream_read_step(source.path, local_pool=False, tolerant=source.tolerant, on_total=_on_total)

    # Peek the first solid so the reader's scan runs and fires on_total with the solid
    # count — we only spin up the worker pool when there's enough work to amortise the
    # ~1 s process-spawn overhead (it makes small conversions SLOWER otherwise).
    try:
        _first = next(geom_iter)
        geom_iter = itertools.chain((_first,), geom_iter)
    except StopIteration:
        geom_iter = iter(())
    n_workers = _stream_workers()
    use_pool = n_workers > 1 and n_roots["total"] >= _POOL_MIN_SOLIDS

    if not use_pool:
        _seq = 0
        for geom in geom_iter:
            _handle(_tessellate_geom_worker(geom))
            _seq += 1
            if _seq % _TRIM_EVERY == 0:  # sequential path tessellates in-process — trim here too
                _maybe_trim()
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

        def _spawn(wid, result_q):
            task_q = ctx.Queue(maxsize=1)
            proc = ctx.Process(target=_pool_worker_loop, args=(wid, task_q, result_q), daemon=True)
            proc.start()
            return {"proc": proc, "task_q": task_q, "busy": False, "gid": None, "since": None}

        try:
            result_q = ctx.Queue()
            slots = [_spawn(i, result_q) for i in range(n_workers)]
        except Exception:  # noqa: BLE001 - pool start failure -> sequential fallback
            slots = None

        if slots is None:
            _seq = 0
            for geom in geom_iter:
                _handle(_tessellate_geom_worker(geom))
                _seq += 1
                if _seq % _TRIM_EVERY == 0:
                    _maybe_trim()
        else:
            logger.info(
                "scene_from_step_stream: tessellating with %d worker process(es), %.0fs/solid timeout",
                n_workers,
                timeout_s,
            )
            exhausted = False
            busy = 0
            try:
                while True:
                    for slot in slots:  # feed every idle worker
                        if slot["busy"] or exhausted:
                            continue
                        try:
                            geom = next(geom_iter)
                        except StopIteration:
                            exhausted = True
                            break
                        slot["busy"] = True
                        slot["gid"] = str(geom.id) if geom.id not in (None, "") else None
                        slot["since"] = _time.monotonic()
                        busy += 1
                        slot["task_q"].put(geom)
                    if exhausted and busy == 0:
                        break
                    # Collect one result; the 1 s poll bounds how often we re-check timeouts.
                    try:
                        wid, result = result_q.get(timeout=1.0)
                        slot = slots[wid]
                        if slot["busy"]:
                            slot["busy"] = False
                            slot["gid"] = None
                            slot["since"] = None
                            busy -= 1
                            _handle(result)
                    except _queue.Empty:
                        pass
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
                            slot["proc"].join(timeout=2)
                            busy -= 1
                            slots[i] = _spawn(i, result_q)
                            _handle(("error:WorkerCrashed (native crash in OCC)", gid, None, None, None, None, None))
                            continue
                        if slot["since"] and (now - slot["since"]) > timeout_s:
                            gid = slot["gid"]
                            slot["proc"].kill()
                            slot["proc"].join(timeout=2)
                            busy -= 1
                            slots[i] = _spawn(i, result_q)
                            _handle(
                                (f"timeout (>{timeout_s:.0f}s; OCC hang, killed)", gid, None, None, None, None, None)
                            )
            finally:
                for slot in slots:
                    try:
                        slot["task_q"].put_nowait(None)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        slot["proc"].kill()
                    except Exception:  # noqa: BLE001
                        pass

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
    logger.info(
        "scene_from_step_stream: %s — meshed %d/%d solids into %d material group(s)",
        source.path,
        n_total - n_skipped,
        n_total,
        len(bt.material_store),
    )
    return {
        "meshed": n_total - n_skipped,
        "total": n_total,
        "skipped": n_skipped,
        "materials": len(bt.material_store),
        "reasons": dict(reasons),
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
    for mat_id, stores in by_material.items():
        merged = concatenate_stores(stores, graph)
        if merged is None:
            continue
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

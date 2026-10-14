"""``clash_contacts`` -- convex-contact clash runs on a viewer GLB, and detailing from them.

ONE KIND, THREE MODES (``conversion_options["mode"]``):

* ``check``: measure the GLB at ``glb_key`` (``ada.clash.contacts.check_glb``), resolve which specs
  could detail each clash against the target's rebuilt members, and write the run to its sidecar key
  (``job.derived_key``) and its target's index. The GLB is the one the viewer loads, so a browser that
  ran the same check on the same bytes would have written the same clashes.
* ``annotate``: the specs half alone, for a run a BROWSER posted -- it measured the GLB itself but has
  no spec registry to ask. The run is rewritten in place with its ``applicable`` specs.
* ``detail``: build the named clashes with a registered spec. The clashes come FROM THE RUN -- contact
  points, normal, distance, patch polygons -- and are not re-measured; the members a builder cuts are
  rebuilt from the run's target and bound by name (``ada.clash.contacts.founds_by_id``). A GLB that has
  changed since the run (its sha256 no longer matches) is refused: the stored clashes would describe
  geometry that is no longer there.

SYNTHETIC, like ``clash_check_asset``: what is read (the GLB, the run, the source a target names) is
fetched through the storage facade, not pre-downloaded by extension. And never short-circuited on an
existing derived key: an ``annotate`` job's derived key IS the run it rewrites.

The work itself is :func:`contacts_work`, a plain function the queue-less viewer calls too
(``local_jobs.start_clash_contacts``), so the two engines cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tempfile
import traceback as tb_module
from typing import Callable

from ada.config import logger

from ..clash_runs import write_run_and_index
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..worker.audit import _audit_done
from ..worker.settings import read_clash_timeout_s
from .clash_isolation import run_clash_work
from .registry import JobContext, SyntheticFormatHandler

CLASH_CONTACTS_KIND = "clash_contacts"
CONTACT_MODES = ("check", "annotate", "detail")

__all__ = ["CLASH_CONTACTS_KIND", "CONTACT_MODES", "ClashContactsHandler", "contacts_work", "load_target_model"]


class ContactsJobError(ValueError):
    """A request the job cannot answer; its message is the whole story."""


def load_target_model(target: dict, storage):
    """The model a run's target names, rebuilt with the members a builder needs.

    A file is read with the full reader (``ada_load._load_with_ada``): its members carry the names the
    viewer GLB was written with (the native IFC -> GLB stream names products as ``ada.from_ifc`` does),
    which is what a run's member names are bound against. A published node is read by its provider.
    """
    kind = target.get("kind")
    if kind == "file":
        from ..converters.ada_load import _load_with_ada

        source_key = str(target.get("source_key") or "")
        if not source_key:
            raise ContactsJobError("a file target needs its source_key")
        suffix = pathlib.PurePosixPath(source_key).suffix.lower()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / f"source{suffix}"
            storage.fetch_to_path(source_key, path)
            return _load_with_ada(path, suffix)
    if kind == "node":
        from ada.clash.from_asset import part_for_asset_node

        return part_for_asset_node(
            collection=str(target["collection"]),
            subject=str(target["subject"]),
            storage=storage,
            revision=target.get("revision") or None,
            node=target.get("node") or None,
        )
    raise ContactsJobError(f"a clash run over a {kind!r} target cannot be rebuilt into members")


def _annotate(run, storage, capability_of, progress):
    from ada.clash.contacts import annotate_applicable

    progress("members", 0.5)
    try:
        model = load_target_model(dict(run.target), storage)
    except Exception as exc:  # noqa: BLE001 - a run without specs is still a run
        logger.warning(f"clash contacts: specs not resolved, the target did not rebuild: {exc}")
        provenance = dict(run.provenance)
        provenance["applicability"] = {"error": f"{type(exc).__name__}: {exc}"}
        from dataclasses import replace

        return replace(run, provenance=provenance)
    annotated, unbound = annotate_applicable(run, model, capability_of)
    if unbound:
        logger.info(f"clash contacts: {unbound} clash(es) of run {run.run_id} did not bind to rebuilt members")
    return annotated


def contacts_work(opts: dict, storage, progress: Callable[[str, float], None], capability_of=None) -> dict[str, bytes]:
    """One ``clash_contacts`` job's work. Returns its outputs by name: ``run`` (the run document) for
    ``check`` / ``annotate``; ``glb`` and ``stats`` for ``detail``."""
    from ada.clash.contacts import check_glb, founds_by_id
    from ada.clash.sidecar import parse_run

    mode = opts.get("mode")
    if mode == "check":
        glb_key = str(opts.get("glb_key") or "")
        if not glb_key:
            raise ContactsJobError("a check needs the glb_key of the GLB it measures")
        progress("geometry", 0.1)
        glb = storage.get_bytes(glb_key)
        progress("clash", 0.3)
        run = check_glb(
            glb,
            target=dict(opts.get("target") or {}),
            glb_key=glb_key,
            up_axis=str(opts.get("up_axis") or "z"),
            options=dict(opts.get("options") or {}),
            run_id=opts.get("run_id"),
        )
        run = _annotate(run, storage, capability_of, progress)
        return {"run": run.to_json().encode("utf-8")}

    run_key = str(opts.get("run_key") or "")
    if not run_key:
        raise ContactsJobError(f"{mode!r} needs the run_key of a clash run")
    run = parse_run(storage.get_bytes(run_key))

    if mode == "annotate":
        return {"run": _annotate(run, storage, capability_of, progress).to_json().encode("utf-8")}

    if mode != "detail":
        raise ContactsJobError(f"unknown clash_contacts mode {mode!r} (one of {', '.join(CONTACT_MODES)})")

    from ada.api.connections.spec import get_registered
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.detail import build_detail

    ids = [str(i) for i in opts.get("contact_ids") or ()]
    spec_name = str(opts.get("spec") or "")
    if not ids or not spec_name:
        raise ContactsJobError("a detail needs contact_ids and a spec")
    register_builtin_specs()
    try:
        registered = get_registered(spec_name)
    except KeyError as exc:
        raise ContactsJobError(
            f"spec {spec_name!r} is not registered in this process -- it must be importable on the pool "
            "a detail for it is routed to"
        ) from exc

    progress("geometry", 0.1)
    glb_key = str(run.geometry.get("key") or "")
    if glb_key:
        now = hashlib.sha256(storage.get_bytes(glb_key)).hexdigest()
        if now != run.geometry.get("sha256"):
            raise ContactsJobError(
                f"the geometry of this run has changed since it was checked ({glb_key}): run the clash "
                "check again before detailing from it"
            )
    progress("members", 0.3)
    model = load_target_model(dict(run.target), storage)
    by_id, problems = founds_by_id(run, model, ids)
    if not by_id:
        raise ContactsJobError(
            "none of the clashes bound to the rebuilt members: "
            + "; ".join(f"{cid}: {why}" for cid, why in problems.items())
        )
    progress("detail", 0.55)
    glb_bytes, stats = build_detail(
        registered,
        spec_name=spec_name,
        joint_ids=[cid for cid in ids if cid in by_id],
        by_id=by_id,
        gen_options=dict(opts.get("options") or {}),
    )
    if problems:
        stats.setdefault("skipped", []).extend(f"{cid}: {why}" for cid, why in problems.items())
    stats["run_id"] = run.run_id
    return {"glb": glb_bytes, "stats": json.dumps(stats).encode("utf-8")}


class ClashContactsHandler(SyntheticFormatHandler):
    kind = CLASH_CONTACTS_KIND
    #: An annotate job's derived key is the run it rewrites, which exists before it runs.
    skip_cached_short_circuit = True

    async def run(self, job: Job, ctx: JobContext) -> None:
        from .clash_check import _capability_of_factory

        opts = dict(job.conversion_options or {})
        mode = opts.get("mode")

        async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
            await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at, traceback=trace)

        if mode not in CONTACT_MODES:
            await _fail("clash", f"clash_contacts needs a mode, one of {', '.join(CONTACT_MODES)} (got {mode!r})")
            return
        capability_of = await _capability_of_factory(ctx.queue) if mode != "detail" else None

        def _work(out_dir: pathlib.Path, storage, progress) -> None:
            for name, data in contacts_work(opts, storage, progress, capability_of).items():
                (out_dir / name).write_bytes(data)

        out_dir = await run_clash_work(
            _work,
            job=job,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            stage="clash",
            storage=ctx.storage,
            scope=ctx.scope,
            timeout_s=await read_clash_timeout_s(ctx.db_pool),
            label=f"clash {mode}",
        )
        if out_dir is None:
            return  # cancelled / failed -- already reported

        try:
            await ctx.queue.update(job.job_id, stage="upload", progress=0.9)
            if mode == "detail":
                if opts.get("glb_key"):
                    await ctx.storage.put_path(ctx.scope, opts["glb_key"], out_dir / "glb", content_encoding="gzip")
                await ctx.storage.put_bytes(
                    ctx.scope, job.derived_key, (out_dir / "stats").read_bytes(), content_encoding="gzip"
                )
            else:
                await write_run_and_index(ctx.storage, ctx.scope, job.derived_key, (out_dir / "run").read_bytes())
        except Exception as exc:
            logger.exception("worker: clash_contacts upload failed for job %s", job.job_id)
            await _fail("upload", str(exc), tb_module.format_exc())
            return
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        await ctx.queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)

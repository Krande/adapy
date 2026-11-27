"""``clash_contacts`` -- convex-contact clash runs on a viewer GLB, and detailing from them.

ONE KIND, THREE MODES (``conversion_options["mode"]``):

* ``check``: measure the GLB at ``glb_key`` (``ada.clash.contacts.check_glb``), resolve which specs
  could detail each clash against the target's rebuilt members, and write the run to its sidecar key
  (``job.derived_key``) and its target's index. The GLB is the one the viewer loads, so a browser that
  ran the same check on the same bytes would have written the same clashes.
* ``annotate``: the specs half alone, for a run a BROWSER posted -- it measured the GLB itself but has
  no spec registry to ask. The run is rewritten in place with its ``applicable`` specs and each
  clash's ``roles`` (incoming / landing; from the members' meshes where the target does not rebuild
  here -- ``ada.clash.contacts.annotate_applicable``). A saved
  GROUP's run rebuilds one model per part (each by its own target's loader) and binds each member in
  its own part's model.
* ``detail``: build the named clashes with a registered spec. The clashes come FROM THE RUN -- contact
  points, normal, distance, patch polygons -- and are not re-measured; the members a builder cuts are
  rebuilt as its annotate rebuilt them -- through the geometry provider the request names, else one
  model per part of a group, else the target's model -- and bound by name
  (``ada.clash.contacts.founds_by_id``). A GLB that has changed since the run (its sha256 no longer
  matches; each part's, for a group) is refused: the stored clashes would describe geometry that is no
  longer there.

SYNTHETIC, like ``clash_check_asset``: what is read (the GLB, the run, the source a target names) is
fetched through the storage facade, not pre-downloaded by extension. And never short-circuited on an
existing derived key: an ``annotate`` job's derived key IS the run it rewrites.

The work itself is :func:`contacts_work`, a plain function the queue-less viewer calls too
(``local_jobs.start_clash_contacts``), so the two engines cannot drift.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import traceback as tb_module

from ada.clash.rebuild import load_target_model
from ada.config import logger

from ..assoc_links import store_source_states, store_subject_links
from ..clash_contacts_work import (  # noqa: F401 - the old names stay importable from here
    CONTACT_MODES,
    MAX_FINGERPRINTED,
    ContactsJobError,
    _annotate,
    _identified,
    _check_geometry_unchanged,
    _no_geometry_detail,
    _rebuild_members,
    _run_outputs,
    _subjects_work,
    contacts_work,
)
from ..clash_runs import write_run_and_index
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..worker.audit import _audit_done
from ..worker.settings import read_clash_timeout_s
from .clash_isolation import run_clash_work
from .registry import JobContext, SyntheticFormatHandler

CLASH_CONTACTS_KIND = "clash_contacts"

__all__ = [
    "CLASH_CONTACTS_KIND",
    "CONTACT_MODES",
    "ClashContactsHandler",
    "contacts_work",
    "load_target_model",
]


async def _store_subjects(ctx, out_dir: pathlib.Path) -> None:
    """File the subjects a job made (its ``subjects`` output), when it made any."""
    path = out_dir / "subjects"
    if path.exists():
        made = json.loads(path.read_bytes())
        await store_subject_links(ctx.storage, ctx.scope, made.get("subjects") or (), pool=ctx.db_pool)


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
        capability_of = await _capability_of_factory(ctx.queue) if mode in ("check", "annotate") else None

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
            if mode == "detail":
                from ada.clash.detail import upload_stage

                if opts.get("glb_key"):
                    stage, frac = upload_stage("GLB", (out_dir / "glb").stat().st_size, index=0, count=2)
                    await ctx.queue.update(job.job_id, stage=stage, progress=frac)
                    await ctx.storage.put_path(ctx.scope, opts["glb_key"], out_dir / "glb", content_encoding="gzip")
                stage, frac = upload_stage("stats", index=1, count=2)
                await ctx.queue.update(job.job_id, stage=stage, progress=frac)
                await ctx.storage.put_bytes(
                    ctx.scope, job.derived_key, (out_dir / "stats").read_bytes(), content_encoding="gzip"
                )
                await _store_subjects(ctx, out_dir)
            elif mode == "subjects":
                await ctx.storage.put_bytes(
                    ctx.scope, job.derived_key, (out_dir / "subjects").read_bytes(), content_encoding="gzip"
                )
                await _store_subjects(ctx, out_dir)
            else:
                await write_run_and_index(
                    ctx.storage, ctx.scope, job.derived_key, (out_dir / "run").read_bytes(), pool=ctx.db_pool
                )
                if (out_dir / "states").exists():
                    await store_source_states(
                        ctx.storage, ctx.scope, json.loads((out_dir / "states").read_bytes()), pool=ctx.db_pool
                    )
        except Exception as exc:
            logger.exception("worker: clash_contacts upload failed for job %s", job.job_id)
            await _fail("upload", str(exc), tb_module.format_exc())
            return
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        await ctx.queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)

"""Cross-format visual-parity validation (``parity``).
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
import traceback as tb_module
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..converter import ConverterRegistry
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..subprocess_convert import IsolatedConvertResult, run_isolated_convert
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


def _parity_child(src_path, source_key, target_format, on_progress, *, produced=None):
    """``convert_fn``-shaped wrapper that runs the cross-format parity check and
    returns its result as JSON bytes.

    ``produced`` maps each compared format (step/ifc/xml/glb) to the local path of
    the blob the audit ALREADY produced+uploaded with the production strategy (or
    None when that conversion failed/was skipped). The check reads those blobs and
    compares a format-agnostic geometry invariant — it re-derives nothing, so it
    validates exactly what ships. It still tessellates step/ifc/xml to measure them;
    running through ``run_isolated_convert`` (this function in the forked child)
    means an OOM there is SIGKILLed by the per-job memory watchdog and fails the
    cell, rather than taking the whole worker pod down.

    Falls back to the offline re-derive path (``parity_for_source_file``) only when
    no produced blobs were passed — never the case on the audit worker."""
    import json as _json
    import pathlib as _pl

    from ada.cadit.visual_parity import (
        parity_for_source_file,
        parity_from_produced_files,
        parity_gxml_from_produced_files,
    )

    on_progress("parity", 0.2)
    if produced:
        pmap = {fmt: (_pl.Path(p) if p else None) for fmt, p in produced.items()}
        if str(source_key).lower().endswith(".xml"):
            # Genie-XML: cheap per-format COUNT comparison over the produced blobs (the
            # historical gxml invariant — catches a leg silently dropping N of M objects,
            # which a bbox gate can miss) — zero re-derivation, zero tessellation.
            res = parity_gxml_from_produced_files(source_key, pmap)
        else:
            res = parity_from_produced_files(source_key, pmap)
    else:
        res = parity_for_source_file(_pl.Path(src_path))
    on_progress("ready", 1.0)
    return _json.dumps(
        {
            "counts": res.counts,
            "expected": res.expected,
            "consistent": res.consistent,
            "mismatches": res.mismatches,
            "errors": res.errors,
            "skipped": res.skipped,
            "summary": res.summary(),
        }
    ).encode("utf-8")


async def _run_parity_validation(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress: Callable[[str, float], Awaitable[None]],
    timeout_s: float | None = None,
) -> None:
    """Cross-format visual-parity validation for one source (target_format=='parity').

    Reads the source's ALREADY-PRODUCED output blobs (step/ifc/xml/glb, converted +
    uploaded earlier in the run with the production strategy) and compares a
    format-agnostic GEOMETRY INVARIANT — surface area + bbox extent (see
    ada.cadit.visual_parity.parity_from_produced_files). It re-derives nothing, so
    it validates exactly what ships and does zero extra conversion; the parity cells
    are only enqueued after every conversion cell for the source has landed, so the
    blobs already exist. A format whose conversion failed/was skipped is recorded
    (its blob is absent), never re-derived. Produces no derived blob: the structured
    per-format result goes to the ``audit_parity`` table and the cell is audited
    done/error (a mismatch maps to ``error`` so it surfaces in the run's failed
    cells). Never raises.

    Runs in the same memory-capped forked child the convert path uses
    (``run_isolated_convert``): tessellating step/ifc/xml to measure them can spike
    RAM, and a blow-up must die in isolation (cell fails as OOM) rather than
    OOM-killing the worker pod.
    """
    import json

    from ada.cadit.visual_parity import PARITY_GEOMETRY_FORMATS

    from ..converter import derived_key_for

    job_id = job.job_id
    suffix = pathlib.PurePosixPath(job.source_key).suffix.lower()

    async def _cancel_check() -> bool:
        if db_pool is None:
            return False
        try:
            return await db_module.audit_is_cancelled(db_pool, job_id)
        except Exception:
            return False

    # FEM sources take the produced-files geometry-invariant path: fetch each already-
    # produced output blob to a worker-local tempfile BEFORE forking (the child can't
    # reach async storage; the fork shares the filesystem, so the child reads these
    # paths). A missing blob (conversion failed/skipped) maps to None — recorded by
    # parity_from_produced_files, never re-derived. This is the fix: it validates what
    # actually ships (the analytic cylinder model) and does zero re-conversion, so it
    # no longer stalls on nvme write-contention writing ~1 GB of temp files.
    #
    # Genie-XML sources take a produced-files COUNT path (parity_gxml_from_produced_files):
    # the old re-derive (load + export via parity's own Python writers + reload) tripled
    # once curved shells thicken by default and dominated the sweep; every produced format
    # has a cheap counter instead (xml structure scan, ifc SPF line scan, native C++ step
    # stream index) so the whole check is seconds and validates exactly what shipped.
    #
    # Non-gxml CAD (STEP/IFC/SAT) sources keep the streaming/whole-model re-derive path
    # (produced left empty -> the child calls parity_for_source_file). Those were never
    # the hang; and their Genie-XML output is legitimately empty for a raw-solid source
    # (no Beam/Plate concept), which parity_for_source_file correctly SKIPS rather than
    # flagging as dropped geometry.
    _FEM_PARITY_SUFFIXES = (".fem", ".inp", ".sif", ".sin")
    _PRODUCED_PARITY_SUFFIXES = _FEM_PARITY_SUFFIXES + (".xml",)
    produced_dir = pathlib.Path(tempfile.mkdtemp(prefix="adapy-parity-"))
    produced: dict[str, str | None] = {}
    if suffix in _PRODUCED_PARITY_SUFFIXES:
        targets = set(ConverterRegistry.targets_for(suffix))
        compare_formats = tuple(f for f in PARITY_GEOMETRY_FORMATS if f in targets)
        for fmt in compare_formats:
            try:
                dkey = derived_key_for(job.source_key, fmt)
            except Exception:
                produced[fmt] = None
                continue
            dpath = produced_dir / f"produced.{fmt}"
            try:
                await storage.stream_to_path(scope, dkey, dpath)
                produced[fmt] = str(dpath)
            except FileNotFoundError:
                produced[fmt] = None
            except Exception:
                logger.exception("worker: parity fetch of produced %s failed for %s", fmt, job.source_key)
                produced[fmt] = None

    try:
        iresult: IsolatedConvertResult = await run_isolated_convert(
            _parity_child,
            src_path,
            job.source_key,
            "parity",
            convert_kwargs={"produced": produced},
            on_progress=_on_progress,
            timeout_s=timeout_s,
            cancel_check=_cancel_check,
        )
    except Exception as exc:
        logger.exception("worker: parity subprocess wrapper failed for %s", job.source_key)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return
    finally:
        # The forked child has read the produced blobs by the time the call returns
        # (or raises); drop the fetched copies either way.
        shutil.rmtree(produced_dir, ignore_errors=True)

    metrics = dict(iresult.final_metrics)

    # User cancellation: the watchdog reaped the child; the row is already
    # 'cancelled' (set by the cancel endpoint) — don't flip it to error.
    if iresult.signal_name == "CANCELLED":
        try:
            await queue.update(job_id, status="cancelled", stage="parity", error="cancelled by user")
        except Exception:
            pass
        iresult.cleanup_output()
        return

    # OOM / timeout / crash / no-output: the child died (memory watchdog
    # SIGKILL, timeout, SIGSEGV) — surface as an error cell, pod intact.
    if iresult.exit_code != 0 or iresult.out_path is None:
        err = iresult.error or "parity subprocess produced no output"
        if iresult.signal_name:
            logger.warning("worker: parity child for %s ended via %s", job.source_key, iresult.signal_name)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=err)
        await _audit_done(db_pool, job_id, "error", err, started_at, traceback=iresult.traceback, metrics=metrics)
        iresult.cleanup_output()
        return

    try:
        payload = json.loads(iresult.out_path.read_text())
    except Exception as exc:
        logger.exception("worker: parity result decode failed for %s", job.source_key)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, metrics=metrics)
        iresult.cleanup_output()
        return
    iresult.cleanup_output()

    if db_pool is not None:
        try:
            await db_module.insert_audit_parity(
                db_pool,
                job_id=job_id,
                source_key=job.source_key,
                baseline=payload["expected"],
                counts=payload["counts"],
                consistent=payload["consistent"],
                mismatches=payload["mismatches"],
                errors=payload["errors"],
            )
        except Exception:
            logger.exception("worker: insert_audit_parity failed for %s", job.source_key)

    if payload["consistent"]:
        await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(db_pool, job_id, "done", None, started_at, metrics=metrics)
    else:
        msg = payload.get("summary") or "parity mismatch"
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="ready", progress=1.0, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, metrics=metrics)


class ParityHandler(SyntheticFormatHandler if not True else SourceFormatHandler):
    kind = "parity"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_parity_validation(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            src_path=ctx.src_path,
            _on_progress=ctx.on_progress,
            timeout_s=ctx.settings.timeout_s,
        )

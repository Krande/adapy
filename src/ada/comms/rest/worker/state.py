"""Process-wide worker state: the job→Scope helper, liveness touch, sidecar table and the
mutable slots ``_run`` publishes for handlers (image tag, stop event, storage).

Mutable slots are read through ``state.<name>`` at call time so a value set at startup (or
patched by a test) is seen everywhere.
"""

from __future__ import annotations

import asyncio
import os
import time

from ada.config import logger

from ..queue import Job
from ..scope import Scope
from ..storage import Storage


def _scope_of(job: Job) -> Scope:
    """Reconstruct the Scope a job's source/derived blobs live under.
    Defaults to ``shared`` for jobs serialized before scope_kind existed.
    """
    if job.scope_kind == "project" and job.scope_id:
        return Scope.project(job.scope_id)
    if job.scope_kind == "user" and job.scope_id:
        return Scope.user(job.scope_id)
    if job.scope_kind == "corpus" and job.scope_id:
        return Scope.corpus(job.scope_id)
    return Scope.shared()


# Liveness heartbeat. The worker touches this file whenever its JetStream pull loop iterates
# (idle / between jobs) or an in-flight conversion reports progress. A k8s livenessProbe checks the
# file's mtime is fresh: if the pull loop stalls — e.g. the durable consumer wedged after a NATS
# restart, leaving the pod "Running" but no longer fetching (num_waiting=0) — the file goes stale and
# k8s restarts the pod, instead of it sitting silently broken while jobs pile up unconsumed.
WORKER_LIVENESS_FILE = os.environ.get("WORKER_LIVENESS_FILE", "/tmp/worker-alive")


def _touch_liveness() -> None:
    try:
        with open(WORKER_LIVENESS_FILE, "w") as fh:
            fh.write(str(time.time()))
    except OSError:
        logger.debug("worker: liveness touch failed", exc_info=True)


# Per-source-suffix sidecar files that the worker co-downloads next to
# the main payload so format-specific readers find them by basename.
# Keep this conservative — a 404 on an absent sibling is silent, but
# we still pay one S3 HEAD per attempt. Add entries here as readers
# grow new sidecar needs; ``.adapy_fem.json`` is the code_aster
# lineage + per-element tessellation companion.
_SIDECAR_SIBLINGS: dict[str, tuple[str, ...]] = {
    ".rmed": (".adapy_fem.json",),
}


# Set once at worker startup from ``ADA_IMAGE_TAG`` (helm chart
# stamps the build SHA into that env var). Read here without
# threading through every call site so the audit row gets the same
# attribution we publish on the workers KV registry without
# touching the per-job code paths.
_WORKER_IMAGE_TAG: str | None = None


# The worker's graceful-shutdown event, published module-level by ``run_worker``
# so long-running poll loops inside a handler (e.g. the chained procedural_detail
# stage waiting on the structural build) can wake early on SIGTERM/SIGINT instead
# of blocking the pod's shutdown for the full wait budget. ``None`` until the
# worker loop wires it (a handler running in a unit test just sees no stop event
# and polls to its timeout).
_WORKER_STOP: "asyncio.Event | None" = None


# Published for failure capture, which hangs off _audit_done — a dozen error
# paths reach that with only a job id, and threading a Storage through each of
# them to preserve a blob would be a lot of plumbing for one best-effort copy.
_WORKER_STORAGE: "Storage | None" = None


# Chained procedural_detail waits for the upstream structural build (a DIFFERENT
# pool, no ordering guarantee) to write the neutral artifact before it runs. Poll
# storage for the artifact up to this total budget, sleeping this interval between
# checks (interruptible on shutdown). 120 s comfortably covers a realistic
# structural compile; 3 s keeps the poll cheap without busy-spinning.
STRUCTURAL_ARTIFACT_WAIT_BUDGET_S = 120.0


STRUCTURAL_ARTIFACT_WAIT_INTERVAL_S = 3.0


# Once the (required) IFC artifact exists the sections sidecar — written moments
# later by the same build — should appear almost immediately; give it a short
# grace before degrading to an empty sidecar.
STRUCTURAL_SECTIONS_WAIT_BUDGET_S = 15.0

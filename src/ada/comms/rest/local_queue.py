"""The queue in front of every job a queue-less server runs itself.

WHY. A local viewer runs its jobs in its own environment (:mod:`local_jobs`): conversions and
bakes in job processes, the lighter kinds on threads. Started as they arrive, a folder upload
starts one conversion per file, a capacity check and a bake start beside them, and a laptop pages
itself to a halt. So every local job, of every kind, passes through ONE queue here:

* **FIFO** with a cap on how many run at once (``max_parallel``). A job that cannot start yet
  stays ``running`` with stage ``queued (n ahead)``, read by the same polling route as any job.
* **Cancel while queued** removes the job without ever starting it.
* **A slot is freed however the job ends** -- done, failed, crashed, cancelled: ``release`` is in
  a ``finally`` of every caller.
* **A CPU budget** (``cpu_budget``, in cores) is divided over the jobs running: a job is given
  ``threads = budget // running`` (at least 1) when it starts. A job process gets that as its
  thread environment (``thread_env``) -- the OpenMP/BLAS pools and adapy's own tessellation and
  gzip workers -- and, on Linux when the budget is below the machine, an affinity to that many
  CPUs of its own. A thread job cannot be given an environment; it counts against the cap and
  the division all the same.

LIGHT JOBS DO NOT WAIT BEHIND HEAVY ONES. A few kinds finish in well under a second (detailing a
handful of joints, writing a publish's manifests, reading a result deck's inventory); queued
behind ten conversions they would make a click wait minutes for no reason. They are FIFO among
themselves and may take ONE extra lane (``LIGHT_LANES``) when the cap is full. A separate
allowance rather than a priority: a priority would let a stream of light jobs hold every slot,
while one extra lane bounds what they can take -- the machine is oversubscribed by at most one
short job.

Limits change at run time (:meth:`LocalJobQueue.configure`, from the settings route) and apply
to the next job that starts; a running job keeps what it was given.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Any

#: Extra concurrent lanes for light jobs beyond ``max_parallel``.
LIGHT_LANES = 1

#: Environment variables a job process gets its thread count in. The OpenMP / BLAS / numexpr
#: pools, adapy's tessellation and native-reader workers (``ADA_STEP_STREAM_WORKERS``, which the
#: adacpp STEP paths also size their threads from) and the at-rest gzip pass.
THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "ADA_STEP_STREAM_WORKERS",
    "ADA_DERIVED_GZIP_THREADS",
)

ENV_MAX_PARALLEL = "ADA_LOCAL_JOB_PROCESSES"
ENV_CPU_BUDGET = "ADA_LOCAL_JOB_CPU_BUDGET"


def host_cores() -> int:
    """The CPUs this process may run on."""
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def default_max_parallel(cores: int | None = None) -> int:
    return max(1, min(4, (cores or host_cores()) // 2))


def parse_max_parallel(raw: Any) -> int | None:
    """A cap from a setting or the environment: an integer 1..256, or None for the default.
    Raises ``ValueError`` for anything else."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ValueError("max_parallel must be an integer")
    try:
        n = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"max_parallel must be an integer, not {raw!r}") from exc
    if not 1 <= n <= 256:
        raise ValueError("max_parallel must be between 1 and 256")
    return n


def parse_cpu_budget(raw: Any, cores: int | None = None) -> float | None:
    """A CPU budget in cores, from ``"50%"``, a fraction below 1 (``0.5``), or a core count
    (``4``, ``2.5``); capped at the machine. None (or empty) means no budget: every core.
    Raises ``ValueError`` for anything else."""
    cores = cores or host_cores()
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ValueError("cpu_budget must be a number of cores, a fraction or a percentage")
    text = str(raw).strip()
    try:
        if text.endswith("%"):
            pct = float(text[:-1])
            if not 0 < pct <= 100:
                raise ValueError
            value = cores * pct / 100.0
        else:
            x = float(text)
            if x <= 0:
                raise ValueError
            value = cores * x if x < 1 else x
    except ValueError as exc:
        raise ValueError(
            f"cpu_budget must be a number of cores (4), a fraction (0.5) or a percentage (50%), not {raw!r}"
        ) from exc
    return min(float(cores), max(1.0, value))


@dataclass(frozen=True)
class LocalJobLimits:
    """The limits in force, and where each came from (for the settings view)."""

    max_parallel: int
    #: Cores; ``None`` = every core (no budget).
    cpu_budget: float | None
    max_parallel_raw: Any = None
    cpu_budget_raw: Any = None

    @property
    def budget_cores(self) -> float:
        return self.cpu_budget if self.cpu_budget is not None else float(host_cores())

    def to_json(self) -> dict[str, Any]:
        return {
            "max_parallel": self.max_parallel,
            "cpu_budget": self.cpu_budget_raw,
            "cpu_budget_cores": self.budget_cores,
            "limited": self.cpu_budget is not None,
        }


def limits_from(max_parallel: Any = None, cpu_budget: Any = None) -> LocalJobLimits:
    """Limits from raw values (a setting, the environment); None falls back to the default."""
    mp = parse_max_parallel(max_parallel)
    cb = parse_cpu_budget(cpu_budget)
    return LocalJobLimits(
        max_parallel=mp if mp is not None else default_max_parallel(),
        cpu_budget=cb,
        max_parallel_raw=mp,
        cpu_budget_raw=None if cb is None else (str(cpu_budget).strip() if cpu_budget is not None else None),
    )


def env_limits() -> LocalJobLimits:
    """The limits the environment asks for (``ADA_LOCAL_JOB_PROCESSES``,
    ``ADA_LOCAL_JOB_CPU_BUDGET``). A malformed value is ignored, as if unset."""
    try:
        mp = parse_max_parallel(os.environ.get(ENV_MAX_PARALLEL))
    except ValueError:
        mp = None
    raw_cb = os.environ.get(ENV_CPU_BUDGET)
    try:
        parse_cpu_budget(raw_cb)
    except ValueError:
        raw_cb = None
    return limits_from(mp, raw_cb)


def env_overrides_present() -> bool:
    """Whether the launch set either limit (env, or ``ada serve`` / ``ada desktop`` flags)."""
    return bool((os.environ.get(ENV_MAX_PARALLEL) or "").strip() or (os.environ.get(ENV_CPU_BUDGET) or "").strip())


@dataclass
class _Ticket:
    job: Any
    light: bool
    threads: int = 1
    cpus: tuple[int, ...] = ()


class LocalJobQueue:
    """See the module docstring. Thread-safe; ``acquire`` blocks its caller's thread."""

    POLL_S = 0.25

    def __init__(self, limits: LocalJobLimits | None = None) -> None:
        self._cond = threading.Condition()
        self._waiting: list[_Ticket] = []
        self._running: dict[str, _Ticket] = {}
        self._limits = limits or env_limits()

    # -- limits ---------------------------------------------------------------------------------

    @property
    def limits(self) -> LocalJobLimits:
        return self._limits

    def configure(self, limits: LocalJobLimits) -> None:
        """New limits for the next job that starts; running jobs keep theirs."""
        with self._cond:
            self._limits = limits
            self._cond.notify_all()

    # -- the queue ------------------------------------------------------------------------------

    def _live_locked(self) -> list[_Ticket]:
        """The running tickets that still hold a slot. A job already ruled on -- finished but
        tidying up, or abandoned by the registry's age sweep (a plugin thread that never returns)
        -- does not: one stuck thread must not wedge the queue for ever."""
        from .local_jobs import STATUS_RUNNING

        return [r for r in self._running.values() if r.job.status == STATUS_RUNNING]

    def _admissible_locked(self, t: _Ticket) -> bool:
        live = self._live_locked()
        heavy_running = sum(1 for r in live if not r.light)
        light_running = len(live) - heavy_running
        if t.light:
            # FIFO among light jobs; a free main lane, else the light allowance.
            first_light = next((w for w in self._waiting if w.light), None)
            if first_light is not t:
                return False
            return len(live) < self._limits.max_parallel or light_running < LIGHT_LANES
        first_heavy = next((w for w in self._waiting if not w.light), None)
        if first_heavy is not t:
            return False
        # Light jobs in their extra lane do not count against the cap.
        light_in_main = max(0, light_running - LIGHT_LANES)
        return heavy_running + light_in_main < self._limits.max_parallel

    def _positions_locked(self) -> None:
        heavy_ahead = 0
        light_ahead = 0
        for w in self._waiting:
            n = light_ahead if w.light else heavy_ahead
            w.job.stage = f"queued ({n} ahead)" if n else "queued (next)"
            if w.light:
                light_ahead += 1
            else:
                heavy_ahead += 1

    def _assign_locked(self, t: _Ticket) -> None:
        limits = self._limits
        share = int(limits.budget_cores // max(1, len(self._live_locked())))
        t.threads = max(1, share)
        if limits.cpu_budget is not None and sys.platform.startswith("linux"):
            try:
                allowed = sorted(os.sched_getaffinity(0))
            except (AttributeError, OSError):
                allowed = []
            budget = max(1, int(limits.cpu_budget))
            pool = allowed[:budget]
            taken = {c for r in self._running.values() if r is not t for c in r.cpus}
            free = [c for c in pool if c not in taken]
            # Own CPUs while there are enough; otherwise share the budget's (never beyond it).
            chosen = free[: t.threads] if len(free) >= t.threads else pool[: t.threads]
            t.cpus = tuple(chosen)

    def acquire(self, job: Any, *, light: bool = False) -> bool:
        """Wait for ``job``'s turn. True: it may start (call :meth:`release` when it ends).
        False: it was cancelled (or ruled on) while it waited, and must not start -- its status
        is already final."""
        from .local_jobs import STATUS_CANCELLED, STATUS_RUNNING

        t = _Ticket(job=job, light=light)
        with self._cond:
            self._waiting.append(t)
            self._positions_locked()
            while True:
                if job.cancel_event.is_set() or job.status != STATUS_RUNNING:
                    self._waiting.remove(t)
                    if job.status == STATUS_RUNNING:
                        job.status, job.stage = STATUS_CANCELLED, "cancelled"
                    self._positions_locked()
                    self._cond.notify_all()
                    return False
                if self._admissible_locked(t):
                    self._waiting.remove(t)
                    self._running[job.job_id] = t
                    self._assign_locked(t)
                    job.threads = t.threads
                    job.cpus = t.cpus
                    job.stage = "starting"
                    self._positions_locked()
                    self._cond.notify_all()
                    return True
                self._cond.wait(self.POLL_S)

    def release(self, job: Any) -> None:
        with self._cond:
            self._running.pop(job.job_id, None)
            self._cond.notify_all()

    def wake(self) -> None:
        """Re-evaluate the waiters now (a cancel, a limits change)."""
        with self._cond:
            self._cond.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._cond:
            return {
                "running": [
                    {"job_id": jid, "kind": t.job.plugin_id, "threads": t.threads, "light": t.light}
                    for jid, t in self._running.items()
                ],
                "queued": [{"job_id": w.job.job_id, "kind": w.job.plugin_id, "light": w.light} for w in self._waiting],
            }


def thread_env(threads: int) -> dict[str, str]:
    """The environment that holds a job process to ``threads`` threads."""
    n = str(max(1, int(threads)))
    return {k: n for k in THREAD_ENV_VARS}


#: The one queue, module-level like ``local_jobs.registry``.
queue = LocalJobQueue()

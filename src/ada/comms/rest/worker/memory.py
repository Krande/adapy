"""Parent-process memory hygiene and self-resource probes (/proc, rusage).
"""

from __future__ import annotations

import ctypes
import os
import pathlib

_LIBC_MALLOC_TRIM: object = None  # cached glibc malloc_trim, or False when unavailable


def _trim_parent_memory() -> None:
    """Return glibc's freed-but-retained arena memory to the OS in the long-lived worker PARENT
    after each job. The parent forks per conversion; the freed per-job allocations it makes itself
    (reading the child's captured log, parsing the marker JSON / cpp profile, building convert_meta,
    the metrics-sample lists) pile up in glibc's arena free-lists rather than returning to the OS, so
    parent RSS creeps up across a run (measured on a 23h prod worker: 218 MB fresh -> 540-840 MB idle,
    higher mid-run). Because every conversion is a fork, the child INHERITS the parent's address space
    (COW, counted in the child's RSS), so a bloated parent lifts EVERY conversion's baseline — enough
    to push a big-model fork (469826) over the per-job memory watchdog, and the parent+child sum over
    the 6 GiB pod limit (the run-90 pod OOM / Exit 137). Trimming after each job keeps the parent flat.
    glibc-only; a best-effort no-op elsewhere. Disable with ADA_WORKER_NO_MALLOC_TRIM."""
    global _LIBC_MALLOC_TRIM
    if _LIBC_MALLOC_TRIM is None:
        if os.environ.get("ADA_WORKER_NO_MALLOC_TRIM"):
            _LIBC_MALLOC_TRIM = False
        else:
            try:
                _LIBC_MALLOC_TRIM = ctypes.CDLL("libc.so.6").malloc_trim
            except (OSError, AttributeError):
                _LIBC_MALLOC_TRIM = False
    if _LIBC_MALLOC_TRIM:
        try:
            _LIBC_MALLOC_TRIM(0)
        except Exception:  # noqa: BLE001 — memory hygiene must never fail a job
            pass


def _read_self_proc_io() -> tuple[int, int]:
    """Return ``(read_bytes, write_bytes)`` for THIS process from
    ``/proc/self/io``. Best-effort: returns ``(0, 0)`` off Linux or when the
    file is unreadable, so a missing counter never breaks the harness."""
    read_bytes = 0
    write_bytes = 0
    try:
        for line in pathlib.Path("/proc/self/io").read_text().splitlines():
            if line.startswith("read_bytes:"):
                read_bytes = int(line.split()[1])
            elif line.startswith("write_bytes:"):
                write_bytes = int(line.split()[1])
    except (FileNotFoundError, PermissionError, ValueError, IndexError):
        pass
    return read_bytes, write_bytes


def _read_self_vmhwm_kb() -> int:
    """Return this process's peak resident set (VmHWM, kB) from
    ``/proc/self/status``; ``0`` when unavailable (non-Linux)."""
    try:
        for line in pathlib.Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except (FileNotFoundError, ValueError, IndexError):
        pass
    return 0


def _read_self_rusage() -> "tuple[float, float, int] | None":
    """``(user_seconds, sys_seconds, max_rss_kb)`` for this process and its
    children, or ``None`` where the counters cannot be read.

    ``resource`` is POSIX-only and does not exist on Windows. That matters
    because the plugin-job profiling harness runs IN-PROCESS — unlike the
    convert path, which does its accounting in a forked child that only ever
    exists on POSIX — so an unguarded ``import resource`` there would fail the
    JOB, not just the measurement, on any Windows worker with profiling on.
    Best-effort, like the ``/proc`` readers above: a counter we cannot read is
    a poorer audit row, never a failed conversion.

    Whole-process by construction: the executor model cannot isolate one
    thread's counters, so the numbers include any concurrent work on this
    worker.
    """
    try:
        import resource
    except ModuleNotFoundError:  # Windows
        return None
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (
        me.ru_utime + kids.ru_utime,
        me.ru_stime + kids.ru_stime,
        int(max(me.ru_maxrss, kids.ru_maxrss)),
    )

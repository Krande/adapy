"""Run the synchronous ``convert()`` call in a forked child process.

Two motivations layered into one component:

* **Crash isolation (B)** — adapy's CAD/FEM stack is OCCT-bound, and
  upstream OCCT bugs occasionally surface as glibc heap corruption
  (``double free`` / SIGABRT) or segfault. A crash inside a thread of
  the asyncio worker takes the whole pod down; doing the convert in a
  child means the worker survives and acks the message as failed.
  Captured rusage on ``os.wait4`` gives us peak RSS / CPU even for
  jobs killed by signal — exactly the data that disappeared from the
  audit row before this change.

* **Time-series resource samples (A)** — while the child runs, the
  parent reads ``/proc/<pid>/{status,stat,io}`` on a fixed cadence
  (~2 s) and emits one snapshot per heartbeat. Persisted as JSONB on
  the audit row, the SPA can plot RSS/CPU/IO against wall time so an
  operator sees *where* in the conversion the resource pressure built
  up — not just the post-mortem peak.

Implementation notes:

* ``os.fork()`` (not ``multiprocessing``) so we can call
  ``os.wait4(pid, 0)`` and harvest ``rusage`` directly — the
  multiprocessing helper reaps children with plain ``waitpid`` and
  drops the rusage struct.
* Child inherits the asyncio loop / asyncpg pool / NATS sockets but
  never touches them. We exit via ``os._exit`` so no cleanup runs
  that might double-close inherited file descriptors.
* Progress callbacks come back as newline-delimited JSON over a pipe;
  the parent forwards them to the existing queue-update flow.
"""

from __future__ import annotations

import asyncio
import dataclasses
import fcntl
import json
import logging
import os
import pathlib
import resource as _resource_mod  # noqa: F401 — imported for posterity
import shutil
import signal
import sys
import tempfile
import time
import traceback
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("ada")


def _move_into_result(src: str, dst: pathlib.Path) -> None:
    """Move the handler's output file into the result slot.

    ``os.replace`` is an atomic rename when ``src`` and ``dst`` share a
    filesystem (they normally do — both under ``$TMPDIR``), so the
    multi-hundred-MB output never gets copied. Cross-device falls back to a
    chunked copy + unlink so a split ``/tmp`` mount still works."""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
        try:
            os.unlink(src)
        except OSError:
            pass


@dataclasses.dataclass
class ConvertSample:
    ts: float  # epoch seconds (sample wall-clock time)
    elapsed_s: float  # seconds since job start (parent's monotonic clock)
    cpu_user_ms: int
    cpu_sys_ms: int
    rss_kb: int  # current resident set size
    peak_rss_kb: int  # high-water mark (VmHWM)
    read_bytes: int
    write_bytes: int
    # Per-thread cumulative CPU (utime+stime, ms) keyed by tid. Drives the per-core utilization
    # envelope for the in-process native engine (its C++ tessellation threads live in this process).
    # None on older rows / when /proc/<pid>/task can't be read.
    per_thread_cpu_ms: Optional[dict] = None


@dataclasses.dataclass
class IsolatedConvertResult:
    # Path to the conversion output on disk (in a per-job work dir), or None
    # on failure. The caller streams it to storage (Storage.put_path) without
    # reading it into RAM, then calls cleanup_output(). Carrying a path rather
    # than bytes is what keeps a multi-hundred-MB output off the parent heap.
    out_path: Optional[pathlib.Path]
    error: Optional[str]  # exception message from child, when present
    traceback: Optional[str]  # python traceback string from child
    exit_code: int  # 0 success; >0 clean error; <0 = -signal
    signal_name: Optional[str]  # e.g. "SIGABRT" when killed by signal
    samples: list[ConvertSample]
    final_metrics: dict  # {cpu_user_ms, cpu_sys_ms, peak_rss_kb, read_bytes, write_bytes}
    profile_bytes: Optional[bytes] = None  # cProfile dump from child, when enabled
    log_bytes: Optional[bytes] = None  # captured child stdout+stderr (Python logging + C++ libs)

    def cleanup_output(self) -> None:
        """Remove the on-disk output file and its work dir. Idempotent and
        safe when ``out_path`` is None — the caller invokes it in a finally
        once the upload (or its failure handling) is done."""
        if self.out_path is None:
            return
        try:
            self.out_path.unlink()
        except OSError:
            pass
        try:
            self.out_path.parent.rmdir()
        except OSError:
            pass
        self.out_path = None


def _per_thread_cpu_ms(pid: int, clock_ticks: int) -> Optional[dict]:
    """Per-thread cumulative CPU (utime+stime, ms) for ``pid``, keyed by tid string.

    Reads ``/proc/<pid>/task/<tid>/stat`` for each thread. Best-effort: a thread that exits between
    the readdir and the open is skipped; returns None when the task dir can't be listed at all.
    Cheap — a handful of small reads for the native engine's thread pool, once per ~2 s sample."""
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return None
    out: dict[str, int] = {}
    for tid in tids:
        try:
            tstat = pathlib.Path(f"/proc/{pid}/task/{tid}/stat").read_text()
        except OSError:
            continue
        rest = tstat[tstat.rfind(")") + 1 :].split()
        try:
            out[tid] = int((int(rest[11]) + int(rest[12])) * 1000 / clock_ticks)
        except (IndexError, ValueError):
            continue
    return out or None


def _proc_stats(pid: int, started_at: float) -> Optional[ConvertSample]:
    """Read ``/proc/<pid>/{status,stat,io}`` and return one snapshot.

    Returns None when the pid disappears (child exited between the
    last alive-check and the open() — common at the end of the loop)."""
    try:
        status = pathlib.Path(f"/proc/{pid}/status").read_text()
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    rss_kb = 0
    peak_rss_kb = 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            rss_kb = int(line.split()[1])
        elif line.startswith("VmHWM:"):
            peak_rss_kb = int(line.split()[1])
    # The comm field can contain spaces and parens. Split fields after the
    # last ')' to dodge that.
    rparen = stat.rfind(")")
    rest = stat[rparen + 1 :].split()
    # Fields after comm in /proc/PID/stat (man 5 proc):
    #   state(0) ppid(1) ... utime(11) stime(12) ...
    try:
        utime_ticks = int(rest[11])
        stime_ticks = int(rest[12])
    except (IndexError, ValueError):
        return None
    clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    cpu_user_ms = int(utime_ticks * 1000 / clock_ticks)
    cpu_sys_ms = int(stime_ticks * 1000 / clock_ticks)
    read_bytes = 0
    write_bytes = 0
    try:
        io = pathlib.Path(f"/proc/{pid}/io").read_text()
        for line in io.splitlines():
            if line.startswith("read_bytes:"):
                read_bytes = int(line.split()[1])
            elif line.startswith("write_bytes:"):
                write_bytes = int(line.split()[1])
    except (FileNotFoundError, PermissionError):
        # /proc/PID/io requires the same uid/gid; should always work for our own children.
        pass
    return ConvertSample(
        ts=time.time(),
        elapsed_s=max(0.0, time.monotonic() - started_at),
        cpu_user_ms=cpu_user_ms,
        cpu_sys_ms=cpu_sys_ms,
        rss_kb=rss_kb,
        peak_rss_kb=peak_rss_kb,
        read_bytes=read_bytes,
        write_bytes=write_bytes,
        per_thread_cpu_ms=_per_thread_cpu_ms(pid, clock_ticks),
    )


def _read_rss_kb(pid: int) -> Optional[int]:
    """Cheap VmRSS-only read for the per-iteration memory watchdog (the full
    ``_proc_stats`` reads three /proc files and only runs on the sample cadence)."""
    try:
        for line in pathlib.Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return None
    return None


def _resolve_mem_limit_bytes() -> Optional[int]:
    """RSS ceiling for the conversion child, or None to disable the watchdog.

    Explicit ``ADA_CONVERT_MEM_LIMIT_MB`` wins; otherwise derive it from the
    container's cgroup memory limit (v2 then v1) at 85% — leaving headroom for
    the parent worker — so the child is reaped *before* it can OOM-kill the whole
    pod. The point: an out-of-memory conversion dies in isolation, the parent
    survives and acks the job as failed once, instead of taking down the pod and
    triggering an ~80 min redelivery loop."""
    env = os.environ.get("ADA_CONVERT_MEM_LIMIT_MB", "").strip()
    if env:
        try:
            mb = int(env)
            return mb * 1024 * 1024 if mb > 0 else None
        except ValueError:
            pass
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = pathlib.Path(path).read_text().strip()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if raw == "max":
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        # Ignore the "effectively unlimited" sentinel cgroups use when uncapped.
        if limit <= 0 or limit > (1 << 50):
            continue
        return int(limit * 0.85)
    return None


def _set_nonblocking(fd: int) -> None:
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)


def _signal_child(pid: int, sig: int) -> None:
    """Send ``sig`` to the child. If it is its own process-group leader (it called
    ``setsid``), signal the whole group so any tessellation worker pool dies with it;
    otherwise fall back to signalling just the child. The pgid==pid guard makes the
    group-kill safe — we never accidentally signal the parent worker's group."""
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _flush_std() -> None:
    """Flush Python's stdout/stderr buffers before the child os._exit()s — block-buffered
    stdout (non-tty) is otherwise lost, so its lines never reach the captured log file."""
    import sys as _sys

    for s in (_sys.stdout, _sys.stderr):
        try:
            s.flush()
        except Exception:
            pass


async def run_isolated_convert(
    convert_fn: Callable[..., "bytes | pathlib.Path"],
    src_path: pathlib.Path,
    source_key: str,
    target_format: str,
    convert_kwargs: Optional[dict] = None,
    on_progress: Optional[Callable[[str, float], Awaitable[None]]] = None,
    on_sample: Optional[Callable[[ConvertSample], Awaitable[None]]] = None,
    sample_interval_s: float = 2.0,
    profile_in_child: bool = False,
    env_overrides: Optional[dict[str, str]] = None,
    timeout_s: Optional[float] = None,
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> IsolatedConvertResult:
    """Fork, run ``convert_fn`` in the child, sample resource usage in
    the parent, and join with full rusage on exit.

    ``on_progress`` is invoked with ``(stage, frac)`` for each progress
    line the child emits. ``on_sample`` is invoked once per heartbeat
    sample so the caller can stream them to the database without
    waiting for the child to exit (important for crash cases where the
    in-memory list is the only record of partial progress).

    ``profile_in_child`` enables cProfile inside the child process; the
    profiler can't meaningfully cross a fork boundary so we attach
    it to the child's interpreter and ship the dump back via a
    sidecar tempfile read after exit. The dump survives clean errors
    too (the child writes it in a finally), which is what makes
    "profile a job that fails after 6 minutes" actually useful.

    ``timeout_s`` is the wall-clock budget. ``None`` (or non-positive)
    disables the watchdog — the conversion runs until it exits on its
    own. When set, the parent SIGTERMs the child after the deadline
    expires; if it doesn't die within 30 s of grace, SIGKILL follows.
    The returned :class:`IsolatedConvertResult` carries ``signal_name=
    "TIMEOUT"`` in that case so the worker can surface a clear,
    timeout-specific error rather than a generic "killed by signal".
    """
    convert_kwargs = convert_kwargs or {}

    work_dir = pathlib.Path(tempfile.mkdtemp(prefix="adapy-convert-"))
    result_path = work_dir / "out.bin"
    err_path = work_dir / "error.json"
    profile_path = work_dir / "profile.prof"
    log_path = work_dir / "convert.log"
    progr_r, progr_w = os.pipe()

    started_at = time.monotonic()
    samples: list[ConvertSample] = []

    pid = os.fork()
    if pid == 0:
        # ── child ──
        try:
            os.close(progr_r)
            # Default signal handlers in the child so a SIGTERM from
            # the orchestrator hits us cleanly rather than being
            # swallowed by the parent's asyncio handlers.
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    signal.signal(sig, signal.SIG_DFL)
                except (OSError, ValueError):
                    pass

            # Become a process-group leader so the watchdog can reap the whole
            # group on timeout / cancel — including any tessellation worker pool the
            # conversion spawns, which a bare kill(child_pid) would orphan.
            try:
                os.setsid()
            except OSError:
                pass

            # Per-job env overrides scoped to the child only — the
            # parent (and sibling jobs) keep their pristine env. Used
            # to flip ADA_USE_SAT_PCURVES / ADA_GLB_MERGE_MESHES /
            # etc. from app_settings or the per-conversion request
            # without a worker restart.
            if env_overrides:
                for k, v in env_overrides.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[str(k)] = str(v)

            # Capture everything the conversion emits — Python logging AND the adacpp/OCCT
            # C++ libraries' stdout/stderr — to a per-job log file at the fd level, so a
            # silently-swallowed library warning (e.g. "meshopt compression skipped") is
            # recoverable through the audit log instead of vanishing. Progress uses its own
            # pipe (progr_w), so redirecting fd 1/2 here doesn't disturb it.
            try:
                import sys as _sys

                _sys.stdout.flush()
                _sys.stderr.flush()
                _logfd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                os.dup2(_logfd, 1)
                os.dup2(_logfd, 2)
                os.close(_logfd)
            except OSError:
                pass

            # Line-buffer the child's streams so each log line reaches the captured file immediately.
            # Otherwise a block-buffered tail is lost when the RSS watchdog SIGKILLs the child mid-run
            # (OOM rows came back with an empty log — the very reason you couldn't see why it died).
            # ``reconfigure`` raises ValueError/AttributeError (not OSError), so it gets its own guard.
            try:
                _sys.stdout.reconfigure(line_buffering=True)
                _sys.stderr.reconfigure(line_buffering=True)
            except Exception:
                pass

            # Conversion log verbosity, set from the admin Conversion panel (app_settings
            # ``convert_log_level`` → ADA_CONVERT_LOG_LEVEL). Unset keeps the quiet WARNING default;
            # INFO/DEBUG surfaces per-stage progress + the native engine summary in the captured log.
            _log_level = os.environ.get("ADA_CONVERT_LOG_LEVEL", "").strip().upper()
            if _log_level:
                try:
                    logging.getLogger("ada").setLevel(_log_level)
                except (ValueError, TypeError):
                    pass

            def _child_progress(stage: str, frac: float) -> None:
                try:
                    line = json.dumps({"stage": stage, "frac": float(frac)}) + "\n"
                    os.write(progr_w, line.encode("utf-8"))
                except (BrokenPipeError, OSError):
                    pass

            profiler = None
            if profile_in_child:
                import cProfile

                profiler = cProfile.Profile()

            try:
                if profiler is not None:
                    profiler.enable()
                try:
                    out = convert_fn(
                        src_path,
                        source_key,
                        target_format,
                        _child_progress,
                        **convert_kwargs,
                    )
                finally:
                    if profiler is not None:
                        profiler.disable()
                        try:
                            profiler.dump_stats(str(profile_path))
                        except Exception:
                            pass
                if out is None:
                    out = b""
                if isinstance(out, (bytes, bytearray, memoryview)):
                    result_path.write_bytes(bytes(out))
                elif isinstance(out, (str, os.PathLike)):
                    # Handler wrote its output to disk and handed back the
                    # path; move it into the result slot rather than reading
                    # it into RAM here (the big-STEP child-copy we're killing).
                    _move_into_result(os.fspath(out), result_path)
                else:
                    raise TypeError(f"convert returned {type(out).__name__}, expected bytes or a path")
                # Emit per-conversion quality tallies for the parent to fold into convert_meta
                # (marker-line channel, same as the C++ [STEPPROF-JSON] profiler). Best-effort:
                # a tally failure must never fail an otherwise-successful conversion.
                try:
                    from ada.occ.tessellating import consume_mesh_distortion_stats, consume_tess_fallback_stats

                    fb = consume_tess_fallback_stats()
                    if fb.get("count"):
                        _sys.stderr.write("[TESSFALLBACK-JSON] " + json.dumps(fb) + "\n")
                    md = consume_mesh_distortion_stats()
                    if md.get("distorted_tris"):
                        _sys.stderr.write("[MESHHEALTH-JSON] " + json.dumps(md) + "\n")
                except Exception:
                    pass
                _flush_std()
                os._exit(0)
            except BaseException as exc:  # noqa: BLE001 — propagate verbatim
                # Even on failure, dump whatever profile data was
                # collected up to the exception — that's exactly the
                # data an operator needs to see *where* in the run we
                # crashed.
                if profiler is not None:
                    try:
                        profiler.disable()
                        profiler.dump_stats(str(profile_path))
                    except Exception:
                        pass
                err_path.write_text(
                    json.dumps(
                        {
                            "error": f"{type(exc).__name__}: {exc}",
                            "tb": traceback.format_exc(),
                        }
                    )
                )
                _flush_std()
                os._exit(2)
        finally:
            _flush_std()
            try:
                os.close(progr_w)
            except OSError:
                pass
            os._exit(3)

    # ── parent ──
    os.close(progr_w)
    _set_nonblocking(progr_r)

    pending_progress = b""

    def _drain_progress_lines() -> list[tuple[str, float]]:
        nonlocal pending_progress
        out: list[tuple[str, float]] = []
        while True:
            try:
                chunk = os.read(progr_r, 4096)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            pending_progress += chunk
        while b"\n" in pending_progress:
            line, _, pending_progress = pending_progress.partition(b"\n")
            try:
                msg = json.loads(line.decode("utf-8"))
                stage = str(msg.get("stage", ""))
                frac = float(msg.get("frac", 0.0))
                out.append((stage, frac))
            except (ValueError, TypeError):
                continue
        return out

    last_sample_time = 0.0
    final_status = 0
    final_rusage = None
    # Timeout watchdog state. ``deadline`` is None when no timeout is
    # configured. After the deadline we SIGTERM once, then SIGKILL
    # ``GRACE_S`` later if the child still hasn't exited. ``timed_out``
    # records that we triggered so the result can carry a specific
    # signal_name regardless of which signal actually reaped the child.
    deadline: Optional[float] = started_at + timeout_s if (timeout_s and timeout_s > 0) else None
    GRACE_S = 30.0
    CANCEL_GRACE_S = 5.0  # cancellation should stop ASAP; OCC ignores SIGTERM so KILL soon
    CANCEL_POLL_S = 3.0
    sigterm_sent_at: Optional[float] = None
    sigkill_sent = False
    timed_out = False
    cancelled = False
    kill_grace = GRACE_S
    last_cancel_check = started_at
    # RSS ceiling for the child (None = disabled). When it breaches we SIGKILL
    # immediately — unlike the timeout's graceful TERM-then-KILL — because the
    # alternative is the kernel OOM-killing the whole pod a moment later.
    mem_limit_bytes: Optional[int] = _resolve_mem_limit_bytes()
    oomed = False

    while True:
        try:
            wpid, status, rusage = os.wait4(pid, os.WNOHANG)
        except ChildProcessError:
            wpid, status, rusage = pid, 0, None  # already reaped
        if wpid == pid:
            final_status = status
            final_rusage = rusage
            break

        now = time.monotonic()

        # User cancellation — poll the source-of-truth (audit_log) every few seconds
        # and reap the child (group) when the job is cancelled, so an actively-running
        # conversion actually stops instead of completing into an orphaned blob.
        if cancel_check is not None and not cancelled and (now - last_cancel_check) >= CANCEL_POLL_S:
            last_cancel_check = now
            try:
                if await cancel_check():
                    cancelled = True
                    kill_grace = CANCEL_GRACE_S
                    logger.info("convert: %s cancelled by user; reaping child", source_key)
            except Exception:
                logger.debug("convert: cancel_check raised; ignoring this tick", exc_info=True)

        # Watchdog escalation (shared by timeout + cancel). Two-step (TERM then KILL)
        # so a converter with a SIGTERM cleanup path can flush; an OCCT-bound
        # tessellation that ignores SIGTERM still gets reaped within the grace window.
        timed_out_now = deadline is not None and now >= deadline
        if timed_out_now:
            timed_out = True
        if timed_out_now or cancelled:
            if sigterm_sent_at is None:
                _signal_child(pid, signal.SIGTERM)
                sigterm_sent_at = now
                logger.warning(
                    "convert: %s for %s; sent SIGTERM",
                    "cancelled" if cancelled else f"timeout {timeout_s:.0f}s exceeded",
                    source_key,
                )
            elif not sigkill_sent and (now - sigterm_sent_at) >= kill_grace:
                _signal_child(pid, signal.SIGKILL)
                sigkill_sent = True
                logger.warning("convert: SIGTERM not honoured for %s; sent SIGKILL", source_key)

        # Memory watchdog: reap the child before it can OOM-kill the whole pod.
        if mem_limit_bytes is not None and not sigkill_sent and not oomed:
            rss_kb = _read_rss_kb(pid)
            if rss_kb is not None and rss_kb * 1024 > mem_limit_bytes:
                oomed = True
                sigkill_sent = True
                _signal_child(pid, signal.SIGKILL)
                logger.warning(
                    "convert: RSS %.0f MB exceeded limit %.0f MB for %s; sent SIGKILL "
                    "(out of memory — failing the job in isolation rather than OOM-killing the pod)",
                    rss_kb / 1024.0,
                    mem_limit_bytes / 1e6,
                    source_key,
                )

        if now - last_sample_time >= sample_interval_s:
            sample = _proc_stats(pid, started_at)
            if sample is not None:
                samples.append(sample)
                if on_sample is not None:
                    try:
                        await on_sample(sample)
                    except Exception:
                        logger.exception("on_sample callback raised; continuing")
            last_sample_time = now

        for stage, frac in _drain_progress_lines():
            if on_progress is not None:
                try:
                    await on_progress(stage, frac)
                except Exception:
                    logger.exception("on_progress callback raised; continuing")

        await asyncio.sleep(0.1)

    # Drain any final progress lines the child wrote just before exit.
    for stage, frac in _drain_progress_lines():
        if on_progress is not None:
            try:
                await on_progress(stage, frac)
            except Exception:
                logger.exception("on_progress callback raised; continuing")
    try:
        os.close(progr_r)
    except OSError:
        pass

    exit_code = 0
    sig_name: Optional[str] = None
    if os.WIFEXITED(final_status):
        exit_code = os.WEXITSTATUS(final_status)
    elif os.WIFSIGNALED(final_status):
        signum = os.WTERMSIG(final_status)
        exit_code = -signum
        try:
            sig_name = signal.Signals(signum).name
        except (ValueError, KeyError):
            sig_name = f"signal {signum}"
    # Override sig_name when WE triggered the kill. The native signal
    # is SIGTERM or SIGKILL, but the operator wants to know the cell
    # was killed by the timeout watchdog, not that something else
    # crashed it. ``signal_name="TIMEOUT"`` is the contract the worker
    # checks against to write a specific error message.
    if cancelled:
        # We reaped it on user cancellation — surface a specific reason so the worker
        # records the job as cancelled rather than a crash.
        sig_name = "CANCELLED"
        if exit_code == 0:
            exit_code = -signal.SIGTERM
    elif oomed:
        # Our memory watchdog reaped it — surface a specific reason rather than
        # the native SIGKILL, so the operator sees "out of memory", not a crash.
        sig_name = "OOM"
        if exit_code == 0:
            exit_code = -signal.SIGKILL
    elif timed_out:
        sig_name = "TIMEOUT"
        if exit_code == 0:
            # ``exit_code=0`` would only happen if the child raced us
            # and exited cleanly between the watchdog firing and
            # wait4 returning. Flip it to non-zero so the result is
            # treated as an error.
            exit_code = -signal.SIGTERM

    out_path: Optional[pathlib.Path] = None
    error_msg: Optional[str] = None
    error_tb: Optional[str] = None
    profile_bytes: Optional[bytes] = None
    log_bytes: Optional[bytes] = None
    success = exit_code == 0 and result_path.exists()
    try:
        if success:
            # Hand the output back as a path; the caller streams it to storage
            # and calls cleanup_output() afterwards. We deliberately do NOT
            # read it into RAM here — that buffer was the parent-side peak.
            out_path = result_path
        if exit_code != 0 and err_path.exists():
            try:
                d = json.loads(err_path.read_text())
                error_msg = d.get("error") or None
                error_tb = d.get("tb") or None
            except (ValueError, TypeError, OSError):
                pass
        if profile_in_child and profile_path.exists():
            try:
                profile_bytes = profile_path.read_bytes()
            except OSError:
                pass
        # Captured child stdout/stderr — kept on success AND failure (a silently-swallowed
        # warning or a crash's last words are exactly what we want in the audit log).
        if log_path.exists():
            try:
                log_bytes = log_path.read_bytes() or None
            except OSError:
                pass
    finally:
        # Small sidecars are always reclaimed. The result file + its work dir
        # survive on success (ownership passes to the caller); on any failure
        # we drop them too so a crashed/oomed job leaves no tmp residue.
        for p in (err_path, profile_path, log_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        if not success:
            try:
                if result_path.exists():
                    result_path.unlink()
            except OSError:
                pass
            try:
                work_dir.rmdir()
            except OSError:
                pass

    if exit_code < 0 and error_msg is None:
        if oomed:
            lim_mb = (mem_limit_bytes or 0) / 1e6
            error_msg = (
                f"conversion ran out of memory (exceeded the {lim_mb:.0f} MB per-job "
                f"limit) and was terminated. The model is too large/heavy for this "
                f"worker's memory budget — raise the limit or reduce the geometry."
            )
        elif timed_out:
            mins = (timeout_s or 0) / 60.0
            error_msg = f"conversion exceeded the configured timeout " f"of {mins:.1f} minutes and was terminated."
        else:
            error_msg = (
                f"convert subprocess killed by {sig_name or f'signal {-exit_code}'} "
                f"(SIGSEGV/SIGABRT typically means a C++ heap fault inside the CAD/FEM stack)."
            )

    final_metrics: dict[str, Any] = {}
    if final_rusage is not None:
        final_metrics["cpu_user_ms"] = int(final_rusage.ru_utime * 1000)
        final_metrics["cpu_sys_ms"] = int(final_rusage.ru_stime * 1000)
        # Linux: ru_maxrss is in KB. macOS: bytes — we run on Linux in
        # the cluster, so KB. Document the assumption with a sys check
        # so we notice if this code ever ships on macOS workers.
        if sys.platform == "darwin":
            final_metrics["peak_rss_kb"] = int(final_rusage.ru_maxrss / 1024)
        else:
            final_metrics["peak_rss_kb"] = int(final_rusage.ru_maxrss)
    if samples:
        final_metrics.setdefault("read_bytes", samples[-1].read_bytes)
        final_metrics.setdefault("write_bytes", samples[-1].write_bytes)
        # If wait4 missed (non-Linux fallback) take peak RSS from samples.
        final_metrics.setdefault("peak_rss_kb", max(s.peak_rss_kb for s in samples))

    return IsolatedConvertResult(
        out_path=out_path,
        error=error_msg,
        traceback=error_tb,
        exit_code=exit_code,
        signal_name=sig_name,
        samples=samples,
        final_metrics=final_metrics,
        profile_bytes=profile_bytes,
        log_bytes=log_bytes,
    )

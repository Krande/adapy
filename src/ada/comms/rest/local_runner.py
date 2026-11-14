"""The process a queue-less server runs one heavy job in: ``python -m ada.comms.rest.local_runner``.

WHY A PROCESS. A server with no job queue still has to convert a file and bake a result deck,
because the person running it has nothing else that would: a tool that starts the viewer from its
own Python environment has every reader and plugin a worker has, and no worker. Running that work
on a thread of the server would put the server's life in the hands of OCC and the native readers,
where an out-of-memory or a heap fault takes the process with it. So each such job gets a child of
the SAME interpreter (``sys.executable``, the server's environment, its ``sys.path``, its
``ADA_WORKER_PRELOAD``), and the server survives whatever the job does.

WHAT RUNS HERE IS THE WORKER. The child calls the worker's own per-job pipeline,
``worker.process._process_one`` -- the cached-result short-circuit, the source fetch, the
conversion settings, the progress plumbing, then the format handler -- with no database and a
queue that is this process's pipe back to the server. Nothing about a handler knows which of the
two it runs under.

THE PROTOCOL. Small on purpose:

* stdin, first line: ``{"job": <queue.Job as a dict>, "storage": <Storage.process_spec()>}``.
  The server then keeps stdin open and never writes again; end-of-file means the server is gone,
  and the job goes with it.
* stdout: one JSON object per line, ``{"update": {...}}``, which is exactly a handler's
  ``queue.update(job_id, **fields)``. The real stdout is moved aside at start-up and fd 1 points
  at stderr, so a library that prints cannot corrupt the stream.
* stderr: everything else. The server keeps its tail to explain a crash.
* exit status: 0 whenever the pipeline returned (a job that failed said so over stdout); any other
  status, or a signal, is a crash -- the server reports it with the tail of stderr.

SIGTERM is the cancel: the forked conversion children this process waits on are killed with it
(``subprocess_convert.kill_live_children``), since each one is its own session.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import threading
import traceback
from typing import IO, Any


class PipeQueue:
    """The ``JobQueue`` a handler sees in this process: ``update`` goes to the server, ``get``
    answers for the one job this process runs. Anything else a handler might ask of a queue
    (chaining a job onto another pool) has no meaning without one, and says so."""

    enabled = False

    def __init__(self, job: Any, out: IO[str]) -> None:
        self._job = job
        self._out = out
        self._lock = threading.Lock()

    def send(self, **fields: Any) -> None:
        line = json.dumps({"update": fields}, default=str)
        with self._lock:
            try:
                self._out.write(line + "\n")
                self._out.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass  # the server is gone; the EOF watchdog ends this process

    async def get(self, job_id: str) -> Any:
        return self._job if job_id == self._job.job_id else None

    async def update(self, job_id: str, **fields: Any) -> None:
        for k, v in fields.items():
            if hasattr(self._job, k):
                setattr(self._job, k, v)
        self.send(**fields)

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            f"a job running without a queue cannot use queue.{name} -- this job kind needs a worker pool"
        )


def _abort(code: int) -> None:
    from ada.comms.rest.subprocess_convert import kill_live_children

    try:
        kill_live_children()
    finally:
        os._exit(code)


def _exit_when_the_server_goes(stdin: IO[bytes]) -> None:
    try:
        while stdin.read(65536):
            pass
    except Exception:
        pass
    _abort(1)


async def _run(payload: dict, proto: IO[str]) -> None:
    from ada.comms.rest.queue import Job
    from ada.comms.rest.storage import Storage

    job = Job(**payload["job"])
    queue = PipeQueue(job, proto)
    try:
        # The worker's half of ada: absent from a slim API image, which never starts this.
        from ada.comms.rest import formats  # noqa: F401 -- registers every handler
        from ada.comms.rest.worker.process import _process_one
    except ImportError as exc:
        queue.send(status="error", stage="error", error=f"this environment cannot run worker jobs: {exc}")
        return
    storage = Storage.from_spec(payload["storage"])
    try:
        await _process_one(job.job_id, queue, storage, None, None)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 -- the job's failure is data: it goes to the server
        traceback.print_exc()
        queue.send(status="error", stage="error", error=f"{type(exc).__name__}: {exc}")


def main() -> None:
    # The protocol stream is the original stdout; fd 1 becomes stderr for everything else.
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    proto = os.fdopen(proto_fd, "w", encoding="utf-8", buffering=1)

    stdin = sys.stdin.buffer
    line = stdin.readline()
    if not line:
        os._exit(1)
    payload = json.loads(line.decode("utf-8"))

    threading.Thread(target=_exit_when_the_server_goes, args=(stdin,), daemon=True).start()
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: _abort(143))

    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    from ada.comms.rest.plugin_registry import load_local_plugins

    load_local_plugins("local-runner")
    asyncio.run(_run(payload, proto))
    try:
        proto.flush()
        sys.stderr.flush()
    except Exception:
        pass
    # Not sys.exit: a native thread pool a reader left behind must not hold the process open.
    os._exit(0)


if __name__ == "__main__":
    main()

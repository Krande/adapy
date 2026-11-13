"""``ada dev connections``: a connection library served live to the studio.

Set ``ADA_DEV_CONNECTIONS`` to a library's ``ada_connections.toml`` (or its folder) and the viewer
holds that library in a child process (:mod:`ada.api.connections.devhost`), watches its files, and
reloads it on every change. The routes in :mod:`.routes.dev_connections` list its specs, build one
from the studio's inputs, and stream the reload events the studio rebuilds on.

Local development only: the launcher (``ada dev connections``) sets the variable on a loopback,
auth-less viewer. The library's code runs with the viewer user's rights, as any script would.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import queue as queue_mod
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from ada.config import logger

ENV_CONFIG = "ADA_DEV_CONNECTIONS"

#: How long the library may take to import (OCC-heavy libraries take a while on a cold start).
READY_TIMEOUT_S = 180.0
#: A build or reload that takes longer than this is taken as hung: the host is restarted.
REQUEST_TIMEOUT_S = 300.0
#: How often the watcher looks at the files, and how long they must stay unchanged before a reload.
POLL_INTERVAL_S = 0.4
SETTLE_S = 0.25

_WATCHED_SUFFIXES = {".py", ".toml"}
_SKIP_DIRS = {"__pycache__", ".git", ".pixi", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def configured_path() -> str | None:
    value = os.environ.get(ENV_CONFIG, "").strip()
    return value or None


class HostError(RuntimeError):
    """The library host answered a request with a failure."""

    def __init__(self, error: str, traceback: str | None = None, **extra: Any):
        super().__init__(error)
        self.error = error
        self.traceback = traceback
        self.extra = extra


class HostUnavailable(RuntimeError):
    """The library host is not running (still starting, or died and is restarting)."""


class LibraryHost:
    """One :mod:`ada.api.connections.devhost` child process and its JSON-line channel."""

    def __init__(self, config_path: str, python: str = sys.executable):
        self.config_path = config_path
        self.python = python
        self._proc: subprocess.Popen | None = None
        self._replies: queue_mod.Queue = queue_mod.Queue()
        self._next_id = 0

    def start(self, timeout: float = READY_TIMEOUT_S) -> dict[str, Any]:
        """Start the process and return its ``ready`` message."""
        self._replies = queue_mod.Queue()
        env = dict(os.environ)
        env.pop(ENV_CONFIG, None)
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._proc = subprocess.Popen(
            [self.python, "-m", "ada.api.connections.devhost", self.config_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        threading.Thread(target=self._read, args=(self._proc, self._replies), daemon=True).start()
        return self._wait(lambda m: m.get("event") == "ready", timeout)

    @staticmethod
    def _read(proc: subprocess.Popen, replies: queue_mod.Queue) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                replies.put(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("dev-connections: unreadable line from the library host: %s", line[:200])
        replies.put(None)  # EOF: the process is gone

    def _wait(self, match, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"the library host did not answer within {timeout:.0f}s")
            try:
                msg = self._replies.get(timeout=remaining)
            except queue_mod.Empty:
                continue
            if msg is None:
                code = self._proc.poll() if self._proc else None
                raise HostUnavailable(f"the library host exited (code {code})")
            if match(msg):
                return msg

    def request(self, op: str, timeout: float = REQUEST_TIMEOUT_S, **fields: Any) -> dict[str, Any]:
        if not self.alive:
            raise HostUnavailable("the library host is not running")
        self._next_id += 1
        req_id = self._next_id
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps({"id": req_id, "op": op, **fields}) + "\n")
            self._proc.stdin.flush()
        except OSError as e:
            raise HostUnavailable(f"the library host is gone: {e}") from e
        reply = self._wait(lambda m: m.get("id") == req_id, timeout)
        if not reply.get("ok"):
            raise HostError(
                reply.get("error") or "failed", reply.get("traceback"), declined=bool(reply.get("declined"))
            )
        return reply

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps({"id": 0, "op": "quit"}) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            proc.kill()
            proc.wait(timeout=5)


class DevConnections:
    """The library being developed: its host process, its file watcher, and what the studio sees."""

    def __init__(self, config_path: str, python: str = sys.executable):
        from ada.api.connections.library import load_config

        self.config = load_config(config_path)
        self._python = python
        self._host = LibraryHost(str(self.config.path), python=python)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._sub_lock = threading.Lock()
        self._last_keys: dict[str, str] = {}
        self._subjects: dict[str, list[str]] = {}
        self.status = "starting"
        self.generation = 0
        self.specs: list[dict[str, Any]] = []
        self.error: dict[str, Any] | None = None
        self.changed: list[str] = []
        self.updated_at = time.time()
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="ada-dev-connections-"))

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        """Start the host and the watcher in the background; the viewer is up meanwhile."""
        threading.Thread(target=self._start_host, name="dev-connections-host", daemon=True).start()
        threading.Thread(target=self._watch, name="dev-connections-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._host.stop()

    def _start_host(self, reason: str = "start") -> None:
        with self._lock:
            self._set(status="starting" if reason == "start" else "restarting")
            self._host.stop()
            try:
                ready = self._host.start()
            except Exception as e:  # noqa: BLE001
                self._set(status="error", error={"error": f"the library host failed to start: {e}", "traceback": None})
                return
            self._apply_load(ready, reason=reason)

    def restart(self) -> None:
        from ada.api.connections.library import LibraryConfigError, load_config

        try:
            self.config = load_config(self.config.path)
        except LibraryConfigError as e:
            self._set(status="error", error={"error": str(e), "traceback": None})
            return
        self._start_host(reason="restart")

    def reload(self, changed: list[str] | None = None) -> None:
        with self._lock:
            if not self._host.alive:
                self._start_host(reason="restart")
                return
            self._set(status="reloading", changed=changed or [])
            try:
                reply = self._host.request("reload")
            except (HostUnavailable, TimeoutError) as e:
                logger.warning("dev-connections: reload failed (%s); restarting the library host", e)
                self._start_host(reason="restart")
                return
            except HostError as e:
                self._set(status="error", error={"error": e.error, "traceback": e.traceback})
                return
            self._apply_load({**reply["load"], "specs": reply["specs"], "generation": reply["generation"]}, "reload")

    def _apply_load(self, msg: dict[str, Any], reason: str) -> None:
        self.generation += 1
        error = None if msg.get("ok") else {"error": msg.get("error"), "traceback": msg.get("traceback")}
        self._set(
            status="ready" if error is None else "error",
            specs=msg.get("specs") or [],
            error=error,
            reason=reason,
        )
        if error is None:
            logger.info("dev-connections: %s loaded (%d specs)", self.config.name, len(self.specs))
        else:
            logger.warning("dev-connections: %s failed to load: %s", self.config.name, error["error"])

    # -- building ----------------------------------------------------------------------------

    def build(self, spec_name: str, inputs: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        """Build ``spec_name`` and return the GLB bytes and the host's report. Raises
        :class:`HostError` for a builder failure, :class:`HostUnavailable` while (re)starting."""
        if self.status in ("starting", "restarting"):
            raise HostUnavailable(f"the library is {self.status}")
        glb = self._tmp / f"{hashlib.sha1(spec_name.encode()).hexdigest()[:12]}-{time.monotonic_ns()}.glb"
        with self._lock:
            if not self._host.alive:
                raise HostUnavailable(f"the library is {self.status}")
            try:
                reply = self._host.request("build", spec_name=spec_name, inputs=inputs, glb_path=str(glb))
            except TimeoutError:
                threading.Thread(target=self._start_host, args=("restart",), daemon=True).start()
                raise HostError(f"{spec_name}: the build did not finish in {REQUEST_TIMEOUT_S:.0f}s; restarting")
            except HostUnavailable:
                threading.Thread(target=self._start_host, args=("restart",), daemon=True).start()
                raise
        try:
            data = glb.read_bytes()
        finally:
            glb.unlink(missing_ok=True)
        return data, reply

    def subject(self, spec_name: str, inputs: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        """Build ``spec_name`` and return its weld assembly (the subject file's bytes) and the
        host's report. :class:`HostError` for a builder failure or a detail without welds (its
        message is "ValueError: the detail has no welds"), :class:`HostUnavailable` while (re)starting."""
        if self.status in ("starting", "restarting"):
            raise HostUnavailable(f"the library is {self.status}")
        path = (
            self._tmp / f"{hashlib.sha1(spec_name.encode()).hexdigest()[:12]}-{time.monotonic_ns()}.weld-assembly.json"
        )
        with self._lock:
            if not self._host.alive:
                raise HostUnavailable(f"the library is {self.status}")
            try:
                reply = self._host.request("subject", spec_name=spec_name, inputs=inputs, path=str(path))
            except TimeoutError:
                threading.Thread(target=self._start_host, args=("restart",), daemon=True).start()
                raise HostError(f"{spec_name}: the build did not finish in {REQUEST_TIMEOUT_S:.0f}s; restarting")
            except HostUnavailable:
                threading.Thread(target=self._start_host, args=("restart",), daemon=True).start()
                raise
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        return data, reply

    #: How many subjects per spec are kept in the scope: a run may still be using an older one.
    SUBJECTS_KEPT = 10

    def remember_subject(self, spec_name: str, key: str) -> list[str]:
        """Record the blob key of a subject offered for ``spec_name``; returns the keys beyond the
        newest :attr:`SUBJECTS_KEPT` (to delete). Offering a key again makes it the newest."""
        keys = self._subjects.setdefault(spec_name, [])
        if key in keys:
            keys.remove(key)
        keys.append(key)
        stale, self._subjects[spec_name] = keys[: -self.SUBJECTS_KEPT], keys[-self.SUBJECTS_KEPT :]
        return stale

    def remember_key(self, spec_name: str, key: str) -> str | None:
        """Record the blob key of ``spec_name``'s newest build; returns the one it replaces."""
        previous = self._last_keys.get(spec_name)
        self._last_keys[spec_name] = key
        return previous if previous != key else None

    # -- watching ----------------------------------------------------------------------------

    def _scan(self) -> dict[str, int]:
        seen: dict[str, int] = {}
        roots = list(self.config.watch)
        for root in roots:
            if root.is_file():
                files = [root]
            else:
                files = []
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                    files.extend(pathlib.Path(dirpath, f) for f in filenames)
            for f in files:
                if f.suffix not in _WATCHED_SUFFIXES:
                    continue
                try:
                    seen[str(f)] = f.stat().st_mtime_ns
                except OSError:
                    continue
        try:
            seen[str(self.config.path)] = self.config.path.stat().st_mtime_ns
        except OSError:
            pass
        return seen

    def _watch(self) -> None:
        last = self._scan()
        while not self._stop.wait(POLL_INTERVAL_S):
            current = self._scan()
            if current == last:
                continue
            # Let an editor finish writing (save-all, formatters) before reloading.
            while not self._stop.wait(SETTLE_S):
                again = self._scan()
                if again == current:
                    break
                current = again
            changed = sorted(k for k in set(current) | set(last) if current.get(k) != last.get(k))
            last = current
            root = self.config.root
            rel = [_relative(c, root) for c in changed]
            logger.info("dev-connections: changed %s", ", ".join(rel))
            try:
                if str(self.config.path) in changed:
                    self.changed = rel
                    self.restart()
                else:
                    self.reload(rel)
            except Exception:  # noqa: BLE001 - the watcher must outlive any one reload
                logger.exception("dev-connections: reload failed")

    # -- state and events --------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "library": {
                "name": self.config.name,
                "config": str(self.config.path),
                "root": str(self.config.root),
                "watch": [str(w) for w in self.config.watch],
            },
            "status": self.status,
            "generation": self.generation,
            "specs": self.specs,
            "error": self.error,
            "changed": self.changed,
            "updated_at": self.updated_at,
        }

    def _set(self, **fields: Any) -> None:
        reason = fields.pop("reason", None)
        for k, v in fields.items():
            setattr(self, k, v)
        self.updated_at = time.time()
        self._publish({"type": "state", "reason": reason, **self.snapshot()})

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        with self._sub_lock:
            self._subscribers.add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._sub_lock:
            self._subscribers = {s for s in self._subscribers if s[1] is not q}

    def _publish(self, event: dict[str, Any]) -> None:
        with self._sub_lock:
            subscribers = list(self._subscribers)
        for loop, q in subscribers:
            try:
                loop.call_soon_threadsafe(_offer, q, event)
            except RuntimeError:  # the loop closed
                self.unsubscribe(q)


def _offer(q: asyncio.Queue, event: dict[str, Any]) -> None:
    """Queue ``event``; a subscriber that fell behind loses its oldest state (the newest wins)."""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(event)


def _relative(path: str, root: pathlib.Path) -> str:
    try:
        return str(pathlib.Path(path).relative_to(root))
    except ValueError:
        return path

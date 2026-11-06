"""``ada desktop``: the local viewer in a native window (the ``ada-desktop`` shell).

1. Pick the port: ``--port``, else ``$ADA_DESKTOP_PORT``, else this user's fixed default (see
   :func:`default_port`). A fixed port keeps the page's origin, and so everything the viewer keeps in
   browser storage (layouts, settings), the same from one launch to the next.
2. If that port already serves a local viewer started by another ``ada desktop``, open one more window
   on it and leave its lifetime to that launch (it waits for this window before it stops). If something
   else holds the port, fall back to a free one and say what that costs.
3. Otherwise start the local viewer (``python -m ada.comms.rest``: local storage, no login, no queue)
   as a child of this process with a per-launch shutdown token, and wait until ``/api/config`` answers.
4. Optionally hand FILE to it (``PUT`` into the personal scope) and open the viewer on ``?file=``.
5. Start the shell on that URL. When the last window closes (and every attached ``ada desktop`` has
   ended), stop the viewer: ``POST /api/local/shutdown`` with the token, then terminate, then kill.
   On Windows a job object also ends the viewer and the shell if this process is killed outright.

``--server URL|NAME`` (or ``$ADA_DESKTOP_SERVER``) is remote mode instead: no local viewer is started; the shell
opens the window on a hosted deployment and trusts exactly that origin (plus its identity provider's sign-in
pages). ``ada desktop servers list|add|remove`` keeps named profiles in a small per-user file.

``--dev --backend URL|NAME`` (``--dev --server`` means the same) develops the frontend against a hosted
deployment: Vite serves the local sources with hot reload on ``http://localhost:PORT`` and proxies the page's
own-origin requests to the deployment; the redirect URI to register at its identity provider is printed.

Stdlib only, and nothing from ``ada`` is imported: ``ada --help`` builds this parser, and the viewer
runs in its own process anyway.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from typing import Callable, Mapping

#: The per-user default port is ``DEFAULT_PORT_BASE + crc32(user name) % DEFAULT_PORT_SPAN``. The range
#: sits below every OS's ephemeral range (Windows and macOS start at 49152, Linux at 32768 but rarely
#: hands out ports this far down in practice) and clear of the well-known dev-server ports, so it is
#: rarely taken. Per user rather than one constant so two people on one machine do not collide.
DEFAULT_PORT_BASE = 38500
DEFAULT_PORT_SPAN = 500
ENV_PORT = "ADA_DESKTOP_PORT"
ENV_BINARY = "ADA_DESKTOP_BINARY"
ENV_SERVER = "ADA_DESKTOP_SERVER"
SERVERS_FILE = "servers.json"
BINARY_NAME = "ada-desktop"
INSTALL_HINT = (
    "the ada-desktop window shell was not found. Install it next to ada-py with "
    "`conda install -c conda-forge ada-py-desktop` (Windows), build it from src/desktop "
    "(`pixi run -e desktop-shell build-desktop`) and pass --binary PATH or set $ADA_DESKTOP_BINARY, "
    "or run with --no-window and open the printed URL in a browser"
)

#: The scope a FILE is uploaded into; the local viewer's single user owns it.
FILE_SCOPE = "user:me"
#: ``ADA_VIEWER_BUNDLE`` values that mean the built-in viewer (mirrors ada.comms.viewer_bundles).
BUILTIN_BUNDLES = frozenset({"", "builtin", "core", "default"})

HEALTH_TIMEOUT_S = 90.0
STOP_GRACE_S = 8.0
PROBE_TIMEOUT_S = 1.5
LOCAL_HOST = "127.0.0.1"

# -- dev mode (``--dev``) -----------------------------------------------------
#: The Vite dev server listens on the viewer port plus this, so it is per-user and stable too.
DEV_VITE_PORT_OFFSET = 500
#: The Vite config that proxies /api, /config.js and /healthz to ``$ENV_DEV_BACKEND``.
DEV_VITE_CONFIG = "vite.config.desktop.ts"
ENV_DEV_BACKEND = "ADA_DESKTOP_BACKEND"
VITE_TIMEOUT_S = 240.0
#: ``--dev --backend`` opens the window on this host, not 127.0.0.1: the identity provider's registered
#: redirect URI for the app is ``http://localhost:PORT/auth/callback`` (providers accept localhost for http).
DEV_WINDOW_HOST = "localhost"
ENV_DEV_REMOTE = "ADA_DESKTOP_REMOTE"
ENV_DEV_ORIGIN = "ADA_DESKTOP_DEV_ORIGIN"


class DesktopError(Exception):
    """Something ``ada desktop`` could not do; printed as one line, exit 1."""


# ── where things live ─────────────────────────────────────────────────────


def _platform_dirs(environ: Mapping[str, str], platform: str) -> tuple[pathlib.Path, pathlib.Path]:
    """``(cache, data)`` roots for ada desktop on ``platform`` (a ``sys.platform`` value)."""
    home = pathlib.Path(environ.get("USERPROFILE") or environ.get("HOME") or os.path.expanduser("~"))
    if platform == "win32":
        base = pathlib.Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "ada" / "desktop"
        return base / "cache", base / "data"
    if platform == "darwin":
        return (
            home / "Library" / "Caches" / "ada" / "desktop",
            home / "Library" / "Application Support" / "ada" / "desktop",
        )
    cache = pathlib.Path(environ.get("XDG_CACHE_HOME") or home / ".cache")
    data = pathlib.Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return cache / "ada" / "desktop", data / "ada" / "desktop"


def cache_root(environ: Mapping[str, str] | None = None, platform: str | None = None) -> pathlib.Path:
    """Unpacked viewer builds and the attached-window registry. Safe to delete while nothing runs."""
    return _platform_dirs(os.environ if environ is None else environ, platform or sys.platform)[0]


def config_root(environ: Mapping[str, str] | None = None, platform: str | None = None) -> pathlib.Path:
    """Per-user settings (the server profiles): ``%APPDATA%/ada/desktop`` on Windows."""
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    home = pathlib.Path(environ.get("USERPROFILE") or environ.get("HOME") or os.path.expanduser("~"))
    if platform == "win32":
        return pathlib.Path(environ.get("APPDATA") or home / "AppData" / "Roaming") / "ada" / "desktop"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "ada" / "desktop"
    return pathlib.Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / "ada" / "desktop"


def data_root(environ: Mapping[str, str] | None = None, platform: str | None = None) -> pathlib.Path:
    """The local viewer's storage (uploaded files, derived artefacts) unless ``--data`` says otherwise."""
    return _platform_dirs(os.environ if environ is None else environ, platform or sys.platform)[1]


# ── the port ──────────────────────────────────────────────────────────────


def default_port(user: str | None = None) -> int:
    """This user's fixed port: stable across launches, different for another user on the machine."""
    if user is None:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not user:
            try:
                import getpass

                user = getpass.getuser()
            except Exception:  # noqa: BLE001 - no user name is not worth failing over
                user = ""
    return DEFAULT_PORT_BASE + zlib.crc32(user.lower().encode("utf-8")) % DEFAULT_PORT_SPAN


def requested_port(cli_port: int | None, environ: Mapping[str, str] | None = None) -> tuple[int, str]:
    """``(port, where it came from)``: ``--port``, then ``$ADA_DESKTOP_PORT``, then :func:`default_port`."""
    from ada_cli import CliUsageError

    environ = os.environ if environ is None else environ
    if cli_port is not None:
        port, source = cli_port, "--port"
    elif environ.get(ENV_PORT, "").strip():
        raw = environ[ENV_PORT].strip()
        try:
            port = int(raw)
        except ValueError:
            raise CliUsageError(f"${ENV_PORT} must be a port number, not {raw!r}") from None
        source = f"${ENV_PORT}"
    else:
        return default_port(), "default"
    if not 1 <= port <= 65535:
        raise CliUsageError(f"{source} must be between 1 and 65535, not {port}")
    return port, source


def is_own_viewer(config: object) -> bool:
    """Whether ``/api/config`` came from a local viewer an ``ada desktop`` (or any launcher) started:
    adapy's REST app, bound to loopback, offering this page the local shutdown route."""
    if not isinstance(config, dict):
        return False
    shutdown = config.get("localShutdown")
    return (
        config.get("transport") == "rest"
        and config.get("apiBase") == "/api"
        and isinstance(shutdown, dict)
        and shutdown.get("available") is True
    )


def _fetch_config(port: int, host: str = LOCAL_HOST, timeout: float = PROBE_TIMEOUT_S) -> object:
    with urllib.request.urlopen(f"http://{host}:{port}/api/config", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def probe_port(port: int, host: str = LOCAL_HOST) -> str:
    """``"free"``, ``"ours"`` (a local viewer answers, see :func:`is_own_viewer`) or ``"taken"``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(PROBE_TIMEOUT_S)
        listening = s.connect_ex((host, port)) == 0
    if listening:
        try:
            return "ours" if is_own_viewer(_fetch_config(port, host)) else "taken"
        except (urllib.error.URLError, OSError, ValueError):
            return "taken"
    # Nothing listens, but the port may still be unusable (a Windows excluded port range, a socket
    # bound without listening): only a bind tells.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((host, port))
        except OSError:
            return "taken"
    return "free"


def free_port(host: str = LOCAL_HOST) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@dataclasses.dataclass(frozen=True)
class PortChoice:
    port: int
    #: A local viewer already serves ``port``: open a window on it, start nothing.
    attach: bool = False
    #: The port that was asked for and was taken by something else, when ``port`` is a fallback.
    fallback_from: int | None = None


def choose_port(
    requested: int,
    probe: Callable[[int], str] = probe_port,
    free: Callable[[], int] = free_port,
) -> PortChoice:
    state = probe(requested)
    if state == "free":
        return PortChoice(requested)
    if state == "ours":
        return PortChoice(requested, attach=True)
    return PortChoice(free(), fallback_from=requested)


def fallback_warning(choice: PortChoice, source: str) -> str:
    return (
        f"ada desktop: warning: port {choice.fallback_from} ({source}) is in use by another program, "
        f"so this launch uses port {choice.port}. The viewer keeps layouts and settings in browser "
        f"storage, which belongs to one port: this window starts without them, and what you change "
        f"here is not there next time. Free port {choice.fallback_from} or pick another with --port / "
        f"${ENV_PORT} to keep them."
    )


# ── the shell binary ──────────────────────────────────────────────────────


def binary_candidates(
    explicit: str | None,
    environ: Mapping[str, str] | None = None,
    prefix: str | os.PathLike | None = None,
    which: Callable[[str], str | None] = shutil.which,
    windows: bool | None = None,
) -> list[pathlib.Path]:
    """Where the shell is looked for, in order: ``--binary``, ``$ADA_DESKTOP_BINARY``, PATH, then the
    environment ``ada`` runs from (``Library/bin`` on Windows conda, ``bin`` elsewhere)."""
    environ = os.environ if environ is None else environ
    windows = (os.name == "nt") if windows is None else windows
    root = pathlib.Path(sys.prefix if prefix is None else prefix)
    exe = BINARY_NAME + (".exe" if windows else "")
    found: list[pathlib.Path] = []
    for c in (explicit, environ.get(ENV_BINARY) or None, which(BINARY_NAME)):
        if c:
            found.append(pathlib.Path(c))
    if windows:
        found += [root / "Library" / "bin" / exe, root / "Scripts" / exe, root / exe]
    found.append(root / "bin" / BINARY_NAME)
    return found


def find_binary(explicit: str | None, **kw) -> pathlib.Path:
    """The shell to start; a ``--binary`` that does not exist is an error of its own."""
    from ada_cli import CliUsageError

    if explicit and not pathlib.Path(explicit).is_file():
        raise CliUsageError(f"--binary {explicit}: no such file")
    for c in binary_candidates(explicit, **kw):
        if c.is_file():
            return c
    raise DesktopError(INSTALL_HINT)


# ── the viewer build it serves ────────────────────────────────────────────


def bundled_zip() -> pathlib.Path | None:
    """The single-file viewer shipped in ada-py, found without importing ``ada`` (which is slow)."""
    import importlib.util

    spec = importlib.util.find_spec("ada")
    if spec is None or not spec.submodule_search_locations:
        return None
    z = pathlib.Path(next(iter(spec.submodule_search_locations))) / "visit" / "rendering" / "resources" / "index.zip"
    return z if z.is_file() else None


def zip_key(zip_path: pathlib.Path) -> str:
    """Content hash of the zip: a new viewer build gets a new directory, the same one is reused."""
    h = hashlib.sha256()
    with zip_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def static_dir(zip_path: pathlib.Path, root: pathlib.Path | None = None) -> pathlib.Path:
    """Unpack the single-file viewer once per zip content (``<cache>/spa/<hash>``); the REST app then
    serves the directory as ``ADA_VIEWER_STATIC_PATH``. Old directories are left alone: a viewer still
    running from one would lose its files."""
    out = (cache_root() if root is None else root) / "spa" / zip_key(zip_path)
    if (out / "index.html").is_file():
        return out
    tmp = out.with_name(f"{out.name}.tmp-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)  # zipfile drops absolute paths and `..` parts
    if not (tmp / "index.html").is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise DesktopError(f"{zip_path} has no index.html at its top level; is it a viewer build?")
    try:
        tmp.rename(out)
    except OSError:
        # Another launch unpacked the same zip first: use theirs.
        shutil.rmtree(tmp, ignore_errors=True)
        if not (out / "index.html").is_file():
            raise
    return out


def _uses_installed_bundle(environ: Mapping[str, str]) -> bool:
    return environ.get("ADA_VIEWER_BUNDLE", "").strip().lower() not in BUILTIN_BUNDLES


def resolve_static(args: argparse.Namespace, environ: Mapping[str, str]) -> pathlib.Path | None:
    """The directory to serve, or ``None`` when the REST app resolves it itself (an explicit
    ``$ADA_VIEWER_STATIC_PATH``, or an installed bundle picked with ``--viewer``)."""
    from ada_cli import CliUsageError

    if args.static:
        static = pathlib.Path(args.static)
        if not (static / "index.html").is_file():
            raise CliUsageError(f"--static {static}: no index.html there")
        return static
    if args.zip:
        z = pathlib.Path(args.zip)
        if not z.is_file():
            raise CliUsageError(f"--zip {z}: no such file")
        return static_dir(z)
    if environ.get("ADA_VIEWER_STATIC_PATH") or _uses_installed_bundle(environ):
        return None
    z = bundled_zip()
    if z is None:
        raise DesktopError(
            "no bundled viewer (ada/visit/rendering/resources/index.zip) found; pass --static DIR or --zip FILE"
        )
    return static_dir(z)


# ── a file to open ────────────────────────────────────────────────────────


def viewer_url(base: str, file_key: str | None = None) -> str:
    """The URL the window opens: the viewer, or the viewer loading ``file_key`` from the personal scope."""
    if not file_key:
        return base
    return base + "?" + urllib.parse.urlencode({"scope": FILE_SCOPE, "file": file_key})


def upload_file(base: str, path: pathlib.Path) -> str:
    """Put ``path`` into the local viewer's personal scope under its file name; returns the key.

    The viewer then loads it exactly as one uploaded from the page. A file the viewer has no reader for
    is refused (415); conversions that need a worker are not available in the local viewer.
    """
    key = path.name
    url = f"{base}api/scopes/{FILE_SCOPE}/blobs/{urllib.parse.quote(key)}"
    size = path.stat().st_size
    with path.open("rb") as fh:
        req = urllib.request.Request(url, data=fh, method="PUT", headers={"Content-Length": str(size)})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise DesktopError(f"the viewer refused {path.name}: {e.code} {detail}") from None
    return key


# ── windows attached to a viewer another launch owns ─────────────────────


def _clients_dir(port: int) -> pathlib.Path:
    return cache_root() / "clients" / str(port)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def live_clients(port: int, alive: Callable[[int], bool] = _pid_alive) -> list[int]:
    """The attached ``ada desktop`` processes still running on ``port``; stale entries are removed."""
    d = _clients_dir(port)
    pids = []
    for f in d.glob("*") if d.is_dir() else ():
        try:
            pid = int(f.name)
        except ValueError:
            continue
        if alive(pid):
            pids.append(pid)
        else:
            f.unlink(missing_ok=True)
    return pids


# ── processes ─────────────────────────────────────────────────────────────


class _Job:
    """A Windows job object that kills its processes when this launcher's handle closes, on any exit
    including a hard kill. A no-op elsewhere (there the children see the parent die on their own:
    the viewer's stdin/console goes, and the shell has nothing to show)."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RB", "WB", "OB")]

        class _Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _Basic),
                ("IoInfo", _Io),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation
        if k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle, self._k32 = job, k32

    def assign(self, proc: subprocess.Popen) -> None:
        if self.handle is not None:
            self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))  # noqa: SLF001


def wait_healthy(base: str, server: subprocess.Popen, timeout: float = HEALTH_TIMEOUT_S) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if server.poll() is not None:
            raise DesktopError(f"the viewer exited during startup (code {server.returncode}); see its log above")
        try:
            with urllib.request.urlopen(base + "api/config", timeout=1.0) as r:
                if r.status == 200:
                    return time.monotonic() - t0
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.05)
    raise DesktopError(f"the viewer did not answer {base}api/config within {timeout:.0f}s")


def stop_server(server: subprocess.Popen, base: str, token: str) -> str:
    """Stop the viewer, gently first; returns how it ended."""
    if server.poll() is not None:
        return "already-exited"
    try:
        req = urllib.request.Request(
            base + "api/local/shutdown", method="POST", headers={"X-Ada-Local-Shutdown": token}
        )
        urllib.request.urlopen(req, timeout=3).read()
        server.wait(timeout=STOP_GRACE_S)
        return "shutdown-route"
    except Exception:  # noqa: BLE001 - every failure here has the same answer: escalate
        pass
    server.terminate()
    try:
        server.wait(timeout=3)
        return "terminate"
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=3)
        return "kill"


# ── dev mode ──────────────────────────────────────────────────────────────


def dev_vite_port(backend_port: int) -> int:
    return backend_port + DEV_VITE_PORT_OFFSET


def checkout_src() -> pathlib.Path:
    """``src`` of the source checkout this module runs from (dev mode needs the frontend sources)."""
    return pathlib.Path(__file__).resolve().parents[1]


def frontend_url_or_error(url: str) -> str:
    """``--frontend-url`` normalised to ``http://HOST:PORT/``; the shell only shows loopback http."""
    from ada_cli import CliUsageError

    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or u.hostname not in (LOCAL_HOST, "localhost", "::1") or not u.netloc:
        raise CliUsageError(f"--frontend-url {url}: must be http on 127.0.0.1, localhost or [::1]")
    return f"http://{u.netloc}/"


def vite_command(frontend: pathlib.Path, port: int, node: str | None, pixi: str | None) -> list[str]:
    """Run Vite's own entry with node: from PATH, else through the ``frontend`` pixi environment."""
    vite_js = str(frontend / "node_modules" / "vite" / "bin" / "vite.js")
    tail = [vite_js, "--config", DEV_VITE_CONFIG, "--host", LOCAL_HOST, "--port", str(port), "--strictPort"]
    if node:
        return [node, *tail]
    if pixi:
        return [pixi, "run", "-e", "frontend", "node", *tail]
    raise DesktopError("--dev needs node (not on PATH); run it as `pixi run desktop`, or pass --frontend-url")


def backend_dev_command(python: str, port: int, reload_dir: pathlib.Path) -> list[str]:
    """The viewer under uvicorn's reloader (``python -m ada.comms.rest`` has no reload mode: it
    builds its own Server so the shutdown route can stop it)."""
    return [
        python,
        "-m",
        "uvicorn",
        "ada.comms.rest.app:app",
        "--host",
        LOCAL_HOST,
        "--port",
        str(port),
        "--reload",
        "--reload-dir",
        str(reload_dir),
    ]


def dev_shell_binary(src: pathlib.Path) -> pathlib.Path | None:
    """The shell as ``build-desktop`` leaves it in the checkout."""
    exe = src / "desktop" / "target" / "release" / (BINARY_NAME + (".exe" if os.name == "nt" else ""))
    return exe if exe.is_file() else None


def kill_tree(proc: subprocess.Popen) -> None:
    """End ``proc`` and everything it started (uvicorn's reloader and Vite's launcher both fork)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False, timeout=15)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_http(url: str, proc: subprocess.Popen, what: str, timeout: float) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc.poll() is not None:
            raise DesktopError(f"{what} exited during startup (code {proc.returncode}); see its log above")
        try:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                if r.status == 200:
                    return time.monotonic() - t0
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    raise DesktopError(f"{what} did not answer {url} within {timeout:.0f}s")


def run_dev(args: argparse.Namespace) -> int:
    """``--dev``: uvicorn --reload for the viewer, Vite (HMR) for the page, the shell on Vite."""
    if sys.platform != "win32":
        raise DesktopError("--dev (and `pixi run desktop`) is Windows only for now")
    src = checkout_src()
    frontend = src / "frontend"
    if not (frontend / DEV_VITE_CONFIG).is_file():
        raise DesktopError(f"--dev needs the source checkout: {frontend / DEV_VITE_CONFIG} not found")
    file_path = _open_file_arg(args)
    binary = None if args.no_window else find_binary(args.binary or (str(dev_shell_binary(src) or "") or None))
    external = frontend_url_or_error(args.frontend_url) if args.frontend_url else None

    port, source = requested_port(args.port)
    choice = choose_port(port)
    if choice.attach:
        raise DesktopError(f"a viewer is already running on port {choice.port}; close it, or pass --port N")
    if choice.fallback_from is not None:
        print(fallback_warning(choice, source), file=sys.stderr, flush=True)
    base = f"http://{LOCAL_HOST}:{choice.port}/"
    data = pathlib.Path(args.data) if args.data else data_root()
    data.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        ADA_VIEWER_STORAGE_KIND="local",
        ADA_VIEWER_LOCAL_PATH=str(data),
        ADA_VIEWER_HOST=LOCAL_HOST,
        ADA_VIEWER_PORT=str(choice.port),
        ADA_VIEWER_AUTH_ENABLED="false",
        PYTHONPATH=os.pathsep.join([str(src)] + ([env_pp] if (env_pp := os.environ.get("PYTHONPATH")) else [])),
    )
    env.pop("ADA_VIEWER_NATS_URL", None)

    job = _Job()
    procs: list[subprocess.Popen] = []
    rc = 0
    try:
        server = subprocess.Popen(backend_dev_command(sys.executable, choice.port, src / "ada"), env=env, cwd=src)
        procs.append(server)
        job.assign(server)
        wait_healthy(base, server)
        if external:
            front = external
        else:
            vport = args.dev_port or dev_vite_port(choice.port)
            if probe_port(vport) != "free":
                if args.dev_port:
                    raise DesktopError(f"--dev-port {vport} is in use")
                vport = free_port()
            front = f"http://{LOCAL_HOST}:{vport}/"
            cmd = vite_command(frontend, vport, shutil.which("node"), os.environ.get("PIXI_EXE"))
            _print(f"ada desktop: starting Vite on {front} (first start optimizes dependencies; can take a minute)")
            vite = subprocess.Popen(cmd, cwd=frontend, env={**os.environ, ENV_DEV_BACKEND: base})
            procs.append(vite)
            job.assign(vite)
            wait_http(front, vite, "Vite", VITE_TIMEOUT_S)
        url = viewer_url(front, _key_or_warn(base, file_path))
        _print(f"viewer (dev): {url}  backend: {base}  (Python edits reload the viewer; frontend edits hot-reload)")
        if binary is None:
            _print("ada desktop: --no-window: press Ctrl+C to stop")
            procs[-1].wait()
        else:
            rc = _run_shell(binary, url, args, job)
    except KeyboardInterrupt:
        rc = 130
    finally:
        for p in reversed(procs):
            kill_tree(p)
    return rc


# ── remote mode: a hosted deployment ───────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Server:
    """A hosted deployment to open: its origin, whether plain http was explicitly allowed, and extra
    identity-provider origins (beyond what the server advertises)."""

    url: str
    insecure: bool = False
    auth_origins: tuple[str, ...] = ()
    name: str | None = None


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_loopback_host(host: str | None) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def normalize_origin(url: str, insecure: bool = False, what: str = "server URL") -> str:
    """``scheme://host[:port]/`` of ``url``. https is required, except for loopback hosts and when
    ``insecure`` says plain http is meant (a lab setup)."""
    from ada_cli import CliUsageError

    u = urllib.parse.urlsplit(url.strip())
    try:
        port = u.port
    except ValueError:
        raise CliUsageError(f"{what} {url!r}: bad port") from None
    if u.scheme not in ("http", "https") or not u.hostname:
        raise CliUsageError(f"{what} {url!r}: must be an http(s) URL with a host, e.g. https://studio.example.com")
    if u.username or u.password:
        raise CliUsageError(f"{what} {url!r}: must not contain credentials")
    if u.scheme == "http" and not insecure and not _is_loopback_host(u.hostname):
        raise CliUsageError(
            f"{what} {url!r}: plain http is only accepted for loopback hosts. Use https, or pass --insecure "
            "if this really is an http-only lab server (sign-in tokens then cross the network unencrypted)."
        )
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"{u.scheme}://{host}{':' + str(port) if port else ''}/"


def servers_path(environ: Mapping[str, str] | None = None, platform: str | None = None) -> pathlib.Path:
    return config_root(environ, platform) / SERVERS_FILE


def load_servers(path: pathlib.Path | None = None) -> dict[str, Server]:
    """The saved profiles (``{"servers": {NAME: {"url", "insecure"?, "auth_origins"?}}}``); none if the
    file is missing. A damaged file is an error rather than silently ignored: ``add`` would overwrite it."""
    path = servers_path() if path is None else path
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = raw["servers"]
        out = {}
        for name, e in entries.items():
            out[name] = Server(
                url=str(e["url"]),
                insecure=bool(e.get("insecure", False)),
                auth_origins=tuple(str(a) for a in e.get("auth_origins", ())),
                name=name,
            )
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise DesktopError(f"{path}: not a valid server profile file ({e}); fix or delete it") from None


def save_servers(servers: Mapping[str, Server], path: pathlib.Path | None = None) -> pathlib.Path:
    path = servers_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {}
    for name in sorted(servers):
        s = servers[name]
        entry: dict = {"url": s.url}
        if s.insecure:
            entry["insecure"] = True
        if s.auth_origins:
            entry["auth_origins"] = list(s.auth_origins)
        body[name] = entry
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"servers": body}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def make_profile(name: str, url: str, insecure: bool = False, auth_origins: list[str] | tuple[str, ...] = ()) -> Server:
    from ada_cli import CliUsageError

    if not _PROFILE_NAME.match(name):
        raise CliUsageError(f"server name {name!r}: use letters, digits, '.', '_' or '-' (no URL here)")
    return Server(
        url=normalize_origin(url, insecure),
        insecure=insecure,
        auth_origins=tuple(normalize_origin(a, insecure, "--auth-origin") for a in auth_origins),
        name=name,
    )


def resolve_server(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
    profiles: Mapping[str, Server] | None = None,
    option: str = "server",
) -> Server | None:
    """The deployment to open, or ``None`` for the local viewer: ``--local`` wins, then ``--server``, then
    ``$ADA_DESKTOP_SERVER``. The value is a URL (anything with ``://``) or the name of a saved profile."""
    from ada_cli import CliUsageError

    environ = os.environ if environ is None else environ
    if getattr(args, "local", False):
        if getattr(args, option, None):
            raise CliUsageError(f"--local and --{option} exclude each other")
        return None
    value, source = getattr(args, option, None), f"--{option}"
    if not value and option == "server" and environ.get(ENV_SERVER, "").strip():
        value, source = environ[ENV_SERVER].strip(), f"${ENV_SERVER}"
    if not value:
        return None
    insecure = bool(getattr(args, "insecure", False))
    extra = tuple(normalize_origin(a, insecure, "--auth-origin") for a in getattr(args, "auth_origin", None) or ())
    if "://" in value:
        return Server(url=normalize_origin(value, insecure, source), insecure=insecure, auth_origins=extra)
    known = load_servers() if profiles is None else dict(profiles)
    if value not in known:
        have = ", ".join(sorted(known)) or "none saved"
        raise CliUsageError(
            f"{source} {value!r}: not a URL and no saved server of that name ({have}). "
            "Add one with `ada desktop servers add NAME URL`."
        )
    p = known[value]
    return dataclasses.replace(
        p,
        insecure=p.insecure or insecure,
        auth_origins=tuple(dict.fromkeys(p.auth_origins + extra)),
    )


def _get_json(url: str, timeout: float = 6.0) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _origin_of(url: str) -> str | None:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    return f"{u.scheme}://{u.netloc}/"


def auth_origins_from(config: object, discovery: object = None) -> list[str]:
    """The identity provider's origins a sign-in redirect visits: the issuer from ``/api/config``
    (``auth.issuer``, only when ``auth.enabled``) and the hosts of the endpoints its discovery document
    names (a provider may sign in on one host and issue tokens on another)."""
    if not isinstance(config, dict):
        return []
    auth = config.get("auth")
    if not isinstance(auth, dict) or not auth.get("enabled") or not isinstance(auth.get("issuer"), str):
        return []
    found: list[str] = []
    candidates = [auth["issuer"]]
    if isinstance(discovery, dict):
        candidates += [discovery.get(k) for k in ("authorization_endpoint", "end_session_endpoint")]
    for c in candidates:
        o = _origin_of(c) if isinstance(c, str) else None
        if o and o not in found:
            found.append(o)
    return found


def discover_auth_origins(
    origin: str, get: Callable[[str], object] = _get_json, warn: Callable[[str], None] | None = None
) -> list[str]:
    """Ask the server who signs its users in (``GET /api/config``, public by design). Best effort: a
    server that cannot be asked yields nothing (``--auth-origin`` can name the provider by hand)."""
    warn = warn or (lambda m: print(f"ada desktop: warning: {m}", file=sys.stderr, flush=True))
    try:
        config = get(origin + "api/config")
    except (urllib.error.URLError, OSError, ValueError) as e:
        warn(
            f"could not read {origin}api/config ({e}); "
            "if the server uses sign-in, pass the identity provider with --auth-origin"
        )
        return []
    discovery = None
    auth = config.get("auth") if isinstance(config, dict) else None
    if isinstance(auth, dict) and auth.get("enabled") and isinstance(auth.get("issuer"), str) and auth["issuer"]:
        try:
            discovery = get(auth["issuer"].rstrip("/") + "/.well-known/openid-configuration")
        except (urllib.error.URLError, OSError, ValueError):
            discovery = None  # the issuer's own origin is still allowed
    return auth_origins_from(config, discovery)


def shell_command_remote(
    binary: pathlib.Path, server: Server, auth_origins: list[str], title: str, shell_args: list[str]
) -> list[str]:
    cmd = [str(binary), "--url", server.url, "--trust-origin", server.url, "--title", title]
    for a in auth_origins:
        cmd += ["--auth-origin", a]
    if server.insecure:
        cmd.append("--insecure")
    return cmd + list(shell_args)


#: Flags that only mean something for the local viewer; with a remote server they would be ignored silently.
LOCAL_ONLY_FLAGS = ("port", "data", "static", "zip", "viewer", "dev", "frontend_url", "dev_port")


def refuse_local_only(args: argparse.Namespace, flags: tuple[str, ...]) -> None:
    from ada_cli import CliUsageError

    clash = [f"--{f.replace('_', '-')}" for f in flags if getattr(args, f, None)]
    if clash:
        verb = "applies" if len(clash) == 1 else "apply"
        raise CliUsageError(f"{', '.join(clash)} only {verb} to the local viewer, not with a server (use --local)")


#: Microsoft's login hosts (global and national clouds); the shell keeps the same list (origin.rs).
ENTRA_LOGIN_HOSTS = frozenset(
    {
        "login.microsoftonline.com",
        "login.microsoft.com",
        "login.windows.net",
        "sts.windows.net",
        "login.microsoftonline.us",
        "login.chinacloudapi.cn",
        "login.partner.microsoftonline.cn",
    }
)


def is_entra(auth_origins: list[str]) -> bool:
    """Whether the identity provider is Microsoft Entra ID (the shell then adds its built-in sign-in hosts)."""
    for o in auth_origins:
        u = urllib.parse.urlsplit(o)
        if u.scheme == "https" and u.hostname in ENTRA_LOGIN_HOSTS:
            return True
    return False


def shell_args(args: argparse.Namespace) -> list[str]:
    """What is passed through to the shell: ``--shell-arg``s, plus ``--no-sso``."""
    extra = list(getattr(args, "shell_arg", None) or [])
    if getattr(args, "no_sso", False) and "--no-sso" not in extra:
        extra.append("--no-sso")
    return extra


def print_sign_in(auth: list[str], args: argparse.Namespace) -> None:
    """Say which sign-in pages the window may show and whether Windows single sign-on is on."""
    if not auth:
        return
    _print("ada desktop: sign-in pages allowed (navigation only): " + ", ".join(a[:-1] for a in auth))
    if is_entra(auth):
        sso = "off (--no-sso)" if getattr(args, "no_sso", False) else "on (--no-sso turns it off)"
        _print(
            "ada desktop: Microsoft Entra ID: its other sign-in hosts are allowed too, and any https page the "
            f"sign-in passes through; single sign-on with the Windows account: {sso}"
        )


def collect_auth_origins(server: Server, discover: Callable[[str], list[str]] | None = None) -> list[str]:
    """The identity-provider origins the window may navigate to: what the server advertises plus the
    profile's own, minus plain-http ones unless the server is ``insecure`` (or the host is loopback)."""
    found = (discover or discover_auth_origins)(server.url)
    auth: list[str] = []
    for o in [*found, *server.auth_origins]:
        if o in auth or o == server.url:
            continue
        u = urllib.parse.urlsplit(o)
        if u.scheme == "http" and not server.insecure and not _is_loopback_host(u.hostname):
            print(f"ada desktop: warning: not allowing sign-in at {o} (plain http); pass --insecure", file=sys.stderr)
            continue
        auth.append(o)
    return auth


def dev_window_url(port: int) -> str:
    """Where ``--dev --backend`` opens the window (and where the identity provider sends users back)."""
    return f"http://{DEV_WINDOW_HOST}:{port}/"


def redirect_uri_for(front: str) -> str:
    """The SPA redirect URI the page will use on ``front`` (the page sends ``origin + /auth/callback``)."""
    return front.rstrip("/") + "/auth/callback"


def shell_command_dev_backend(
    binary: pathlib.Path, front: str, server: Server, auth_origins: list[str], title: str, shell_args: list[str]
) -> list[str]:
    """The shell on the dev frontend: it trusts (and grants native events to) exactly that loopback origin;
    the hosted server is only reached through the dev server's proxy, so it needs no trust of its own."""
    cmd = [str(binary), "--url", front, "--trust-origin", front, "--title", title]
    for a in auth_origins:
        cmd += ["--auth-origin", a]
    if server.insecure:
        cmd.append("--insecure")
    return cmd + list(shell_args)


def run_dev_backend(args: argparse.Namespace, server: Server) -> int:
    """``--dev --backend``: Vite (HMR) on localhost proxying to a hosted deployment; no local viewer."""
    from ada_cli import CliUsageError

    refuse_local_only(args, ("data", "static", "zip", "viewer"))
    if _files(args):
        raise CliUsageError("FILE is not uploaded to a server from the command line; open it in the app (File > Open)")
    src = checkout_src()
    frontend = src / "frontend"
    if not (frontend / DEV_VITE_CONFIG).is_file():
        raise DesktopError(f"--dev needs the source checkout: {frontend / DEV_VITE_CONFIG} not found")
    binary = None if args.no_window else find_binary(args.binary or (str(dev_shell_binary(src) or "") or None))
    external = frontend_url_or_error(args.frontend_url) if args.frontend_url else None
    vport = 0
    if external:
        front = external
    else:
        vport = args.dev_port or dev_vite_port(requested_port(args.port)[0])
        if probe_port(vport) != "free":
            # Not a fallback port: the redirect URI registered at the identity provider names this one.
            raise DesktopError(
                f"port {vport} is in use (the dev port is fixed because the sign-in redirect URI names it); "
                "close what uses it, or pass --dev-port N and register that redirect URI instead"
            )
        front = dev_window_url(vport)
    label = f"{server.name} " if server.name else ""
    _print(f"ada desktop: dev frontend against server {label}{server.url}")
    _print(f"ada desktop: trusting only the origin {front[:-1]} (native events and the app itself)")
    auth = collect_auth_origins(server)
    print_sign_in(auth, args)
    _print(
        f"Register this redirect URI for the app's SPA platform: {redirect_uri_for(front)} "
        "(until it is, Microsoft Entra ID answers AADSTS50011)"
    )

    job = _Job()
    procs: list[subprocess.Popen] = []
    rc = 0
    try:
        if not external:
            cmd = vite_command(frontend, vport, shutil.which("node"), os.environ.get("PIXI_EXE"))
            _print(f"ada desktop: starting Vite on {front} (first start optimizes dependencies; can take a minute)")
            env = {**os.environ, ENV_DEV_BACKEND: server.url, ENV_DEV_REMOTE: "1", ENV_DEV_ORIGIN: front[:-1]}
            vite = subprocess.Popen(cmd, cwd=frontend, env=env)
            procs.append(vite)
            job.assign(vite)
            wait_http(front, vite, "Vite", VITE_TIMEOUT_S)
        _print(f"viewer (dev): {front}  backend: {server.url}  (frontend edits hot-reload)")
        if binary is None:
            _print("ada desktop: --no-window: press Ctrl+C to stop")
            while True:
                if procs and procs[-1].poll() is not None:
                    break
                time.sleep(0.5)
        else:
            t0 = time.monotonic()
            shell = subprocess.Popen(
                shell_command_dev_backend(binary, front, server, auth, args.title, shell_args(args))
            )
            job.assign(shell)
            rc = shell.wait()
            if args.timing:
                _print(f"[timing] window open {time.monotonic() - t0:.1f}s, shell exit {rc}")
    except KeyboardInterrupt:
        rc = 130
    finally:
        for p in reversed(procs):
            kill_tree(p)
    return rc


def run_remote(args: argparse.Namespace, server: Server) -> int:
    """Open the window on a hosted deployment. No viewer is started and nothing is uploaded: the page
    runs on the server's own origin, so its sign-in redirect URI and API need no change."""
    from ada_cli import CliUsageError

    refuse_local_only(args, LOCAL_ONLY_FLAGS)
    if _files(args):
        raise CliUsageError("FILE is not uploaded to a server from the command line; open it in the app (File > Open)")
    binary = None if args.no_window else find_binary(args.binary)
    label = f"{server.name} " if server.name else ""
    _print(f"ada desktop: server {label}{server.url}")
    _print(f"ada desktop: trusting only the origin {server.url[:-1]} (native events and the app itself)")
    auth = collect_auth_origins(server)
    print_sign_in(auth, args)
    if binary is None:
        _print(f"viewer: {server.url}")
        return 0
    job = _Job()
    t0 = time.monotonic()
    shell = subprocess.Popen(shell_command_remote(binary, server, auth, args.title, shell_args(args)))
    job.assign(shell)
    rc = shell.wait()
    if args.timing:
        _print(f"[timing] window open {time.monotonic() - t0:.1f}s, shell exit {rc}")
    return rc


def run_servers(words: list[str], args: argparse.Namespace) -> int:
    """``ada desktop servers list|add NAME URL|remove NAME``."""
    from ada_cli import CliUsageError

    action = words[0] if words else "list"
    path = servers_path()
    if action == "list" and len(words) <= 1:
        servers = load_servers(path)
        if not servers:
            _print(f"no saved servers ({path}); add one with `ada desktop servers add NAME URL`")
        for name, s in sorted(servers.items()):
            extra = (" insecure" if s.insecure else "") + "".join(f" auth={a[:-1]}" for a in s.auth_origins)
            _print(f"{name}\t{s.url}{extra}")
        return 0
    if action == "add" and len(words) == 3:
        profile = make_profile(words[1], words[2], bool(args.insecure), args.auth_origin or ())
        servers = load_servers(path)
        replaced = words[1] in servers
        servers[words[1]] = profile
        save_servers(servers, path)
        _print(f"{'updated' if replaced else 'added'} server {words[1]}: {profile.url} ({path})")
        return 0
    if action in ("remove", "rm") and len(words) == 2:
        servers = load_servers(path)
        if words[1] not in servers:
            raise CliUsageError(f"no saved server {words[1]!r}")
        del servers[words[1]]
        save_servers(servers, path)
        _print(f"removed server {words[1]}")
        return 0
    raise CliUsageError("usage: ada desktop servers list | add NAME URL [--insecure] [--auth-origin O] | remove NAME")


# ── the command ───────────────────────────────────────────────────────────


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "words",
        nargs="*",
        metavar="FILE",
        help="A model to open: uploaded into the local viewer's personal scope and loaded (?file=). "
        "Or `servers list|add NAME URL|remove NAME` to manage saved servers (see --server).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help=f"Viewer port. Default: ${ENV_PORT}, else a fixed per-user port "
        f"({DEFAULT_PORT_BASE}-{DEFAULT_PORT_BASE + DEFAULT_PORT_SPAN - 1}; this user: {default_port()}). "
        "Browser-stored layouts and settings belong to the port.",
    )
    p.add_argument(
        "--data",
        default=None,
        metavar="DIR",
        help=f"Local storage for uploaded files and results. Default: {data_root()}.",
    )
    p.add_argument(
        "--binary",
        default=None,
        metavar="PATH",
        help=f"The ada-desktop shell. Default: ${ENV_BINARY}, else ada-desktop on PATH or in this environment.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--static", default=None, metavar="DIR", help="Serve this built viewer directory.")
    src.add_argument(
        "--zip",
        default=None,
        metavar="FILE",
        help="Serve this single-file viewer zip (default: the one shipped in ada-py, unpacked once into the cache).",
    )
    p.add_argument("--title", default="Ada Studio", help="Main window title (default: Ada Studio).")
    p.add_argument(
        "--no-window",
        action="store_true",
        help="Start the viewer only, print its URL, and wait for Ctrl+C (no shell needed).",
    )
    p.add_argument(
        "--shell-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="Pass ARG to the shell, e.g. --shell-arg=--no-spacemouse; repeatable.",
    )
    p.add_argument(
        "--server",
        default=None,
        metavar="URL|NAME",
        help=f"Open a hosted deployment instead of starting the local viewer: its https URL, or the name of a server "
        f"saved with `servers add`. Default: ${ENV_SERVER}. Sign-in, storage and jobs are then the server's.",
    )
    p.add_argument("--local", action="store_true", help=f"Start the local viewer even if ${ENV_SERVER} is set.")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="With --server: accept plain http for a non-loopback host (lab servers only; tokens are not encrypted).",
    )
    p.add_argument(
        "--auth-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="With --server: an identity provider origin the window may sign in at, in addition to the one the "
        "server advertises (/api/config). Repeatable.",
    )
    p.add_argument(
        "--no-sso",
        action="store_true",
        help="Do not sign in with the Windows account (WebView2 single sign-on with the OS primary account, "
        "on by default): the identity provider then asks for the account itself.",
    )
    p.add_argument(
        "--backend",
        default=None,
        metavar="URL|NAME",
        help="Implies --dev against a hosted deployment (its https URL or a saved server name): Vite serves the "
        f"local frontend on http://{DEV_WINDOW_HOST}:PORT and proxies /api, /config.js and /healthz to it; "
        "prints the redirect URI to register at its identity provider. --dev --server means the same.",
    )
    p.add_argument("--timing", action="store_true", help="Print how long startup and shutdown took.")
    p.add_argument(
        "--dev",
        action="store_true",
        help="Development (source checkout, Windows): run the viewer under uvicorn --reload and the frontend "
        "under Vite (hot reload), and open the window on Vite, which proxies to the viewer.",
    )
    p.add_argument(
        "--frontend-url",
        default=None,
        metavar="URL",
        help="Implies --dev, but open this already running Vite dev server instead of starting one "
        f"(start it with ${ENV_DEV_BACKEND}=http://127.0.0.1:PORT/ and --config {DEV_VITE_CONFIG}).",
    )
    p.add_argument(
        "--dev-port",
        type=int,
        default=None,
        metavar="N",
        help=f"With --dev: the Vite port (default: the viewer port + {DEV_VITE_PORT_OFFSET}).",
    )


def _print(msg: str) -> None:
    print(msg, flush=True)


def _files(args: argparse.Namespace) -> list[str]:
    return list(getattr(args, "words", None) or [])


def _open_file_arg(args: argparse.Namespace) -> pathlib.Path | None:
    from ada_cli import CliUsageError

    words = _files(args)
    if not words:
        return None
    if len(words) > 1:
        raise CliUsageError(f"only one FILE can be opened, got {len(words)}")
    path = pathlib.Path(words[0])
    if not path.is_file():
        raise CliUsageError(f"{words[0]}: no such file")
    return path


def _key_or_warn(base: str, path: pathlib.Path | None) -> str | None:
    if path is None:
        return None
    try:
        return upload_file(base, path)
    except (DesktopError, urllib.error.URLError, OSError) as e:
        print(f"ada desktop: warning: opening the viewer without {path.name}: {e}", file=sys.stderr, flush=True)
        return None


def _run_shell(binary: pathlib.Path, url: str, args: argparse.Namespace, job: _Job) -> int:
    t0 = time.monotonic()
    shell = subprocess.Popen([str(binary), "--url", url, "--title", args.title, *shell_args(args)])
    job.assign(shell)
    rc = shell.wait()
    if args.timing:
        _print(f"[timing] window open {time.monotonic() - t0:.1f}s, shell exit {rc}")
    return rc


def _attach(choice: PortChoice, args: argparse.Namespace, binary: pathlib.Path | None) -> int:
    base = f"http://{LOCAL_HOST}:{choice.port}/"
    ignored = [f for f in ("data", "static", "zip", "viewer") if getattr(args, f, None)]
    if ignored:
        print(
            f"ada desktop: warning: {', '.join('--' + f for f in ignored)} ignored: the viewer on port "
            f"{choice.port} is already running with its own settings",
            file=sys.stderr,
            flush=True,
        )
    url = viewer_url(base, _key_or_warn(base, _open_file_arg(args)))
    if binary is None:  # --no-window
        _print(f"ada desktop: a viewer is already running at {base}")
        _print(f"viewer: {url}")
        return 0
    _print(
        f"ada desktop: a viewer started by another `ada desktop` runs on port {choice.port}; opening one more window on it"
    )
    marker = _clients_dir(choice.port) / str(os.getpid())
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    try:
        return _run_shell(binary, url, args, _Job())
    finally:
        marker.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> int:
    words = _files(args)
    if words[:1] == ["servers"] and not pathlib.Path("servers").exists():
        return run_servers(words[1:], args)
    dev = bool(args.dev or args.frontend_url)
    backend = getattr(args, "backend", None)
    if backend and args.server:
        from ada_cli import CliUsageError

        raise CliUsageError("--backend and --server exclude each other (with --dev they mean the same)")
    if dev and not (backend or args.server) and not args.local:
        return run_dev(args)  # the local viewer: $ADA_DESKTOP_SERVER does not turn a plain --dev remote
    server = resolve_server(args, option="backend" if backend else "server")
    if server is not None:
        # `--dev --server X` is `--dev --backend X`: dev frontend, hosted backend.
        if dev or backend:
            return run_dev_backend(args, server)
        return run_remote(args, server)
    if dev:
        return run_dev(args)
    t_start = time.monotonic()
    file_path = _open_file_arg(args)
    binary = None if args.no_window else find_binary(args.binary)
    port, source = requested_port(args.port)
    choice = choose_port(port)
    if choice.attach:
        return _attach(choice, args, binary)
    if choice.fallback_from is not None:
        print(fallback_warning(choice, source), file=sys.stderr, flush=True)

    environ = os.environ
    static = resolve_static(args, environ)
    base = f"http://{LOCAL_HOST}:{choice.port}/"
    token = secrets.token_urlsafe(24)
    data = pathlib.Path(args.data) if args.data else data_root()
    data.mkdir(parents=True, exist_ok=True)

    env = dict(environ)
    env.update(
        ADA_VIEWER_STORAGE_KIND="local",
        ADA_VIEWER_LOCAL_PATH=str(data),
        ADA_VIEWER_HOST=LOCAL_HOST,
        ADA_VIEWER_PORT=str(choice.port),
        ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN=token,
        ADA_VIEWER_AUTH_ENABLED="false",
    )
    # No queue: jobs run in-process, and no hosted infrastructure is reached.
    env.pop("ADA_VIEWER_NATS_URL", None)
    if static is not None:
        env["ADA_VIEWER_STATIC_PATH"] = str(static)

    job = _Job()
    server = subprocess.Popen([sys.executable, "-m", "ada.comms.rest"], env=env)
    job.assign(server)
    how = None
    rc = 0
    try:
        ready = wait_healthy(base, server)
        if args.timing:
            _print(f"[timing] viewer ready after {ready:.2f}s (start to ready {time.monotonic() - t_start:.2f}s)")
        url = viewer_url(base, _key_or_warn(base, file_path))
        _print(f"viewer: {url}")
        if binary is None:
            _print("ada desktop: --no-window: press Ctrl+C to stop the viewer")
            server.wait()
        else:
            rc = _run_shell(binary, url, args, job)
            waiting = live_clients(choice.port)
            if waiting:
                _print(f"ada desktop: waiting for {len(waiting)} more window(s) on this viewer before stopping it")
            while waiting:
                time.sleep(0.5)
                waiting = live_clients(choice.port)
    except KeyboardInterrupt:
        rc = 130
    finally:
        t0 = time.monotonic()
        how = stop_server(server, base, token)
        if args.timing:
            _print(f"[timing] viewer stopped via {how} in {time.monotonic() - t0:.2f}s")
    return rc


def main_run(args: argparse.Namespace) -> int:
    """``ada desktop``'s entry: :func:`run`, with a :class:`DesktopError` as one line and exit 1."""
    try:
        return run(args)
    except DesktopError as e:
        print(f"ada desktop: error: {e}", file=sys.stderr)
        return 1

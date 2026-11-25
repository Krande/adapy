"""The viewer ``ada dev connections`` runs: the standard library only.

``python -m ada.comms.rest`` (the full REST app) needs the viewer API's packages (uvicorn, FastAPI,
obstore, NATS and more), which a plain ada-py install does not have. The connection builder needs
little of it, so this server answers just that, with :mod:`http.server`:

* the viewer itself (``ADA_VIEWER_STATIC_PATH``, an installed bundle's build, else the single-file
  build ada-py ships, ``ada/visit/rendering/resources/index.zip``, read straight from the zip);
* ``/config.js`` and ``/api/config``: a local viewer (no login, no database, no queue) serving a
  connection library, so the page opens the connection builder and asks for nothing else;
* the six ``/api/dev/connections`` routes, answered by the same code as the full app's
  (:mod:`.dev_connections`);
* ``GET /api/scopes/{scope}/blobs/{key}``: the previews and subjects the builds store, held in
  memory (the newest per spec, as the full app keeps them);
* ``POST /api/local/shutdown`` with the launch's token, as the full app has it (see
  :mod:`.local_shutdown`), so ``ada dev connections`` stops it the same way.

``POST /api/rpc`` (the page's message channel) answers 204, "no message". Every other ``/api``
path is a 404, as on a viewer without that feature, and the page carries on without it: what the
connection builder asks for besides its own routes (``/api/me``, the stored layout, the simulation
providers, the clash-detail models, a GLB's optional ``.stats.json``) it treats as optional. What
the full app adds
(simulation runs, conversions, the file browser's uploads) is not here: run ``python -m
ada.comms.rest`` with ``ADA_DEV_CONNECTIONS`` set for that.

Started by ``ada dev connections`` with the full app's environment variables (``ADA_VIEWER_HOST``,
``ADA_VIEWER_PORT``, ``ADA_VIEWER_STATIC_PATH``, ``ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN`` and
``ADA_DEV_CONNECTIONS``). Loopback only: requests that name another host are refused.
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import queue as queue_mod
import secrets
import sys
import threading
import urllib.parse
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ada.config import logger

from .dev_connections import (
    SSE_HEARTBEAT_S,
    DevConnections,
    DevReply,
    DevRequestError,
    build_reply,
    configured_path,
    sse,
    subject_reply,
)

SHUTDOWN_ROUTE = "/api/local/shutdown"
SHUTDOWN_HEADER = "X-Ada-Local-Shutdown"
DEV_PREFIX = "/api/dev/connections"

#: Content types by suffix. Not :mod:`mimetypes`: on Windows it reads the registry, which can map
#: ``.js`` to ``text/plain``, and a browser refuses a module script served as that.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}
_ASSET_SUFFIXES = {".js", ".mjs", ".css", ".wasm", ".map"}


def content_type(name: str) -> str:
    return _CONTENT_TYPES.get(pathlib.PurePosixPath(name).suffix.lower(), "application/octet-stream")


def is_loopback(host: str | None) -> bool:
    """Whether ``host`` (an address or a name, no port) is this machine (as :mod:`.local_shutdown`)."""
    if not host:
        return False
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _host_name(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header[1 : host_header.find("]")] if "]" in host_header else host_header
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


# -- the viewer's files -----------------------------------------------------------------------------


class ViewerFiles:
    """The viewer build: a directory (``index.html`` + ``assets/``) or the single-file zip."""

    def __init__(self, source: pathlib.Path):
        self.source = source
        self._zip: zipfile.ZipFile | None = None
        self._zip_lock = threading.Lock()
        if source.is_file():
            self._zip = zipfile.ZipFile(source)
            self._names = set(self._zip.namelist())
            if "index.html" not in self._names:
                raise ValueError(f"{source} has no index.html at its top level; is it a viewer build?")
        elif not (source / "index.html").is_file():
            raise ValueError(f"{source}: no index.html there")

    def read(self, rel: str) -> bytes | None:
        """The file at ``rel`` (a URL path without its leading slash), or None."""
        rel = rel.strip("/")
        if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
            return None
        if self._zip is not None:
            if rel not in self._names:
                return None
            with self._zip_lock:
                return self._zip.read(rel)
        root = self.source.resolve()
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate.read_bytes() if candidate.is_file() else None


def resolve_viewer(static_path: str | None = None) -> pathlib.Path:
    """What to serve: ``static_path`` (else ``ADA_VIEWER_STATIC_PATH``), an installed viewer bundle
    (``ADA_VIEWER_BUNDLE``), else the single-file build ada-py ships."""
    static_path = (static_path or os.environ.get("ADA_VIEWER_STATIC_PATH", "")).strip()
    if static_path:
        return pathlib.Path(static_path)
    from ada.comms.viewer_bundles import resolve_viewer_bundle

    bundle = resolve_viewer_bundle()
    if bundle is not None:
        if bundle.dist is not None:
            return bundle.dist
        if bundle.embed_zip is not None:
            return bundle.embed_zip
        raise ValueError(f'viewer bundle "{bundle.name}" has no viewer build ({bundle.root})')
    import ada

    z = pathlib.Path(ada.__file__).parent / "visit" / "rendering" / "resources" / "index.zip"
    if not z.is_file():
        raise ValueError(f"no bundled viewer ({z}); set ADA_VIEWER_STATIC_PATH")
    return z


# -- what the page is told --------------------------------------------------------------------------


def adapy_version() -> str:
    version = os.environ.get("ADAPY_VERSION", "").strip()
    if version:
        return version
    try:
        from ada._version import __version__ as v

        if v and v != "0.0.0":
            return v
    except Exception:  # noqa: BLE001 - display only
        pass
    try:
        from importlib.metadata import version as dist_version

        return dist_version("ada-py")
    except Exception:  # noqa: BLE001
        return ""


def config_js(*, local_shutdown: bool) -> str:
    """``window.*`` for a local viewer serving a connection library: no login, no database, no
    queue, no conversions, a local store. Only the connection builder is switched on."""
    lines = [
        'window.COMMS_MODE = "rest";',
        'window.API_BASE = "/api";',
        "window.CONVERT_ENABLED = false;",
        "window.LOCAL_JOBS = false;",
        "window.AUTH_ENABLED = false;",
        'window.AUTH_ISSUER = "";',
        'window.AUTH_CLIENT_ID = "";',
        'window.AUTH_AUDIENCE = "";',
        'window.AUTH_SCOPE = "";',
        "window.VIEWER_IMAGE_TAG = null;",
        "window.WORKER_IMAGE_TAG = null;",
        "window.ADAPY_BUILD_REF = null;",
        f"window.ADAPY_VERSION = {json.dumps(adapy_version())};",
        "window.EXTRA_SOURCE_EXTS = [];",
        "window.STREAMING_ONLY_EXTS = [];",
        "window.CONVERSION_MATRIX = [];",
    ]
    ui_default = os.environ.get("ADA_VIEWER_UI_DEFAULT", "").strip()
    if ui_default:
        lines.append(f"window.ADA_UI_DEFAULT = {json.dumps(ui_default)};")
    if local_shutdown:
        lines.append("window.ADA_LOCAL_SHUTDOWN = true;")
    lines += [
        "window.ADA_DEV_CONNECTIONS = true;",
        "window.ADA_NO_DATABASE = true;",
        "window.ADA_NO_PRESIGNED_URLS = true;",
    ]
    return "\n".join(lines) + "\n"


# -- the server -------------------------------------------------------------------------------------


class BlobStore:
    """The files builds store (previews, subjects), in memory: ``key -> (data, content_encoding)``."""

    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, str | None]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, data: bytes, encoding: str | None) -> None:
        with self._lock:
            self._blobs[key] = (data, encoding)

    def get(self, key: str) -> tuple[bytes, str | None] | None:
        with self._lock:
            return self._blobs.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._blobs.pop(key, None)


class DevConnectionsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        dev: DevConnections | None,
        viewer: ViewerFiles | None,
        shutdown_token: str = "",
    ):
        super().__init__(address, _Handler)
        self.dev = dev
        self.viewer = viewer
        self.blobs = BlobStore()
        #: The shutdown route exists only with a token and on loopback (as :mod:`.local_shutdown`).
        self.shutdown_token = shutdown_token if is_loopback(address[0]) else ""
        self.stopping = threading.Event()

    def stop(self) -> None:
        """Stop serving (from any thread but the serving one) and the library host."""
        self.stopping.set()
        threading.Thread(target=self.shutdown, daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    server: DevConnectionsServer
    server_version = "ada-dev-connections"

    # -- plumbing --

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
        logger.debug("dev-connections server: " + format, *args)

    def _send(
        self,
        status: int,
        body: bytes,
        ctype: str,
        headers: dict[str, str] | None = None,
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json", {"Cache-Control": "no-store"})

    def _error(self, status: int, detail: str | None = None) -> None:
        self._json(status, {"detail": detail or HTTPStatus(status).phrase})

    def _body(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"null")
        except ValueError:
            raise DevRequestError(400, "the request body is not JSON") from None

    def _local_request(self) -> bool:
        return is_loopback(self.client_address[0]) and is_loopback(_host_name(self.headers.get("Host", "")))

    def _path(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    # -- methods --

    def do_HEAD(self) -> None:
        self._get(head_only=True)

    def do_GET(self) -> None:
        self._get(head_only=False)

    def _get(self, head_only: bool) -> None:
        if not self._local_request():
            return self._error(403, "this viewer answers its own machine only")
        path = self._path()
        if path == "/config.js":
            body = config_js(local_shutdown=bool(self.server.shutdown_token)).encode()
            return self._send(200, body, "application/javascript", {"Cache-Control": "no-store"}, head_only)
        if path == "/healthz":
            return self._json(200, {"ok": True})
        if path == "/api/config":
            return self._json(200, self._api_config())
        if path.startswith("/api/"):
            return self._api_get(path, head_only)
        return self._static(path, head_only)

    def do_POST(self) -> None:
        if not self._local_request():
            return self._error(403, "this viewer answers its own machine only")
        path = self._path()
        if path == SHUTDOWN_ROUTE and self.server.shutdown_token:
            given = self.headers.get(SHUTDOWN_HEADER, "")
            if not given or not secrets.compare_digest(given, self.server.shutdown_token):
                return self._error(403, "wrong or missing local shutdown token")
            logger.info("dev-connections server: shutdown requested from the page")
            self._json(202, {"stopping": True})
            self.server.stop()
            return
        if path == "/api/rpc":
            # The page's message channel (file listings and the like): there is nothing behind it
            # here. 204 is the page's "no message", which it takes quietly; a 404 it logs as an error.
            self.send_response(204)
            self.end_headers()
            return
        dev = self.server.dev
        if dev is None or not path.startswith(DEV_PREFIX):
            return self._error(404)
        try:
            if path == f"{DEV_PREFIX}/build":
                return self._answer(build_reply(dev, self._body()))
            if path == f"{DEV_PREFIX}/subject":
                return self._answer(subject_reply(dev, self._body()))
            if path == f"{DEV_PREFIX}/reload":
                dev.reload(["(manual reload)"])
                return self._json(200, dev.snapshot())
            if path == f"{DEV_PREFIX}/restart":
                dev.restart()
                return self._json(200, dev.snapshot())
        except DevRequestError as e:
            return self._error(e.status, e.detail)
        return self._error(404)

    # -- routes --

    def _api_config(self) -> dict[str, Any]:
        token = self.server.shutdown_token
        shutdown = {"available": True, "token": token, "header": SHUTDOWN_HEADER} if token else {"available": False}
        return {
            "transport": "rest",
            "apiBase": "/api",
            "convertEnabled": False,
            "localJobs": False,
            "utilities": [],
            "auth": {"enabled": False, "issuer": "", "clientId": "", "audience": ""},
            "viewerImageTag": None,
            "workerImageTag": None,
            "adapyBuildRef": None,
            "extraSourceExts": [],
            "streamingOnlyExts": [],
            "conversionMatrix": [],
            "localShutdown": shutdown,
            "viewerControl": "off",
        }

    def _answer(self, reply: DevReply) -> None:
        if reply.store is not None:
            key, data, encoding = reply.store
            self.server.blobs.put(key, data, encoding)
        for stale in reply.delete:
            self.server.blobs.delete(stale)
        self._json(reply.status, reply.body)

    def _api_get(self, path: str, head_only: bool) -> None:
        dev = self.server.dev
        if dev is not None and path == DEV_PREFIX:
            return self._json(200, dev.snapshot())
        if dev is not None and path == f"{DEV_PREFIX}/events":
            return self._events(dev)
        # /api/scopes/{scope}/blobs/{key}: the key is URL-encoded as one segment, or spans several.
        parts = path.split("/")
        if len(parts) >= 6 and parts[1:3] == ["api", "scopes"] and parts[4] == "blobs":
            key = urllib.parse.unquote("/".join(parts[5:]))
            return self._blob(key, head_only)
        logger.debug("dev-connections server: %s is not served here (404)", path)
        return self._error(404)

    def _blob(self, key: str, head_only: bool) -> None:
        found = self.server.blobs.get(key)
        if found is None:
            return self._error(404, f"no such file: {key}")
        data, encoding = found
        headers = {"Cache-Control": "no-store"}
        if encoding:
            headers["Content-Encoding"] = encoding
        self._send(200, data, content_type(key), headers, head_only)

    def _events(self, dev: DevConnections) -> None:
        q = dev.subscribe_thread()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._write_event(sse({"type": "state", "reason": "subscribe", **dev.snapshot()}))
            while not self.server.stopping.is_set():
                try:
                    event = q.get(timeout=SSE_HEARTBEAT_S)
                except queue_mod.Empty:
                    self._write_event(": keep-alive\n\n")
                    continue
                self._write_event(sse(event))
        except (BrokenPipeError, ConnectionError, OSError):
            pass  # the page went away
        finally:
            dev.unsubscribe(q)
            self.close_connection = True

    def _write_event(self, text: str) -> None:
        self.wfile.write(text.encode())
        self.wfile.flush()

    def _static(self, path: str, head_only: bool) -> None:
        viewer = self.server.viewer
        if viewer is None:
            return self._error(404, "no viewer build is served")
        rel = urllib.parse.unquote(path).lstrip("/")
        if rel and rel != "index.html":
            data = viewer.read(rel)
            if data is not None:
                cache = "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-cache"
                return self._send(200, data, content_type(rel), {"Cache-Control": cache}, head_only)
            # A missing asset is a 404, not the page (a browser refuses HTML as a module script).
            if rel.startswith(("assets/", "wasm/")) or pathlib.PurePosixPath(rel).suffix in _ASSET_SUFFIXES:
                return self._error(404)
        index = viewer.read("index.html") or b""
        self._send(200, index, content_type("index.html"), {"Cache-Control": "no-cache"}, head_only)


def make_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    library: str | None = None,
    viewer: pathlib.Path | None = None,
    shutdown_token: str = "",
) -> DevConnectionsServer:
    """A server on ``host:port`` (0: a free port) serving ``library`` (an ``ada_connections.toml`` or
    its folder; None: no library) and the viewer build at ``viewer`` (None: none). The library host
    is not started: call ``server.dev.start()``."""
    dev = DevConnections(library) if library else None
    files = ViewerFiles(viewer) if viewer is not None else None
    return DevConnectionsServer((host, port), dev, files, shutdown_token)


def main() -> int:
    host = os.environ.get("ADA_VIEWER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("ADA_VIEWER_PORT", "8080"))
    library = configured_path()
    if not library:
        print(f"set ADA_DEV_CONNECTIONS to a connection library ({__name__})", file=sys.stderr)
        return 2
    if not is_loopback(host):
        print(f"ADA_VIEWER_HOST={host}: this server listens on loopback only", file=sys.stderr)
        return 2
    try:
        viewer = resolve_viewer()
        server = make_server(
            host,
            port,
            library=library,
            viewer=viewer,
            shutdown_token=os.environ.get("ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN", "").strip(),
        )
    except (OSError, ValueError) as e:
        print(f"dev-connections server: {e}", file=sys.stderr)
        return 1
    assert server.dev is not None
    server.dev.start()
    logger.info("dev-connections server: http://%s:%d/ (viewer: %s)", host, server.server_address[1], viewer)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.dev.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

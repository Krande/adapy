"""``ada dev connections``: develop a connection library live in the studio.

1. Read the library's ``ada_connections.toml`` (``--dir``, default the current folder; see
   :mod:`ada.api.connections.library` for the format) so a bad one fails here, with its own message.
2. Start the local viewer (as ``ada desktop`` does: local storage, no login, no queue) with
   ``ADA_DEV_CONNECTIONS`` naming the library. The viewer holds the library in a child process,
   watches its files and reloads it on every change; the studio's connection builder lists the
   library's specs and rebuilds the selected one, with the members' sections and plate
   thicknesses editable per joint.
3. Open it: in the ada-desktop window when that is installed, else (or with ``--browser``) in the
   default browser; ``--no-window`` just prints the URL. Ctrl+C (or closing the window) stops it.

``--dev`` runs the viewer from this source checkout instead (uvicorn --reload, Vite hot reload),
for working on the studio itself â€” it is ``ada desktop --dev`` with the library attached.

Its own port (by default this user's ``ada desktop`` port + 1000), so it runs beside ``ada desktop``.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import subprocess
import sys
import time
import webbrowser

from ada_cli import desktop

#: Mirrors ada.comms.rest.dev_connections.ENV_CONFIG (not imported: that pulls in the REST app).
ENV_CONFIG = "ADA_DEV_CONNECTIONS"
#: Offset from the ``ada desktop`` default port: above ``ada desktop --dev``'s Vite ports.
PORT_OFFSET = 1000


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--dir",
        default=".",
        metavar="DIR",
        help="The connection library: a folder holding ada_connections.toml, or the toml itself (default: .).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help=f"Viewer port (default: this user's `ada desktop` port + {PORT_OFFSET}, else a free one).",
    )
    p.add_argument(
        "--data",
        default=None,
        metavar="DIR",
        help=f"Local storage for the built previews (default: {desktop.data_root()}).",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--static", default=None, metavar="DIR", help="Serve this built viewer directory.")
    src.add_argument("--zip", default=None, metavar="FILE", help="Serve this single-file viewer zip.")
    win = p.add_mutually_exclusive_group()
    win.add_argument("--browser", action="store_true", help="Open the default browser, not the ada-desktop window.")
    win.add_argument("--no-window", action="store_true", help="Print the URL and wait for Ctrl+C.")
    p.add_argument("--binary", default=None, metavar="PATH", help="The ada-desktop shell (see `ada desktop`).")
    p.add_argument("--title", default="Ada Studio - connections", help="Window title.")
    p.add_argument(
        "--dev",
        action="store_true",
        help="Run the viewer from this source checkout under uvicorn --reload and Vite (hot reload), to "
        "work on the studio itself (Windows; as `ada desktop --dev`).",
    )


def _load_library(path: str):
    from ada.api.connections.library import LibraryConfigError, load_config
    from ada_cli import CliUsageError

    try:
        return load_config(path)
    except LibraryConfigError as e:
        raise CliUsageError(str(e)) from None


def _port(cli_port: int | None) -> int:
    if cli_port is not None:
        if desktop.probe_port(cli_port) != "free":
            raise desktop.DesktopError(f"--port {cli_port} is in use")
        return cli_port
    port = desktop.default_port() + PORT_OFFSET
    return port if desktop.probe_port(port) == "free" else desktop.free_port()


def run(args: argparse.Namespace) -> int:
    cfg = _load_library(args.dir)
    _print(f"ada dev connections: {cfg.name} ({cfg.path})")
    what = ", ".join([*cfg.packages, *([cfg.loader] if cfg.loader else [])])
    _print(f"  loads {what}; watches {', '.join(str(w) for w in cfg.watch)}")
    os.environ[ENV_CONFIG] = str(cfg.path)

    if args.dev:
        return desktop.run_dev(_desktop_dev_args(args))

    port = _port(args.port)
    base = f"http://{desktop.LOCAL_HOST}:{port}/"
    static = desktop.resolve_static(args, os.environ)
    data = pathlib.Path(args.data) if args.data else desktop.data_root()
    data.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)

    env = dict(os.environ)
    env.update(
        ADA_VIEWER_STORAGE_KIND="local",
        ADA_VIEWER_LOCAL_PATH=str(data),
        ADA_VIEWER_HOST=desktop.LOCAL_HOST,
        ADA_VIEWER_PORT=str(port),
        ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN=token,
        ADA_VIEWER_AUTH_ENABLED="false",
    )
    env.pop("ADA_VIEWER_NATS_URL", None)
    if static is not None:
        env["ADA_VIEWER_STATIC_PATH"] = str(static)

    binary = None
    if not (args.no_window or args.browser):
        try:
            binary = desktop.find_binary(args.binary)
        except desktop.DesktopError:
            if args.binary:
                raise
            _print("ada dev connections: no ada-desktop window shell installed; opening the default browser")

    job = desktop._Job()
    server = subprocess.Popen([sys.executable, "-m", "ada.comms.rest"], env=env)
    job.assign(server)
    rc = 0
    try:
        desktop.wait_healthy(base, server)
        _print(f"viewer: {base}  (the library loads in the background; edits under the watched folders reload it)")
        if binary is not None:
            rc = desktop._run_shell(binary, base, _shell_args(args), job)
        else:
            if not args.no_window:
                webbrowser.open(base)
            _print("ada dev connections: press Ctrl+C to stop")
            while server.poll() is None:
                time.sleep(0.5)
    except KeyboardInterrupt:
        rc = 130
    finally:
        desktop.stop_server(server, base, token)
    return rc


def _shell_args(args: argparse.Namespace) -> argparse.Namespace:
    """What ``desktop._run_shell`` reads off its namespace."""
    return argparse.Namespace(title=args.title, timing=False, shell_arg=[], no_sso=False)


def _desktop_dev_args(args: argparse.Namespace) -> argparse.Namespace:
    """``ada desktop --dev``'s namespace for this launch."""
    return argparse.Namespace(
        words=[],
        no_window=args.no_window or args.browser,
        binary=args.binary,
        frontend_url=None,
        port=args.port,
        dev_port=None,
        data=args.data,
        title=args.title,
        timing=False,
        shell_arg=[],
        no_sso=False,
    )


def _print(msg: str) -> None:
    print(msg, flush=True)


def main_run(args: argparse.Namespace) -> int:
    try:
        return run(args)
    except desktop.DesktopError as e:
        print(f"ada dev connections: error: {e}", file=sys.stderr)
        return 1

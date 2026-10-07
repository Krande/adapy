"""Screenshot Ada Studio as a set of user stories, for the docs.

    pixi run -e tests ui-stories                     # every story
    pixi run -e tests ui-stories --list
    pixi run -e tests ui-stories --story fea-modes --story clash
    pixi run -e tests ui-stories --theme light       # *-light.png (the docs use dark)
    pixi run -e tests ui-stories --keep              # leave the server up afterwards

Each story is a function below that opens one screen in the state a user would
see it and writes docs/screenshots/ada-studio/<name>.png, which the Ada Studio
docs page embeds.

The script boots a throwaway Ada Studio of its own: the REST API
(``python -m ada.comms.rest`` from the ``viewer-api`` env) with local storage
in .pixi/ui-stories/, wiped on every run, no auth, no database and no job
queue, serving the SPA from src/frontend/dist (``pixi run -e frontend
wbuild-serve`` builds it). Without a queue there is no worker to convert
anything, so the seeding does the worker's job in this process with the same
code the worker runs -- ``ada.comms.rest.converter.convert`` for the model GLBs
and ``bake_fea_artefacts_from_source`` for the FEA results -- and uploads the
outputs through the HTTP API the in-browser converters use. What the browser
then loads is byte for byte what a deployment's worker would have produced.
"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from playwright.sync_api import Locator, Page, sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots" / "ada-studio"
WORK = ROOT / ".pixi" / "ui-stories"
DIST = ROOT / "src" / "frontend" / "dist"
VIEWPORT = {"width": 1440, "height": 900}
THEME = "dark"
SCOPE = "user:me"

MODEL = "structure.ifc"
FEA = "cantilever_eigen.rmed"
PENETRATION = "penetration_detail.glb"
PENETRATION_EXAMPLE = ROOT / "examples" / "penetration_detail.py"
FEA_SOURCE = ROOT / "files" / "fem_files" / "cantilever" / "code_aster" / "eigen_shell_cantilever_code_aster.rmed"


# ── The throwaway server ─────────────────────────────────────────────────────


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(port: int) -> dict[str, str]:
    # Drop the calling pixi environment's activation, so `pixi run -e viewer-api` activates its own.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIXI_", "CONDA_"))}
    for key in ("ADA_VIEWER_NATS_URL", "DATABASE_URL"):
        env.pop(key, None)
    env.update(
        {
            "ADA_VIEWER_HOST": "127.0.0.1",
            "ADA_VIEWER_PORT": str(port),
            "ADA_VIEWER_STORAGE_KIND": "local",
            "ADA_VIEWER_LOCAL_PATH": str(WORK / "storage"),
            "ADA_VIEWER_STATIC_PATH": str(DIST),
            "ADA_VIEWER_AUTH_ENABLED": "false",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    return env


def start_server() -> tuple[subprocess.Popen, str]:
    if not (DIST / "index.html").is_file():
        sys.exit("No src/frontend/dist — build it with `pixi run -e frontend wbuild-serve`.")
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "storage").mkdir(parents=True)

    port = free_port()
    log = (WORK / "server.log").open("w")
    pixi = shutil.which("pixi") or "pixi"
    proc = subprocess.Popen(
        [pixi, "run", "--manifest-path", str(ROOT / "pixi.toml"), "-e", "viewer-api", "python", "-m", "ada.comms.rest"],
        cwd=ROOT,
        env=server_env(port),
        stdout=log,
        stderr=subprocess.STDOUT,
        # Its own process group, so stop_server can take pixi and the server down together.
        start_new_session=os.name != "nt",
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"The server exited; see {WORK / 'server.log'}")
        try:
            if httpx.get(f"{base_url}/healthz", timeout=2).is_success:
                return proc, base_url
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    stop_server(proc)
    sys.exit(f"The server didn't answer /healthz within 180s; see {WORK / 'server.log'}")


# ── Seeding ──────────────────────────────────────────────────────────────────


def build_model(path: pathlib.Path) -> None:
    """The topology engine's demo structure: two bays of HEB columns, IPE girders and
    stiffened decks (see docs/documents/topology_engine.md)."""
    from ada.topo_model.build import build_topo_model

    build_topo_model("Structure").to_ifc(path)


def build_penetration_figure(path: pathlib.Path) -> None:
    """The procedural-modelling docs example, run as is, reduced to what the figure is about:
    the crossed wall, the equipment, the pipe and the detail, without the roof in the way."""
    import runpy

    import ada

    g = runpy.run_path(str(PENETRATION_EXAMPLE), run_name="ui_stories")
    result, pipe = g["result"], g["pipe"]
    wall = result.penetrations[0].face.associated_part
    fig = ada.Assembly("PenetrationDetail") / [
        ada.Part("Wall") / list(wall.get_all_physical_objects()),
        ada.Part("Equipment") / [g["pump"], g["tank"]],
        ada.Part("Piping") / result.route_geometry[pipe.name],
        *result.penetration_parts,
    ]
    fig.to_gltf(path)


def bake_fea(src: pathlib.Path, key: str) -> bytes:
    """The worker's FEA bake, zipped flat the way the browser bake uploads it."""
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    out = WORK / "bake" / key
    bake_fea_artefacts_from_source(src, out, src_key=key)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.iterdir()):
            if f.is_file() and f.name.startswith("fea."):
                zf.write(f, f.name)
    return buf.getvalue()


def seed(base_url: str) -> None:
    from ada.comms.rest.converter import convert, result_bytes

    print("Seeding demo data…", flush=True)
    src_dir = WORK / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)

    model = src_dir / MODEL
    build_model(model)
    shutil.copyfile(FEA_SOURCE, src_dir / FEA)
    build_penetration_figure(src_dir / PENETRATION)

    with httpx.Client(base_url=f"{base_url}/api/scopes/{SCOPE}", timeout=600) as api:

        def check(resp: httpx.Response, what: str) -> None:
            if not resp.is_success:
                raise RuntimeError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")

        for key in (MODEL, FEA, PENETRATION):
            check(api.put(f"/blobs/{key}", content=(src_dir / key).read_bytes()), f"upload {key}")

        glb = result_bytes(convert(model, MODEL, "glb"))
        check(api.put("/derived", params={"source": MODEL, "target": "glb"}, content=glb), f"derived GLB for {MODEL}")
        check(api.post("/fea/artefacts", params={"source": FEA}, content=bake_fea(src_dir / FEA, FEA)), f"bake {FEA}")


# ── Stories ──────────────────────────────────────────────────────────────────


@dataclass
class Story:
    name: str
    summary: str
    # Returns None to shoot the viewport, or a locator to shoot just that.
    run: Callable[[Page], Locator | None]


STORIES: dict[str, Story] = {}


def story(name: str, summary: str):
    def register(fn: Callable[[Page], Locator | None]):
        STORIES[name] = Story(name, summary, fn)
        return fn

    return register


def settle(page: Page, ms: int = 1500) -> None:
    """Let requests finish and the scene render before the shutter."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def open_model(page: Page, key: str = MODEL) -> None:
    """The deep link the /convert page's "View in 3D" uses, with the GLB the seeding made."""
    page.goto(f"/?scope={SCOPE}&file={key}&derived=_derived/{key}.glb")
    page.locator("canvas").first.wait_for()
    settle(page, 4000)


def open_fea(page: Page, key: str = FEA) -> None:
    """A result file opens in the streaming FEA viewer from its baked artefacts."""
    page.goto(f"/?scope={SCOPE}&file={key}")
    page.get_by_title("Toggle animation controls").wait_for(timeout=60_000)
    settle(page, 4000)


def open_tree(page: Page) -> None:
    page.get_by_role("button", name="Show selection tree").click()
    page.get_by_role("treeitem").first.wait_for()


def expand(page: Page, item: Locator) -> None:
    """Open one level of the tree: the row's own disclosure arrow."""
    item.get_by_text("▶").first.click()
    page.wait_for_timeout(400)


def drill(page: Page, levels: int = 8) -> Locator:
    """Open the tree's first branch level by level, down to its first leaf; return that row.
    An opened row's first child is the row right below it, so the walk is by index."""
    row = 0
    for _ in range(levels):
        item = page.get_by_role("treeitem").nth(row)
        if not item.get_by_text("▶").count():
            return item
        expand(page, item)
        row += 1
    return page.get_by_role("treeitem").nth(row)


@story("model", "An IFC model opened from storage, with its object tree")
def _model(page: Page) -> None:
    open_model(page)
    open_tree(page)
    drill(page, levels=3)
    settle(page)


@story("select", "Selecting a member and reading its properties")
def _select(page: Page) -> None:
    open_model(page)
    open_tree(page)
    drill(page).click()
    page.get_by_title("Toggle object info").click()
    page.get_by_text("Selected Object Info").first.wait_for()
    settle(page)


@story("storage", "Files in your storage scope, and what has been made from them")
def _storage(page: Page) -> None:
    open_model(page)
    page.get_by_title("Storage").click()
    page.get_by_role("heading", name="Storage").wait_for()
    # The first listing goes out before the signed-in scope is known; refresh, as a user would.
    page.get_by_role("button", name="Refresh list").click()
    page.get_by_text(FEA).first.wait_for()
    settle(page)


@story("fea-modes", "Eigenmodes of a Code_Aster result, streamed step by step")
def _fea_modes(page: Page) -> None:
    open_fea(page)
    page.get_by_title("Step 1 of", exact=False).select_option(index=2)
    settle(page, 3000)


@story("penetration", "A pipe through a shared wall with its penetration detail (procedural modelling page)")
def _penetration(page: Page) -> None:
    page.goto(f"/?scope={SCOPE}&file={PENETRATION}")
    page.locator("canvas").first.wait_for()
    settle(page, 4000)


@story("clash", "A clash check: every joint in the model, grouped by type")
def _clash(page: Page) -> None:
    open_model(page)
    page.get_by_role("button", name="Toggle scene info").click()
    page.get_by_role("tab", name="Clashes").click()
    page.get_by_role("button", name="Run clash check").click()
    page.get_by_test_id("clash-origin-filter").wait_for(timeout=120_000)
    settle(page, 2000)


# ── Runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--story", action="append", choices=sorted(STORIES), help="run only this story; repeatable")
    parser.add_argument("--list", action="store_true", help="list the stories and exit")
    parser.add_argument(
        "--theme",
        choices=["dark", "light"],
        help=f"shoot in this theme and write <story>-<theme>.png (default: {THEME}, <story>.png)",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--keep", action="store_true", help="leave the demo server running afterwards")
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help="default %(default)s")
    args = parser.parse_args()

    if args.list:
        for s in STORIES.values():
            print(f"  {s.name:<14} {s.summary}")
        return 0

    selected = [STORIES[n] for n in args.story] if args.story else list(STORIES.values())
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.theme}" if args.theme else ""
    theme = args.theme or THEME

    proc, base_url = start_server()
    failed: list[str] = []
    try:
        seed(base_url)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=not args.headed, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
            )
            ctx = browser.new_context(base_url=base_url, viewport=VIEWPORT, color_scheme=theme)
            for s in selected:
                path = args.out / f"{s.name}{suffix}.png"
                page = ctx.new_page()
                try:
                    target = s.run(page)
                    (target or page).screenshot(path=path)
                    print(f"  ✓ {s.name:<14} {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
                except Exception as exc:  # keep going; report every broken story
                    failed.append(s.name)
                    print(f"  ✗ {s.name:<14} {exc}", file=sys.stderr)
                finally:
                    page.close()
            ctx.close()
            browser.close()

        if args.keep:
            print(f"\nAda Studio at {base_url} — Ctrl-C to stop.")
            proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_server(proc)

    return 1 if failed else 0


def stop_server(proc: subprocess.Popen) -> None:
    """`pixi run` starts the server as a child; take the whole tree down with it."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        return
    import signal

    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


if __name__ == "__main__":
    sys.exit(main())

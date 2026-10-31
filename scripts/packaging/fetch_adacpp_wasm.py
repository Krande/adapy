"""Put the adacpp wasm modules into the SPA's public/wasm for local dev, and keep them current.

The viewer fetches ``/wasm/adacpp_*.js`` at RUNTIME — the in-browser STEP/IFC
converters, the GLB diff, the compact beam-solid expander and the WebGPU renderer.
The image build puts them there (deploy/Dockerfile.viewer), but the Vite dev server
serves only ``src/frontend/public``, where the directory is gitignored and starts
out empty. Every one of those features then fails into a console warning, or, for
the renderer, "Not available, three.js is drawing".

Two sources, both read from deploy/Dockerfile.viewer rather than restated here, so
the dev server serves what a deployment would:

- the embind modules, copied out of the ``ARG ADACPP_BASE_IMAGE`` base image;
- the WebGPU renderer, built from the ``adacpp-render`` stage, which compiles adacpp
  at ``ARG ADACPP_RENDER_REF``.

``.adacpp-pins.json`` in ``dest`` records the pins the files were made from. A pin
that moves makes its part stale, and only that part is fetched again.

    python scripts/packaging/fetch_adacpp_wasm.py [dest] [--if-stale]    # needs docker

``dest`` defaults to src/frontend/public/wasm. ``--if-stale`` is what ``wdev`` runs on
every start: current files cost no docker call, and a machine without docker gets a
warning and a dev server whose wasm features degrade, rather than no dev server.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO / "deploy" / "Dockerfile.viewer"
_DEFAULT_DEST = _REPO / "src" / "frontend" / "public" / "wasm"
_PINS_FILE = ".adacpp-pins.json"
_RENDER_STAGE = "adacpp-render"
_RENDER_FILES = ("adacpp_render.js", "adacpp_render.wasm", "adacpp_render.ref")


class FetchError(RuntimeError):
    pass


def _arg_pin(name: str, text: str) -> str:
    found = re.findall(rf"^ARG {name}=(\S+)$", text, re.MULTILINE)
    if len(found) != 1:
        raise FetchError(f"expected one ARG {name} in {_DOCKERFILE}, got {found}")
    return found[0]


def pins(dockerfile_text: str | None = None) -> dict[str, str]:
    """What deploy/Dockerfile.viewer builds the viewer's wasm from."""
    text = _DOCKERFILE.read_text(encoding="utf-8") if dockerfile_text is None else dockerfile_text
    return {"base_image": _arg_pin("ADACPP_BASE_IMAGE", text), "render_ref": _arg_pin("ADACPP_RENDER_REF", text)}


def _recorded(dest: pathlib.Path) -> dict[str, str]:
    try:
        return json.loads((dest / _PINS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stale_parts(dest: pathlib.Path, wanted: dict[str, str]) -> list[str]:
    """The pins in ``wanted`` whose files in ``dest`` are missing or were made from another value."""
    have = _recorded(dest)
    stale = [key for key, value in wanted.items() if have.get(key) != value]
    if "base_image" not in stale and not (dest / "adacpp_extrude.js").is_file():
        stale.append("base_image")
    if "render_ref" not in stale and not all((dest / name).is_file() for name in _RENDER_FILES):
        stale.append("render_ref")
    return stale


def _docker(*args: str) -> str:
    try:
        done = subprocess.run(["docker", *args], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise FetchError("docker is not on PATH") from e
    if done.returncode != 0:
        raise FetchError(f"docker {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def _copy_out(image: str, src: str, dest: pathlib.Path, keep) -> list[str]:
    cid = _docker("create", image, "true")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _docker("cp", f"{cid}:{src}/.", tmp)
            dest.mkdir(parents=True, exist_ok=True)
            copied = []
            for f in sorted(pathlib.Path(tmp).iterdir()):
                if keep(f):
                    shutil.copy2(f, dest / f.name)
                    copied.append(f.name)
    finally:
        subprocess.run(["docker", "rm", cid], capture_output=True)
    return copied


def fetch_base_modules(image: str, dest: pathlib.Path) -> list[str]:
    print(f"copying /out/wasm from {image} -> {dest}", file=sys.stderr)
    # `docker create` needs the image locally; pull first so a fresh machine works.
    _docker("pull", "-q", image)
    copied = _copy_out(image, "/out/wasm", dest, lambda f: f.suffix in (".js", ".wasm", ".ts"))
    if "adacpp_extrude.js" not in copied:
        raise FetchError(f"{image} ships no adacpp_extrude.js under /out/wasm — got {copied}")
    return copied


def build_renderer(ref: str, dest: pathlib.Path) -> list[str]:
    tag = f"adapy-{_RENDER_STAGE}:{ref[:12]}"
    print(f"building the {_RENDER_STAGE} stage (adacpp {ref[:12]}) -> {dest}", file=sys.stderr)
    # The stage copies nothing from the build context, so an empty one keeps the repo out of it.
    with tempfile.TemporaryDirectory() as context:
        _docker("build", "-q", "-f", str(_DOCKERFILE), "--target", _RENDER_STAGE, "-t", tag, context)
    copied = _copy_out(tag, "/out", dest, lambda f: f.name in _RENDER_FILES)
    built_from = (dest / "adacpp_render.ref").read_text(encoding="utf-8").strip()
    if built_from != ref:
        raise FetchError(f"{tag} was built from adacpp {built_from}, expected {ref}")
    return copied


def main(argv: list[str]) -> int:
    if_stale = "--if-stale" in argv
    positional = [a for a in argv[1:] if not a.startswith("--")]
    dest = pathlib.Path(positional[0]) if positional else _DEFAULT_DEST
    copied: list[str] = []
    try:
        wanted = pins()
        stale = stale_parts(dest, wanted) if if_stale else list(wanted)
        if not stale:
            print(f"adacpp wasm in {dest} is current", file=sys.stderr)
            return 0
        recorded = _recorded(dest)
        for part, fetch in (("base_image", fetch_base_modules), ("render_ref", build_renderer)):
            if part in stale:
                copied += fetch(wanted[part], dest)
                # Recorded per part, so a renderer build that fails does not cost the next run the base too.
                recorded[part] = wanted[part]
                (dest / _PINS_FILE).write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")
    except FetchError as e:
        if not if_stale:
            raise SystemExit(str(e))
        print(f"WARNING: adacpp wasm not updated ({e}); the dev server's wasm features will degrade", file=sys.stderr)
        return 0
    print("\n".join(copied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Copy the adacpp embind wasm modules into the SPA's public/wasm for local dev.

The viewer fetches ``/wasm/adacpp_*.js`` at RUNTIME — the in-browser STEP/IFC
converters, the GLB diff and the compact beam-solid expander. The image build
puts them there (deploy/Dockerfile.viewer promotes the base image's /out/wasm/),
but the Vite dev server serves only ``src/frontend/public``, where the directory
is gitignored and starts out empty. Every one of those features then fails into a
console warning: a compact-baked result shows a "Beams as solid" toggle with
nothing behind it.

This copies the modules out of the SAME base image the viewer is built on — the
``ARG ADACPP_BASE_IMAGE`` pin in deploy/Dockerfile.viewer, read here rather than
restated — so the dev server serves what a deployment would.

    python scripts/packaging/fetch_adacpp_wasm.py [dest]    # needs docker

``dest`` defaults to src/frontend/public/wasm.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO / "deploy" / "Dockerfile.viewer"
_ARG_PIN = re.compile(r"^ARG ADACPP_BASE_IMAGE=(\S+)$", re.MULTILINE)


def base_image() -> str:
    found = _ARG_PIN.findall(_DOCKERFILE.read_text(encoding="utf-8"))
    if len(found) != 1:
        raise SystemExit(f"expected one ARG ADACPP_BASE_IMAGE in {_DOCKERFILE}, got {found}")
    return found[0]


def _docker(*args: str) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def main(argv: list[str]) -> int:
    dest = pathlib.Path(argv[1]) if len(argv) > 1 else _REPO / "src" / "frontend" / "public" / "wasm"
    image = base_image()
    print(f"copying /out/wasm from {image} -> {dest}", file=sys.stderr)
    # `docker create` needs the image locally; pull first so a fresh machine works.
    _docker("pull", "-q", image)
    cid = _docker("create", image, "true")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _docker("cp", f"{cid}:/out/wasm/.", tmp)
            dest.mkdir(parents=True, exist_ok=True)
            copied = []
            for f in sorted(pathlib.Path(tmp).iterdir()):
                if f.suffix in (".js", ".wasm", ".ts"):
                    shutil.copy2(f, dest / f.name)
                    copied.append(f.name)
    finally:
        subprocess.run(["docker", "rm", cid], capture_output=True)
    if not any(n == "adacpp_extrude.js" for n in copied):
        raise SystemExit(f"{image} ships no adacpp_extrude.js under /out/wasm — got {copied}")
    print("\n".join(copied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Build the ada.topo_model demo, upload it to the personal viewer scope and
stream it to the local websocket viewer.

Run: pixi run -e prod topo-model-demo
Flags: --no-upload (skip the hosted-viewer upload), --no-show (skip show()).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from ada.topo_model import build_topo_model

ADAPY_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_env_file(env_file: pathlib.Path) -> None:
    """Minimal stdlib KEY=VAL .env loader; existing environment wins."""
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _upload_to_personal_scope(glb: pathlib.Path) -> None:
    _load_env_file(ADAPY_ROOT / ".env")
    if not os.environ.get("ADAPY_API_TOKEN"):
        print("no ADAPY_API_TOKEN (adapy/.env) — skipping personal-scope upload")
        return
    cmd = [
        sys.executable,
        str(ADAPY_ROOT / "scripts" / "viewer_upload.py"),
        str(glb),
        "--scope",
        "user:me",
        "--as",
        "topo_model_demo.glb",
    ]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"viewer upload failed with exit code {res.returncode} (non-fatal)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-upload", action="store_true", help="skip the personal-scope viewer upload")
    ap.add_argument("--no-show", action="store_true", help="build + export only, do not open the viewer")
    args = ap.parse_args()

    a = build_topo_model()

    glb = pathlib.Path("temp/topo_model_demo.glb").resolve()
    glb.parent.mkdir(parents=True, exist_ok=True)
    a.to_gltf(glb)
    print(f"wrote {glb}")

    if not args.no_upload:
        _upload_to_personal_scope(glb)
    if not args.no_show:
        a.show()


if __name__ == "__main__":
    main()

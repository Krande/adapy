"""Stamp the inputs of the committed viewer bundle, and fail when they have moved on.

``src/ada/visit/rendering/resources/index.zip`` is built by ``pixi run wbuild`` from ``src/frontend``
and committed. Nothing used to tie it to the sources it came from, so a frontend change could merge
without a rebuild and ship a stale viewer. ``index.zip.inputs`` next to it is that tie: a hash over
everything the build reads, plus the per-file list it was made from (so a failing check can say which
inputs changed).

    python scripts/packaging/bundle_inputs.py write    # refresh the stamp (``pixi run wbuild`` does)
    python scripts/packaging/bundle_inputs.py check    # exit 1 when the stamp is out of date

Stdlib only, needs ``git``, and no build: the hash is over git blob ids of the files as they would be
committed, so it does not depend on the OS or on line endings.

What counts as an input (see ``is_input``): the tracked files under ``src/frontend`` the default Vite
build can read -- sources, ``index.html``, ``public/``, the configs, ``package.json`` and the lock, the
committed plugin registry -- and the two pins in ``deploy/Dockerfile.viewer`` that decide the WebGPU
renderer inlined into the bundle (``ARG ADACPP_BASE_IMAGE`` and ``ARG ADACPP_RENDER_REF``, see
``fetch_adacpp_wasm.py``). Left out: tests, markdown, and what only the other builds (embed, serve,
desktop dev server) or the tooling around them read. When unsure, a file is an input: a needless
rebuild costs a minute, a missed input ships a stale bundle.

The stamp also records the sha256 of index.zip itself (``bundle-sha256``), so a bundle changed from outside
-- rebuilt from other sources, replaced by hand, the other side of a merge conflict -- fails ``check`` even
when the inputs match. index.zip is binary (``-text`` in git), so the working file is the committed bytes.

The package ``version`` in ``package.json`` and ``package-lock.json`` is ignored. The release bumps it
in the tagged commit and nothing bundles it, so counting it would make every release stale.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = "src/frontend"
STAMP = "src/ada/visit/rendering/resources/index.zip.inputs"
BUNDLE = "src/ada/visit/rendering/resources/index.zip"
DOCKERFILE = "deploy/Dockerfile.viewer"
PINS = ("ADACPP_BASE_IMAGE", "ADACPP_RENDER_REF")
HASH_LINE = "inputs-sha256"
BUNDLE_LINE = "bundle-sha256"

# Directories under src/frontend (prefix match on the path relative to it) that no default build reads.
_EXCLUDED_DIRS = (
    "src/__tests__/",  # the unit tests
    "docs/",
    "scripts/",  # gen-plugin-registry (its output is committed and counted), the seam check, diagnostics
    "embed/",  # the paradoc embed builds
    "embed-webgpu/",
    "public/models/",  # a .gitignore keeping a local drop folder
)
# Other builds' configs, and the tooling around the default build.
_EXCLUDED_FILES = (
    "vite.config.embed.ts",
    "vite.config.embed-webgpu.ts",
    "vite.config.serve.ts",
    "vite.config.desktop.ts",
    "vite.desktop-proxy.mjs",
    "tsconfig.embed.json",
    "tsconfig.embed-webgpu.json",
    ".gitignore",
)
_EXCLUDED_PATTERNS = ("*.test.*", "*.md")
# package.json fields (top level) and lock fields that change on a release without changing the bundle.
_VERSIONED = ("package.json", "package-lock.json")


def is_input(rel: str) -> bool:
    """Whether ``rel`` (a path relative to ``src/frontend``, forward slashes) feeds the default build."""
    if rel.startswith(_EXCLUDED_DIRS) or rel in _EXCLUDED_FILES:
        return False
    return not any(fnmatch.fnmatch(rel.rsplit("/", 1)[-1], p) for p in _EXCLUDED_PATTERNS)


def _git(root: pathlib.Path, *args: str, stdin: bytes | None = None) -> bytes:
    done = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def _blob_ids(root: pathlib.Path, paths: list[str]) -> list[str]:
    """What ``git add`` would store for each path, from the working tree."""
    if not paths:
        return []
    out = _git(root, "hash-object", "--stdin-paths", stdin=("\n".join(paths) + "\n").encode())
    return out.decode().split()


def _strip_version(rel: str, data: bytes) -> str | None:
    """Blob-style id of a package manifest/lock without its own version, or None if it will not parse."""
    try:
        doc = json.loads(data)
    except ValueError:
        return None
    doc.pop("version", None)
    if isinstance(doc.get("packages"), dict) and isinstance(doc["packages"].get(""), dict):
        doc["packages"][""].pop("version", None)
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha1(canon).hexdigest()


def dockerfile_pins(root: pathlib.Path) -> dict[str, str]:
    text = (root / DOCKERFILE).read_text(encoding="utf-8")
    pins = {}
    for name in PINS:
        found = re.findall(rf"^ARG {name}=(\S+)$", text, re.MULTILINE)
        if len(found) != 1:
            raise SystemExit(f"expected exactly one 'ARG {name}=' in {DOCKERFILE}, found {found}")
        pins[name] = found[0]
    return pins


def collect(root: pathlib.Path = REPO) -> dict[str, str]:
    """Every input, as {name: id}, where the name is a repo path or ``pin:<ARG>``."""
    tracked = _git(root, "ls-files", "-z", "--", FRONTEND).decode().split("\0")
    wanted = sorted(
        p
        for p in tracked
        if p and p.startswith(FRONTEND + "/") and is_input(p[len(FRONTEND) + 1 :]) and (root / p).is_file()
    )
    ids = dict(zip(wanted, _blob_ids(root, wanted)))
    for p in wanted:
        if p.rsplit("/", 1)[-1] in _VERSIONED and p.count("/") == FRONTEND.count("/") + 1:
            stripped = _strip_version(p, (root / p).read_bytes())
            if stripped is not None:
                ids[p] = stripped
    for name, value in dockerfile_pins(root).items():
        ids[f"pin:{name}"] = value
    return ids


def digest(inputs: dict[str, str]) -> str:
    lines = "".join(f"{name} {inputs[name]}\n" for name in sorted(inputs))
    return hashlib.sha256(lines.encode()).hexdigest()


def bundle_digest(root: pathlib.Path = REPO) -> str:
    """sha256 of index.zip's bytes ("" when it is missing). The zip is binary (`-text` in git), so the
    working file is the committed file byte for byte."""
    path = root / BUNDLE
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def render(inputs: dict[str, str], bundle: str) -> str:
    header = (
        "# The inputs src/ada/visit/rendering/resources/index.zip was built from, and its own hash. Do not edit.\n"
        "# Refresh with `pixi run wbuild` (rebuilds the bundle and rewrites this), then commit both.\n"
        "# Checked by `pixi run check-bundle-inputs` and by CI. See scripts/packaging/bundle_inputs.py.\n"
    )
    body = "".join(f"{name} {inputs[name]}\n" for name in sorted(inputs))
    return f"{header}{HASH_LINE} {digest(inputs)}\n{BUNDLE_LINE} {bundle}\n{body}"


def parse(text: str) -> tuple[str, str, dict[str, str]]:
    stamped = ""
    bundle = ""
    inputs: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        if key == HASH_LINE:
            stamped = value.strip()
        elif key == BUNDLE_LINE:
            bundle = value.strip()
        else:
            inputs[key] = value.strip()
    return stamped, bundle, inputs


def changes(stamped: dict[str, str], now: dict[str, str]) -> list[str]:
    out = []
    for name in sorted(set(stamped) | set(now)):
        if name not in stamped:
            out.append(f"  added    {name}")
        elif name not in now:
            out.append(f"  removed  {name}")
        elif stamped[name] != now[name]:
            out.append(f"  changed  {name}")
    return out


def write(root: pathlib.Path = REPO) -> int:
    if os.environ.get("ADA_PLUGINS_EXTRA"):
        print(
            "WARNING: ADA_PLUGINS_EXTRA is set, so the bundle just built may carry out-of-tree plugins "
            "that must not be committed (CI's provenance check rejects it).",
            file=sys.stderr,
        )
    path = root / STAMP
    inputs = collect(root)
    bundle = bundle_digest(root)
    if not bundle:
        print(f"{BUNDLE} does not exist; build it first (`pixi run wbuild`)", file=sys.stderr)
        return 1
    path.write_text(render(inputs, bundle), encoding="utf-8", newline="\n")
    print(f"wrote {STAMP} (inputs {digest(inputs)[:12]}, bundle {bundle[:12]})")
    return 0


def check(root: pathlib.Path = REPO) -> int:
    path = root / STAMP
    now = collect(root)
    if not path.is_file():
        print(f"the viewer bundle has no stamp ({STAMP}); {_FIX}", file=sys.stderr)
        return 1
    stamped_hash, stamped_bundle, stamped = parse(path.read_text(encoding="utf-8"))
    if stamped_hash == digest(now) and stamped == now:
        if stamped_bundle and stamped_bundle == bundle_digest(root):
            print(f"the viewer bundle is up to date (inputs {stamped_hash[:12]}, bundle {stamped_bundle[:12]})")
            return 0
        why = (
            "the stamp has no bundle-sha256 line"
            if not stamped_bundle
            else "index.zip's bytes differ from the stamped ones"
        )
        print(
            f"the viewer bundle changed without its stamp: index.zip is not the one built from these inputs ({why}) "
            f"-- {_FIX}",
            file=sys.stderr,
        )
        return 1
    moved = changes(stamped, now)
    print(f"the viewer bundle is stale: its inputs changed since it was built -- {_FIX}", file=sys.stderr)
    if moved:
        print("inputs that differ from the stamp:\n" + "\n".join(moved[:40]), file=sys.stderr)
        if len(moved) > 40:
            print(f"  ... and {len(moved) - 40} more", file=sys.stderr)
    else:
        print("(the stamp itself was edited by hand)", file=sys.stderr)
    return 1


_FIX = f"run `pixi run wbuild` and commit {BUNDLE.rsplit('/', 1)[-1]} and {STAMP.rsplit('/', 1)[-1]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("write", "check"))
    ap.add_argument("--root", type=pathlib.Path, default=REPO, help="the repository (default: this one)")
    args = ap.parse_args(argv)
    return (write if args.command == "write" else check)(args.root)


if __name__ == "__main__":
    raise SystemExit(main())

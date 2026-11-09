"""The FEA verification report for the docs image, reused while nothing it is built from has changed.

The report (solver runs, posters, the web bundle and the DOCX / ODT / PDF exports) is most of the docs
image's build, and Docker's layer cache cannot keep it: the release string changes with every commit.
So the finished output is kept in a directory that outlives the build (a BuildKit cache mount), under a
hash of every input: adapy's package, the report's sources and committed solver snapshots, the locked
environment, and paradoc's own files. Paradoc's per-task cache is not enough for a verification report:
its call-graph walk cannot see dynamic dispatch, so a solver change could reuse the old results here.

Paradoc's frontend is not an input: only the 3D viewer depends on it, and a hit installs the current one
(--frontend-zip), so a viewer change reaches the report without re-running the analysis.

An export that fails warns and leaves a dead download link, as before, and that build is not kept: a
passing failure must not be replayed until the inputs next change.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, Iterable

#: What a finished report is (relative to the repo root).
OUTPUTS = ("docs/_static/fea-report", "docs/_static/fea-report-files")
EXPORTS = ("docx", "odt", "pdf")
#: Inputs, as single files or trees (relative to the repo root).
INPUTS = ("pixi.lock", "pixi.toml", "pyproject.toml", "scripts/docs/fea_report_cached.py", "src/ada", "verification")
#: Build products that sit among the inputs.
_SKIP_DIRS = {"__pycache__", "_assets", "temp", ".paradoc-cache"}
_FRONTEND_ZIP = "frontend/resources/frontend.zip"
#: In paradoc's directory but not code: the frontend, and the overlay's note of which commit it came from.
_PARADOC_NON_INPUTS = {_FRONTEND_ZIP, "OVERLAY"}

_CONFIG_RE = re.compile(
    r"<head[^>]*>(<script>\(function\(\)\{var c=window\.__PARADOC_CONFIG__=.*?</script>)", re.DOTALL
)


def _files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".pyc"):
                yield Path(dirpath) / name


def report_key(repo: Path, paradoc_dir: Path) -> str:
    """The hash of everything the report is built from, the viewer frontend aside."""
    h = hashlib.sha256()
    trees = [(rel, repo / rel) for rel in INPUTS] + [("<paradoc>", paradoc_dir)]
    for label, root in trees:
        for f in _files(root):
            if label == "<paradoc>" and f.relative_to(root).as_posix() in _PARADOC_NON_INPUTS:
                continue
            rel = f.relative_to(root).as_posix() if root.is_dir() else ""
            h.update(f"{label}/{rel}\0".encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:24]


def install_frontend(report_dir: Path, frontend_zip: Path) -> bool:
    """Replace a finished report's frontend, keeping the runtime config paradoc seeded into its page.
    False (nothing changed) when the page has no config to carry over."""
    index = report_dir / "index.html"
    found = _CONFIG_RE.search(index.read_text(encoding="utf-8")) if index.is_file() else None
    if not found:
        return False
    with zipfile.ZipFile(frontend_zip) as archive:
        archive.extractall(report_dir)
    html = index.read_text(encoding="utf-8")
    head = html.find("<head")
    close = html.find(">", head)
    if head < 0 or close < 0:
        raise SystemExit(f"{frontend_zip} has no <head> in its index.html")
    index.write_text(html[: close + 1] + found.group(1) + html[close + 1 :], encoding="utf-8")
    return True


def _copy_outputs(src_root: Path, dest_root: Path) -> None:
    for rel in OUTPUTS:
        dest = dest_root / rel
        shutil.rmtree(dest, ignore_errors=True)
        if (src_root / rel).is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_root / rel, dest)


def save(repo: Path, entry: Path, keep: int) -> None:
    """Keep this build's report as `entry` (complete or not at all); drop the least recently used."""
    tmp = entry.with_name(f".{entry.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    _copy_outputs(repo, tmp)
    shutil.rmtree(entry, ignore_errors=True)
    tmp.rename(entry)
    entries = sorted(
        (p for p in entry.parent.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in entries[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def build_report(run: Callable[[str], bool]) -> bool:
    """The report and its exports; True when every export succeeded. A failed report stops the build."""
    if not run("fea-doc-build"):
        raise SystemExit("fea-doc-build failed")
    ok = True
    for fmt in EXPORTS:
        if not run(f"fea-doc-{fmt}"):
            print(f"WARNING: fea-doc-{fmt} failed: the report's {fmt} download link will be dead", flush=True)
            ok = False
    return ok


def produce(
    repo: Path, cache: Path, paradoc_dir: Path, frontend_zip: Path | None, keep: int, run: Callable[[str], bool]
) -> str:
    """Restore the report for these inputs, or build and keep it. Returns "hit" or "built"."""
    cache.mkdir(parents=True, exist_ok=True)
    key = report_key(repo, paradoc_dir)
    entry = cache / key
    if entry.is_dir():
        _copy_outputs(entry, repo)
        report = repo / OUTPUTS[0]
        if frontend_zip is None or not frontend_zip.is_file() or install_frontend(report, frontend_zip):
            os.utime(entry)
            print(f"fea report: reused {key}, its inputs are unchanged", flush=True)
            return "hit"
        print(f"fea report: {key} has no runtime config to carry over; rebuilding", flush=True)
    print(f"fea report: building {key}", flush=True)
    if build_report(run):
        save(repo, entry, keep)
    else:
        print(f"fea report: not keeping {key}, an export failed", flush=True)
    return "built"


def _pixi_task(task: str) -> bool:
    return subprocess.run(["pixi", "run", "--environment", "docs", task], check=False).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, required=True, help="directory that outlives the build")
    parser.add_argument("--frontend-zip", type=Path, default=None, help="paradoc frontend for a reused report")
    parser.add_argument("--keep", type=int, default=3, help="reports to keep")
    args = parser.parse_args()
    import paradoc

    repo = Path(__file__).resolve().parents[2]
    produce(repo, args.cache, Path(paradoc.__file__).resolve().parent, args.frontend_zip, args.keep, _pixi_task)


if __name__ == "__main__":
    sys.exit(main())

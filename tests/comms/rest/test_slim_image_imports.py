"""The API process runs the SLIM viewer image: ``deploy/Dockerfile.viewer`` copies a curated subset of
``ada`` into it (no CAD stack, no modelling stack, no worker handlers). Code the API runs may only
import ``ada`` modules that subset ships -- or guard the import (``except ImportError``) and degrade.

A request handler that lazily imported a worker-only module passed every test (the full env has
everything) and answered a bare 500 in the image. This test reads the Dockerfile's own COPY lines, so
the shipped set cannot drift from what the image has, and checks two things:

* statically: every ``ada`` import in the API-side code (module level or inside a function) resolves
  inside the shipped set, unless guarded by a ``try`` that catches ImportError;
* dynamically: with ONLY the shipped files on ``sys.path``, the app imports and the lazily-imported
  helpers the API calls work.
"""

from __future__ import annotations

import ast
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
SRC = REPO / "src"
DOCKERFILE = REPO / "deploy" / "Dockerfile.viewer"
DOCKERFILE_FAST = REPO / "deploy" / "Dockerfile.viewer-fast"
REST = SRC / "ada" / "comms" / "rest"

# Not run by the API process: worker handlers, the converters they call, and the queue-less
# ("local") engine that runs them in-process on a single-node viewer with the full environment.
WORKER_SIDE = {
    "formats",
    "worker",
    "converters",
    "utilities",
}
WORKER_SIDE_FILES = {
    "local_jobs.py",
    "subprocess_convert.py",
    "selection_export.py",
    "search_export.py",
    "__main__.py",
}
# Imports the API makes only behind a caller that catches ImportError and degrades (the guard cannot see
# the caller). (file relative to ada/comms/rest, target) -> why.
CALLER_GUARDED = {
    ("procedural.py", "ada.topo_model.engines"): "_doc_model() is called under `except ImportError` in validate_doc",
    ("procedural.py", "ada.topology.entities"): "_doc_model() is called under `except ImportError` in validate_doc",
}
# Worker-side modules the API must never import (they import the CAD stack at module level).
WORKER_PACKAGES = ("ada.comms.rest.formats", "ada.comms.rest.worker")


def _runtime_copies() -> list[tuple[str, str]]:
    """(source, destination) of every COPY in the runtime stage that puts ``src/ada`` code under /app."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text[text.index("AS runtime") :]
    return re.findall(r"^COPY\s+(\S+)\s+(/app/\S+)\s*$", runtime, re.M)


def _shipped_modules() -> set[str]:
    mods: set[str] = set()
    for src, dst in _runtime_copies():
        path = REPO / src
        files = [p for p in path.rglob("*.py")] if path.is_dir() else [path]
        for f in files:
            if f.suffix != ".py":
                continue
            rel = pathlib.Path(dst).relative_to("/app/src")
            if path.is_dir():
                rel = rel / f.relative_to(path)
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            mods.add(".".join(parts))
    mods.add("ada.sections")  # the slim stub the Dockerfile writes
    return mods


SHIPPED = _shipped_modules()


def _exists_in_full_tree(module: str) -> bool:
    base = SRC.joinpath(*module.split("."))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def _api_files() -> list[pathlib.Path]:
    out = []
    for p in sorted(REST.rglob("*.py")):
        rel = p.relative_to(REST)
        if rel.parts[0] in WORKER_SIDE or (len(rel.parts) == 1 and rel.name in WORKER_SIDE_FILES):
            continue
        out.append(p)
    return out


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in {"ImportError", "ModuleNotFoundError", "Exception"} for n in names)


def _unshipped_imports(path: pathlib.Path) -> list[tuple[int, str]]:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    is_pkg = parts[-1] == "__init__"
    pkg = parts[:-1] if is_pkg else parts[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(_catches_import_error(h) for h in node.handlers):
            for child in node.body:
                for sub in ast.walk(child):
                    guarded.add(id(sub))
    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            targets = [mod] + [f"{mod}.{a.name}" for a in node.names if a.name != "*"]
        for t in targets:
            if t.split(".")[0] != "ada":
                continue
            if t.startswith(WORKER_PACKAGES):
                bad.append((node.lineno, t))
            elif t not in SHIPPED and _exists_in_full_tree(t):
                bad.append((node.lineno, t))
    return bad


def test_the_fast_dockerfile_ships_what_the_full_one_does():
    """``Dockerfile.viewer-fast`` overlays the same COPYs on a prior image; a module added to one and
    not the other ships in some builds only."""

    def copies(path: pathlib.Path) -> set[tuple[str, str]]:
        return {(s, d) for s, d in re.findall(r"^COPY\s+(\S+)\s+(/app/src/ada\S*)\s*$", path.read_text("utf-8"), re.M)}

    assert copies(DOCKERFILE_FAST) == copies(DOCKERFILE)


@pytest.mark.parametrize("path", _api_files(), ids=lambda p: str(p.relative_to(REST)).replace("\\", "/"))
def test_api_code_imports_only_what_the_slim_image_ships(path):
    rel = str(path.relative_to(REST)).replace("\\", "/")
    bad = [(ln, t) for ln, t in _unshipped_imports(path) if (rel, t) not in CALLER_GUARDED]
    assert not bad, (
        f"{path.relative_to(REPO)} imports ada modules the slim API image does not ship (see "
        f"deploy/Dockerfile.viewer): {bad}. Move the helper into a shipped module, import it where the "
        f"worker runs, or catch ImportError and degrade."
    )


_SLIM_PROBE = """
import asyncio, sys
import ada.comms.rest.app  # noqa: F401  -- every module-level import of the API
from ada.comms.rest.routes import geometry_requests as greq
from ada.comms.rest.scope import Scope
from ada.core.takeoff_tables import takeoff_to_csv, takeoff_to_xlsx_bytes

class S:
    async def list_prefix(self, scope, prefix):
        return []

async def main():
    return await greq.classify_in_scope(S(), Scope.from_prefix('projects/x'), [('c', [])])

asyncio.run(main())
stats = {"disciplines": [{"name": "structural", "mass": 1.0, "count": 2}], "total_mass": 1.0}
assert "Mass by discipline" in takeoff_to_csv(stats, "overview")
assert takeoff_to_xlsx_bytes(stats)[:2] == b"PK"
for forbidden in ("ada.comms.rest.formats", "ada.comms.rest.worker", "ada.cadit", "ada.topo_model"):
    assert forbidden not in sys.modules, forbidden
print("ok")
"""


def test_the_api_runs_from_the_shipped_files_alone(tmp_path):
    """The shipped files, laid out as in the image, and nothing else of ``ada`` on the path."""
    root = tmp_path / "app"
    for src, dst in _runtime_copies():
        s, d = REPO / src, tmp_path / dst.lstrip("/")
        if s.is_dir():
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(s, d)
    (root / "src" / "ada" / "sections").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ada" / "sections" / "__init__.py").write_text(
        "from .categories import SectionCat\n__all__ = ['SectionCat']\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    data.mkdir()
    import os

    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "ADA_VIEWER_STORAGE_KIND": "local",
        "ADA_VIEWER_LOCAL_PATH": str(data),
    }
    done = subprocess.run(
        [sys.executable, "-c", _SLIM_PROBE], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120
    )
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]

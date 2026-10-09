"""Every file the docs build reads must reach the docs image.

deploy/Dockerfile.docs and Dockerfile.docs-fast COPY a curated list, not the repo, and the image is
only built by the tag-time publish workflow -- so a page that starts reading a new file builds green
in every PR job (they run on a full checkout) and fails only after merge. That happened: the meshing
page's 3D figures came from examples/mesh_overrides.py, which neither Dockerfile copied
(FileNotFoundError in `render_figure`).

The inputs are derived from the sources rather than listed here: the scripts behind the 3D figures
(``FIGURES`` in scripts/docs/docs_notebooks.py) and every ``--8<--`` snippet a page includes. Each must
be copied by both docs Dockerfiles and watched by the ci-docs publish trigger.
"""

from __future__ import annotations

import ast
import fnmatch
import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).parents[2]
_DOCKERFILES = ("deploy/Dockerfile.docs", "deploy/Dockerfile.docs-fast")
_WORKFLOW = _REPO / ".github" / "workflows" / "ci-pages.yml"

_needs_repo = pytest.mark.skipif(
    not (_REPO / _DOCKERFILES[0]).is_file(),
    reason=f"no repo tree at {_REPO} (sdist/wheel test env) -- deploy/ is not packaged",
)

_SNIPPET = re.compile(r'^\s*--8<--\s+"([^"]+)"', re.MULTILINE)
_COPY = re.compile(r"^COPY\s+(?!--from)(.+?)\s*$", re.MULTILINE)


def _figure_scripts() -> set[str]:
    """The scripts behind ``FIGURES``, read from the source (importing it needs the docs environment)."""
    tree = ast.parse((_REPO / "scripts" / "docs" / "docs_notebooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else (node.targets[0] if isinstance(node, ast.Assign) else None)
        )
        if isinstance(target, ast.Name) and target.id == "FIGURES":
            # Evaluated, not just walked: entries may be built by a comprehension.
            figures = eval(compile(ast.Expression(node.value), "FIGURES", "eval"), {})  # noqa: S307 - our own source
            return {spec.rsplit(":", 1)[0] for spec in figures.values()}
    raise AssertionError("FIGURES not found in scripts/docs/docs_notebooks.py")


def _snippet_sources() -> set[str]:
    """Files the docs pages include with ``--8<--`` (a ``:section`` suffix names a part of the file)."""
    found = set()
    for page in (_REPO / "docs").rglob("*.md"):
        if "_build" in page.parts:
            continue
        for ref in _SNIPPET.findall(page.read_text(encoding="utf-8")):
            path = ref.split(":", 1)[0]
            if (_REPO / path).is_file():  # repo-relative includes; anything else is not ours to check
                found.add(path)
    return found


def _docs_inputs() -> set[str]:
    return _figure_scripts() | _snippet_sources()


def _copied(dockerfile: str) -> list[str]:
    """The repo paths a Dockerfile COPYs from the build context (``COPY src... dest``)."""
    text = (_REPO / dockerfile).read_text(encoding="utf-8")
    sources = []
    for args in _COPY.findall(text):
        parts = [p for p in args.split() if not p.startswith("--")]
        sources += [p.rstrip("/") for p in parts[:-1]]
    return sources


def _is_copied(path: str, sources: list[str]) -> bool:
    return any(path == src or path.startswith(src + "/") for src in sources)


def _publish_paths() -> list[str]:
    """The `paths:` globs of the ci-docs push trigger."""
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "paths:")
    globs = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("- "):
            break
        globs.append(stripped[2:].strip().strip("'\""))
    return globs


def test_the_docs_inputs_are_found():
    """Guards the guard: the derivation must see the inputs it is meant to check."""
    inputs = _docs_inputs()
    assert "examples/penetration_detail.py" in inputs
    assert "examples/mesh_overrides.py" in inputs


@_needs_repo
@pytest.mark.parametrize("dockerfile", _DOCKERFILES)
def test_every_docs_input_is_copied_into_the_docs_image(dockerfile):
    sources = _copied(dockerfile)
    missing = sorted(p for p in _docs_inputs() if not _is_copied(p, sources))
    assert not missing, f"{dockerfile} does not COPY {missing}; the docs build reads them (FIGURES / --8<--)"


@_needs_repo
def test_every_docs_input_triggers_a_docs_publish():
    globs = _publish_paths()
    missing = sorted(p for p in _docs_inputs() if not any(fnmatch.fnmatch(p, g.replace("**", "*")) for g in globs))
    assert not missing, f"ci-pages.yml's push paths do not cover {missing}; editing them would not republish"

"""Every WASM host must install adapy before dispatching a conversion.

Both pyodide hosts dispatch every cell through ``ada.cadit.wasm_convert``, so
the adapy wheel is a precondition of all of them. Formats needing no CAD kernel
previously installed only their own packages and died at that import. The hosts
are JavaScript and no JS suite runs in CI, so the invariant is pinned here.
"""

from __future__ import annotations

import re

import pytest

# Each host paired with the repo-only tree it lives in. Only a checkout carries
# these: the conda-forge feedstock copies tests/ and files/ alone, so there is
# no frontend tree there to check.
HOSTS = (
    ("src/frontend/src/utils/pyodide/pyodide_worker.js", "src/frontend"),
    ("tools/pyodide-test/wasm_sweep_driver.js", "tools/pyodide-test"),
)

RUNTIME_HELPER = "ensureAdapyRuntime"


def _host_source(root_dir, host: str, tree: str) -> str:
    """Read a host's source, or skip when this is not a repo checkout.

    A missing *tree* means the suite is running against an installed package and
    the invariant does not apply. A missing host inside a present tree is drift,
    and must fail rather than quietly skip.
    """
    if not (root_dir / tree).is_dir():
        pytest.skip(f"source-tree invariant; no {tree} at {root_dir}")
    path = root_dir / host
    assert path.is_file(), f"{host} is gone but {tree} is present — did the host move?"
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """Return the body of ``async function <name>(...) { ... }`` by brace matching."""
    m = re.search(r"async function " + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert m is not None, f"{name} not found"
    depth, start = 0, m.end() - 1
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : i]
    raise AssertionError(f"unbalanced braces in {name}")


def _code_body(root_dir, host: str, tree: str, name: str) -> str:
    """The function's body, comments stripped.

    Stripping matters: the comment above the call names the helper, and would
    otherwise satisfy every check below even with the call itself deleted.
    """
    body = _function_body(_host_source(root_dir, host, tree), name)
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", body, flags=re.S))


@pytest.mark.parametrize(("host", "tree"), HOSTS, ids=[h for h, _ in HOSTS])
def test_adapy_runtime_helper_installs_the_wheel(root_dir, host, tree):
    body = _code_body(root_dir, host, tree, RUNTIME_HELPER)
    # ada/__init__.py is eager: trimesh + pyquaternion must precede the import.
    for required in ("ensureTrimesh", "ensurePyquaternion", "ensureAdapyWheel"):
        assert required in body, f"{host}: {RUNTIME_HELPER} does not await {required}"


@pytest.mark.parametrize(("host", "tree"), HOSTS, ids=[h for h, _ in HOSTS])
def test_ensure_stacks_installs_adapy_before_any_format_branch(root_dir, host, tree):
    body = _code_body(root_dir, host, tree, "ensureStacks")
    assert RUNTIME_HELPER in body, f"{host}: ensureStacks never awaits {RUNTIME_HELPER}"
    # Nothing may branch or return ahead of the install, or a format whose stack
    # skips the CAD kernel reaches the dispatch import without `ada` again.
    head = body[: body.index(RUNTIME_HELPER)]
    for token in (r"\bif\b", r"\breturn\b", r"\?"):
        assert not re.search(token, head), f"{host}: ensureStacks branches before awaiting {RUNTIME_HELPER}"

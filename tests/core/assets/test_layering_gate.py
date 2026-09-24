"""The gate that keeps core ignorant of any provider's source format.

Phase 1's fixture provider uses a private newline-delimited JSON format. If core ever grows
knowledge of it -- a filename, an extension, a provider id -- this fails. That is the whole point
of having a fixture provider rather than only the in-tree IFC one: the IFC provider is *allowed*
to know IFC, so it cannot witness this property.
"""

import importlib.util
import re
from pathlib import Path

import pytest


def _package_dir(name: str) -> Path:
    """Where the IMPORTED package lives -- a checkout's src/ or an install's site-packages.

    find_spec rather than import: locating ada.comms.rest must not need the REST stack's own
    dependencies, which a package-only test env does not carry."""
    spec = importlib.util.find_spec(name)
    assert spec is not None and spec.submodule_search_locations, f"{name} is not an importable package"
    return Path(next(iter(spec.submodule_search_locations))).resolve()


# Everything core must not know about the fixture provider.
FORBIDDEN = (
    "fixture-lines",
    "vendor-lines",
    ".jsonl",
    "asset-build-fixture",
    # the second fixture, whose format is an indented outline and shares nothing with the first
    "fixture-outline",
    ".outline",
    "asset-build-outline",
)

# Where core lives. The asset store and the REST layer must both be clean. Read as the imported
# packages, not as src/ paths: the conda recipe runs this suite against the installed package with
# no src/ tree and no git repo of its own, where a `git grep` over src/ searched the enclosing
# feedstock checkout, found nothing -- and so passed without reading a line of core.
CORE_PACKAGES = ("ada.assets", "ada.comms.rest")
TESTS_DIR = Path(__file__).resolve().parent


def _grep(needle: str, root: Path) -> list[str]:
    """``relpath:line: text`` for every line containing ``needle`` in ``root``'s ``.py`` files.
    An empty tree is an error, not a clean result: a gate that read nothing proves nothing."""
    files = sorted(root.rglob("*.py"))
    assert files, f"no .py files under {root} -- the gate would pass without reading anything"
    return [
        f"{f.relative_to(root.parent)}:{n}: {line.strip()}"
        for f in files
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
        if needle in line
    ]


@pytest.mark.parametrize("token", FORBIDDEN)
def test_core_never_names_the_fixture_providers_format(token):
    hits = [h for pkg in CORE_PACKAGES for h in _grep(token, _package_dir(pkg))]
    assert not hits, (
        f"core names {token!r}, which belongs to a test-only provider:\n  "
        + "\n  ".join(hits)
        + "\n\nCore must stay ignorant of every provider's source format -- source blobs are an "
        "opaque 'role' on an opaque 'file', and core's only uses for them are refcounting and "
        "passing keys through to a job."
    )


def test_the_gate_can_actually_fail():
    """A gate that cannot fail proves nothing -- check the tokens ARE findable where they live."""
    hits = _grep("fixture-lines", TESTS_DIR)
    assert hits, "the fixture provider should name its own format in tests/"


def test_core_asset_package_imports_no_test_code():
    """src/ada must never reach into tests/ -- an easy accident once a fixture is this useful."""
    root = _package_dir("ada.assets")
    files = sorted(root.rglob("*.py"))
    assert files, f"no .py files under {root} -- the check would pass without reading anything"
    offenders = [
        str(path.relative_to(root.parent))
        for path in files
        if re.search(r"^\s*(from|import)\s+tests\b", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert not offenders, f"core imports test code: {offenders}"

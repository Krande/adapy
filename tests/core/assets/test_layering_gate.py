"""The gate that keeps core ignorant of any provider's source format.

Phase 1's fixture provider uses a private newline-delimited JSON format. If core ever grows
knowledge of it -- a filename, an extension, a provider id -- this fails. That is the whole point
of having a fixture provider rather than only the in-tree IFC one: the IFC provider is *allowed*
to know IFC, so it cannot witness this property.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

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

# Where core lives. The asset store and the REST layer must both be clean.
CORE_PATHS = ("src/ada/assets", "src/ada/comms/rest")


def _grep(pattern: str, paths) -> list[str]:
    proc = subprocess.run(
        ["git", "grep", "-n", "-F", pattern, "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):  # 1 == no match, which is what we want
        pytest.skip(f"git grep unavailable here: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


@pytest.mark.parametrize("token", FORBIDDEN)
def test_core_never_names_the_fixture_providers_format(token):
    hits = _grep(token, CORE_PATHS)
    assert not hits, (
        f"core names {token!r}, which belongs to a test-only provider:\n  "
        + "\n  ".join(hits)
        + "\n\nCore must stay ignorant of every provider's source format -- source blobs are an "
        "opaque 'role' on an opaque 'file', and core's only uses for them are refcounting and "
        "passing keys through to a job."
    )


def test_the_gate_can_actually_fail():
    """A gate that cannot fail proves nothing -- check the tokens ARE findable where they live."""
    hits = _grep("fixture-lines", ["tests/core/assets"])
    assert hits, "the fixture provider should name its own format in tests/"


def test_core_asset_package_imports_no_test_code():
    """src/ada must never reach into tests/ -- an easy accident once a fixture is this useful."""
    offenders = []
    for path in (REPO / "src" / "ada" / "assets").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+tests\b", text, re.MULTILINE):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"core imports test code: {offenders}"

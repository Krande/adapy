"""The CI gate Decision 10 names, made a test: ``src/ada/clash`` must never name a fabrication
process, a vendor system or a provider package -- and a joint's ``type_key`` must be built from
core vocabulary only. Follows the shape of ``tests/core/assets/test_layering_gate.py``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from ada.api.connections.spec import MemberKind
from ada.clash import ClashOptions, run_clash_check
from ada.clash.classify import ANGLE_BUCKETS
from ada.sections.categories import BaseTypes
from ada.topo_model import build_topo_model

REPO = Path(__file__).resolve().parents[3]

# "the two job formats, the route and the panel must not contain the tokens weld, tekla, e3d,
# csg" (Decision 10, "Layering, operationalised"; the literal command is §Verification's
# "Clash-check vocabulary gate"). This test covers the backend half that is core's to own:
# src/ada/clash itself.
FORBIDDEN = ("weld", "tekla", "e3d", "csg")
CORE_PATHS = ("src/ada/clash",)


def _git_grep(pattern: str, paths) -> list[str]:
    # --untracked: the clash package may not be committed yet in this checkout, and a gate that
    # only sees tracked files would pass by omission rather than by being clean.
    proc = subprocess.run(
        ["git", "grep", "-niE", "--untracked", pattern, "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):  # 1 == no match, which is what we want
        pytest.skip(f"git grep unavailable here: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_clash_vocabulary_gate_is_empty():
    pattern = "|".join(FORBIDDEN)
    hits = _git_grep(pattern, CORE_PATHS)
    assert not hits, (
        f"src/ada/clash names a fabrication/vendor term ({FORBIDDEN}):\n  "
        + "\n  ".join(hits)
        + "\n\nA `Weld` object may appear only in what a generator returns, never in "
        "identification/classification/matching (Decision 10)."
    )


def test_the_gate_can_actually_fail():
    """A gate that cannot fail proves nothing -- these tokens ARE findable in the repo."""
    hits = _git_grep("weld", ["src/ada/topo_model"])
    assert hits, "expected 'weld' to be findable somewhere in core (topo_model's own detailing)"


# ── the type key's vocabulary, verified by parsing it (not by eyeballing) ────


def _allowed_vocabulary():
    kinds = {k.name for k in MemberKind}
    sections = {bt.value.upper() for bt in BaseTypes} | {"PLATE"}
    member_types = {"COLUMN", "GIRDER", "BRACE"}
    buckets = set(ANGLE_BUCKETS) | {"unknown"}
    return kinds, sections, member_types, buckets


def _assert_key_uses_only_core_vocabulary(type_key: str) -> None:
    kinds, sections, member_types, buckets = _allowed_vocabulary()
    count_str, members_str, bucket = type_key.split("|")
    assert count_str.isdigit()
    assert bucket in buckets, f"{bucket!r} is not a declared angle bucket"
    for token in members_str.split("+"):
        parts = token.split(":")
        assert parts[0] in kinds, f"{parts[0]!r} is not a MemberKind name"
        if len(parts) > 1:
            assert parts[1] in sections, f"{parts[1]!r} is not a section family"
        if len(parts) > 2:
            assert parts[2] in member_types, f"{parts[2]!r} is not a member_type"
        assert len(parts) <= 3


def test_demo_type_keys_use_only_core_vocabulary():
    model = build_topo_model()
    result = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=False))
    assert result.joints, "expected the demo to yield joints to check keys against"
    for joint in result.joints:
        _assert_key_uses_only_core_vocabulary(joint.type_key)


def test_a_key_naming_something_outside_the_vocabulary_is_rejected_by_the_parser():
    """The parser above can actually fail -- pin that on an invented, disallowed token."""
    with pytest.raises(AssertionError):
        _assert_key_uses_only_core_vocabulary("2|BEAM:WELD:GIRDER+BEAM:I:GIRDER|perpendicular")


def test_core_asset_style_gate_also_covers_the_result_and_match_modules():
    # Explicit per-module check in addition to the directory-wide grep above, so a future file
    # split under src/ada/clash cannot narrow the gate by accident. builtin_specs.py carries the
    # no exceptions: the gate is blunt on purpose, so it is checked line by
    # line rather than as a whole file.
    for module in ("result.py", "classify.py", "match.py", "identify.py", "__init__.py"):
        path = REPO / "src" / "ada" / "clash" / module
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert not re.search(token, text, re.IGNORECASE), f"{module} names {token!r}"

    builtin_specs = (REPO / "src" / "ada" / "clash" / "builtin_specs.py").read_text(encoding="utf-8")
    offending_lines = [
        f"src/ada/clash/builtin_specs.py:{i}:{line}"
        for i, line in enumerate(builtin_specs.splitlines(), start=1)
        if re.search("|".join(FORBIDDEN), line, re.IGNORECASE)
    ]
    assert not offending_lines, (
        "src/ada/clash must name no fabrication term at all. The one line that used to need an "
        "exception (a config key forwarded to a built-in joint class) now forwards config by "
        "SIGNATURE instead, so the gate needs no carve-out -- keep it that way: an allowlist is "
        "the thing that grows."
    )

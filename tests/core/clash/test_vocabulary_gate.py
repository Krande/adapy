"""The CI gate Decision 10 names, made a test: ``src/ada/clash`` and ``src/ada/assets`` must never
name a fabrication process, a vendor system or a provider package -- and a joint's ``type_key``
must be built from core vocabulary only. Follows the shape of
``tests/core/assets/test_layering_gate.py``.

Both packages are public and both are provider seams, which is the same rule twice: core's job is
to describe the SHAPE of a source -- a private format, a catalogue, a system of record -- and never
whose it is. The formats core genuinely implements (``ada.cadit.*``) are a different matter and are
not read by this gate: naming a format you can write is documentation, naming one you deliberately
cannot read is a leak.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import ada.assets
import ada.clash
import ada.topo_model
from ada.api.connections.spec import MemberKind
from ada.clash import ClashOptions, run_clash_check
from ada.clash.classify import ANGLE_BUCKETS
from ada.sections.categories import BaseTypes
from ada.topo_model import build_topo_model

#: The packages as IMPORTED, not as paths under a checkout. The conda recipe runs this suite
#: against the INSTALLED package with no `src/` tree beside it, and no git repo of its own: a
#: `git grep` there searched whatever checkout happened to enclose the test files (the
#: feedstock's), found nothing, and so PASSED the gate below without reading a single clash file
#: -- only the self-check noticed. Reading the imported package's own files checks the code that
#: actually ships, in a checkout and in an install alike.
CLASH_PKG = Path(ada.clash.__file__).resolve().parent
TOPO_MODEL_PKG = Path(ada.topo_model.__file__).resolve().parent
ASSETS_PKG = Path(ada.assets.__file__).resolve().parent

# "the two job formats, the route and the panel must not contain the tokens weld, tekla, e3d,
# csg" (Decision 10, "Layering, operationalised"; the literal command is §Verification's
# "Clash-check vocabulary gate"). This test covers the backend half that is core's to own:
# src/ada/clash itself.
FORBIDDEN = ("weld", "tekla", "e3d", "csg")


def _grep_package(pattern: str, pkg: Path) -> list[str]:
    """``relpath:line: text`` for every case-insensitive match in the package's ``.py`` files.

    Every file on disk counts, committed or not, so a gate cannot pass by omission. An empty
    package is an error, not a clean result: a gate that read nothing proves nothing."""
    files = sorted(pkg.rglob("*.py"))
    assert files, f"no .py files under {pkg} -- the gate would pass without reading anything"
    rx = re.compile(pattern, re.IGNORECASE)
    return [
        f"{f.relative_to(pkg.parent)}:{n}: {line.strip()}"
        for f in files
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
        if rx.search(line)
    ]


def test_asset_vocabulary_gate_is_empty():
    """``ada.assets`` is held to the same vocabulary as ``ada.clash``, and for a stronger reason.

    The asset grammar's whole claim is that a provider may keep a source format core has no reader
    for: the manifest carries opaque artefact roles, the build options are forwarded unread, and
    the concepts seam exists so core never learns what it is reading. A comment here naming one
    vendor's format is that claim leaking -- it says the design had a particular source in mind,
    and the next reader writes the next branch for it.

    It is also a PUBLIC package, so a term that names a customer's system or an internal project
    does not belong in it whatever the design says. Two such mentions had already shipped when this
    gate was widened; they are what it is for.
    """
    pattern = "|".join(FORBIDDEN)
    hits = _grep_package(pattern, ASSETS_PKG)
    assert not hits, (
        f"ada.assets names a fabrication/vendor term ({FORBIDDEN}):\n  "
        + "\n  ".join(hits)
        + "\n\nA provider's format is opaque to core by design; describe the SHAPE of a source "
        "(a private format, a catalogue, a system of record), never whose it is."
    )


def test_clash_vocabulary_gate_is_empty():
    pattern = "|".join(FORBIDDEN)
    hits = _grep_package(pattern, CLASH_PKG)
    assert not hits, (
        f"ada.clash names a fabrication/vendor term ({FORBIDDEN}):\n  "
        + "\n  ".join(hits)
        + "\n\nA `Weld` object may appear only in what a generator returns, never in "
        "identification/classification/matching (Decision 10)."
    )


def test_the_gate_can_actually_fail():
    """A gate that cannot fail proves nothing -- these tokens ARE findable in the repo."""
    hits = _grep_package("weld", TOPO_MODEL_PKG)
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
        path = CLASH_PKG / module
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert not re.search(token, text, re.IGNORECASE), f"{module} names {token!r}"

    builtin_specs = (CLASH_PKG / "builtin_specs.py").read_text(encoding="utf-8")
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

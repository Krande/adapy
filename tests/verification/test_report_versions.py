"""The verification report's intro states which adapy built it (issue #452)."""

from __future__ import annotations

import pathlib
import sys

import pytest

pytest.importorskip("paradoc")

_VERIFICATION_DIR = pathlib.Path(__file__).resolve().parents[2] / "verification"
if str(_VERIFICATION_DIR) not in sys.path:
    sys.path.insert(0, str(_VERIFICATION_DIR))

import ada  # noqa: E402
from filters import Versions  # noqa: E402

_INTRO = _VERIFICATION_DIR / "report" / "00-main" / "00-intro" / "00-intro.md"


def test_versions_filter_reports_the_running_adapy():
    assert Versions({}, name="versions").adapy() == ada.__version__


def test_versions_filter_adapy_can_be_supplied():
    assert Versions({"adapy": "1.2.3"}, name="versions").adapy() == "1.2.3"


def test_intro_renders_the_adapy_version():
    # The one markdown behind the web, PDF, ODT and docx outputs (verification/paradoc.toml).
    assert "${ versions.adapy }" in _INTRO.read_text(encoding="utf-8")

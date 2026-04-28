"""Bundle validation tests.

Covers the pre-conversion inspection: which family a zip belongs to,
where the entry-point is, and the various ways a bundle can be
malformed. The actual ada-py conversion path isn't exercised here —
it's tested via the existing convert smoke tests with single-file
inputs, and the bundle path just feeds an unpacked entry into the
same ada loader.
"""

from __future__ import annotations

import io
import pathlib
import zipfile

import pytest

from ada.comms.rest import bundle as bundle_mod
from ada.comms.rest.bundle import BundleError


def _make_zip(entries: dict[str, str]) -> bytes:
    """Build an in-memory zip from a {filename: content} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_clean_abaqus_bundle(tmp_path):
    """Single .inp with two INCLUDEs → entry is the top-level deck."""
    data = _make_zip(
        {
            "job.inp": (
                "*HEADING\n"
                "test\n"
                "*INCLUDE, INPUT=mesh.inp\n"
                "*INCLUDE, INPUT=materials.inp\n"
                "*END\n"
            ),
            "mesh.inp": "*NODE\n1, 0., 0., 0.\n",
            "materials.inp": "*MATERIAL, NAME=steel\n",
        }
    )
    tmp, info = bundle_mod.unpack_and_inspect(data)
    try:
        assert info.family == "abaqus"
        assert info.entry.name == "job.inp"
        names = sorted(p.name for p in info.referenced)
        assert names == ["job.inp", "materials.inp", "mesh.inp"]
    finally:
        tmp.cleanup()


def test_missing_include_is_rejected():
    data = _make_zip(
        {
            "job.inp": "*INCLUDE, INPUT=missing.inp\n",
        }
    )
    with pytest.raises(BundleError, match="include not found"):
        bundle_mod.unpack_and_inspect(data)


def test_absolute_include_path_is_rejected():
    data = _make_zip(
        {
            "job.inp": "*INCLUDE, INPUT=/etc/passwd\n",
            "child.inp": "*NODE\n1, 0., 0., 0.\n",
        }
    )
    with pytest.raises(BundleError, match="absolute"):
        bundle_mod.unpack_and_inspect(data)


def test_traversal_include_path_is_rejected():
    data = _make_zip(
        {
            "job.inp": "*INCLUDE, INPUT=../escape.inp\n",
        }
    )
    with pytest.raises(BundleError, match="traverses"):
        bundle_mod.unpack_and_inspect(data)


def test_backslash_include_path_is_rejected():
    data = _make_zip(
        {
            "job.inp": "*INCLUDE, INPUT=sub\\mesh.inp\n",
            "sub/mesh.inp": "*NODE\n",
        }
    )
    with pytest.raises(BundleError, match="backslashes"):
        bundle_mod.unpack_and_inspect(data)


def test_ambiguous_entry_is_rejected():
    """Two top-level .inp files, neither included by the other."""
    data = _make_zip(
        {
            "job-a.inp": "*HEADING\nA\n",
            "job-b.inp": "*HEADING\nB\n",
        }
    )
    with pytest.raises(BundleError, match="ambiguous entry-point"):
        bundle_mod.unpack_and_inspect(data)


def test_no_entry_is_rejected():
    """Every .inp is included by another → no top-level deck."""
    data = _make_zip(
        {
            "a.inp": "*INCLUDE, INPUT=b.inp\n",
            "b.inp": "*INCLUDE, INPUT=a.inp\n",
        }
    )
    with pytest.raises(BundleError, match="no entry-point"):
        bundle_mod.unpack_and_inspect(data)


def test_mixed_formats_rejected():
    """An IFC and an INP in the same bundle → format mix error."""
    data = _make_zip(
        {
            "job.inp": "*HEADING\n",
            "model.ifc": "ISO-10303-21;\n",
        }
    )
    with pytest.raises(BundleError, match="don't belong"):
        bundle_mod.unpack_and_inspect(data)


def test_genie_only_rejected_for_now():
    """Phase-1 scope is Abaqus only; clear error for the rest."""
    data = _make_zip(
        {
            "model.xml": "<?xml version='1.0'?><a/>\n",
        }
    )
    with pytest.raises(BundleError, match="Abaqus"):
        bundle_mod.unpack_and_inspect(data)


def test_not_a_zip():
    with pytest.raises(BundleError, match="not a valid zip"):
        bundle_mod.unpack_and_inspect(b"plain text, not a zip")


def test_zip_with_absolute_member_rejected():
    """Crafted zip with a leading-/ filename — refuse to unpack."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/evil.inp", "*HEADING\n")
    with pytest.raises(BundleError, match="absolute"):
        bundle_mod.unpack_and_inspect(buf.getvalue())


def test_zip_with_path_traversal_member_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.inp", "*HEADING\n")
    with pytest.raises(BundleError, match="escapes"):
        bundle_mod.unpack_and_inspect(buf.getvalue())


def test_comments_dont_match_include():
    """A `**` comment line that mentions *INCLUDE shouldn't trigger
    the include parser. Real Abaqus decks routinely document things
    in comments."""
    data = _make_zip(
        {
            "job.inp": (
                "** Note: we used to *INCLUDE, INPUT=old.inp here\n"
                "*INCLUDE, INPUT=mesh.inp\n"
            ),
            "mesh.inp": "*NODE\n",
        }
    )
    tmp, info = bundle_mod.unpack_and_inspect(data)
    try:
        assert info.entry.name == "job.inp"
        names = sorted(p.name for p in info.referenced)
        assert names == ["job.inp", "mesh.inp"]
    finally:
        tmp.cleanup()


def test_subdirectory_includes(tmp_path):
    """INCLUDE paths are resolved relative to the file containing them."""
    data = _make_zip(
        {
            "job.inp": "*INCLUDE, INPUT=parts/mesh.inp\n",
            "parts/mesh.inp": "*INCLUDE, INPUT=nodes.inp\n",
            "parts/nodes.inp": "*NODE\n",
        }
    )
    tmp, info = bundle_mod.unpack_and_inspect(data)
    try:
        assert info.entry.name == "job.inp"
        rels = sorted(str(p.relative_to(pathlib.Path(tmp.name))) for p in info.referenced)
        assert "job.inp" in rels
        # The mesh.inp / nodes.inp filenames live under parts/.
        assert any("parts" in r for r in rels)
    finally:
        tmp.cleanup()


def test_empty_zip_rejected():
    data = _make_zip({})
    with pytest.raises(BundleError, match="empty"):
        bundle_mod.unpack_and_inspect(data)

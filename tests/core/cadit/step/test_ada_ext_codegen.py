"""Drift guard for the schema-generated adacpp ADA_EXT_data C++ header.

``ada_ext_schema.h`` (in the sibling adacpp checkout) is generated from
``src/gltf_extension_schema/design_and_analysis_extension.schema.json`` by
``scripts/codegen_ada_ext_cpp.py`` (``pixi run json-code-gen-cpp``). These tests fail with an
actionable "regenerate" message if the schema changed without regenerating, and assert the schema
version is stamped into the header (so producer/consumer drift is a checkable version mismatch).

Skipped when adacpp isn't checked out as a sibling (the header path is absent).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[4]
_GEN = _REPO / "scripts" / "codegen_ada_ext_cpp.py"
_SCHEMA = _REPO / "src" / "gltf_extension_schema" / "design_and_analysis_extension.schema.json"
_HEADER = _REPO.parent / "adacpp" / "src" / "geom" / "neutral" / "ada_ext_schema.h"


def _load_generator():
    spec = importlib.util.spec_from_file_location("codegen_ada_ext_cpp", _GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _HEADER.exists(), reason="adacpp sibling checkout / generated header absent")
def test_ada_ext_header_matches_schema():
    """The committed header must equal a fresh generation — else it's stale vs the schema."""
    expected = _load_generator().generate(_SCHEMA)
    actual = _HEADER.read_text()
    assert actual == expected, "ada_ext_schema.h is stale vs the schema — run `pixi run json-code-gen-cpp`"


@pytest.mark.skipif(not _HEADER.exists(), reason="adacpp sibling checkout / generated header absent")
def test_ada_ext_schema_version_stamped():
    """The schema's version default is stamped into the header (the drift-detection anchor)."""
    version = json.loads(_SCHEMA.read_text())["properties"]["version"]["default"]
    assert f'kSchemaVersion = "{version}"' in _HEADER.read_text()

"""The FEA manifest is written compactly.

It carries per-element tables (element labels and node indices per field
bucket) and group member lists. Pretty-printed with ``indent=2`` each integer
took its own line behind a dozen spaces, and a model-only bake of a large deck
came out about three times the size of the same JSON written compactly. Every
reader parses it as JSON, so only the size changes.
"""

from __future__ import annotations

import json

from ada.fem.results.artefacts import write_manifest


def _manifest(n: int) -> dict:
    bucket = {"element_labels": list(range(1, n + 1)), "element_node_indices": [[i, i + 1] for i in range(n)]}
    return {
        "schema_version": 1,
        "fields": [{"name_canonical": "props.material", "per_type": {"line": bucket}}],
        "groups": [{"name": "a set", "members": [f"EL{i}" for i in range(n)]}],
    }


def test_written_compactly_and_read_back_unchanged(tmp_path):
    manifest = _manifest(1000)
    out = tmp_path / "fea.manifest.json"
    write_manifest(manifest, out)
    raw = out.read_bytes()
    # One line, no indentation, no separator padding, no CR on any platform.
    assert b"\n" not in raw and b"\r" not in raw
    assert b": " not in raw and b", " not in raw
    assert json.loads(raw.decode("utf-8")) == manifest
    # The size the change is for: well under half the indented layout.
    assert len(raw) < len(json.dumps(manifest, indent=2)) / 2

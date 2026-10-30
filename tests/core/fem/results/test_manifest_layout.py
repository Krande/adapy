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


# ── Split manifest (bake_version 6) ──────────────────────────────────────────
#
# The per-model tables leave the manifest: each element field's labels / node
# indices go to a content-addressed detail sidecar, the sets and the node ids to
# their own files, and a stamp carries the freshness keys for the API.


def _split_input(n: int) -> dict:
    def buckets():
        return [
            {
                "elem_type": "line",
                "n_elements": n,
                "n_ips": 2,
                "ip_layout": [{"ip": 0, "layer": "mid", "in_plane": ""}],
                "element_labels": list(range(1, n + 1)),
                "element_node_indices": [[i, i + 1] for i in range(n)],
                "blob": {"url": "fea.F.line.bin", "header_bytes": 1024, "stride_bytes": 8 * n},
                "scalar_range": {"N": [0, 1]},
            }
        ]

    return {
        "version": 2,
        "bake_version": 6,
        "src": "deck.sin",
        "mesh": {"url": "fea.mesh.glb", "n_points": n + 1, "node_labels": list(range(10, n + 11))},
        "fields": [
            {"name_canonical": "N", "per_type": buckets(), "steps": [{"i": 0, "value": 1, "label": "1"}]},
            {"name_canonical": "M", "per_type": buckets(), "steps": [{"i": 0, "value": 1, "label": "1"}]},
            {"name_canonical": "U", "blob": {"url": "fea.U.bin"}, "steps": []},
        ],
        "groups": [{"name": "a set", "members": [f"EL{i}" for i in range(n)]}],
    }


def test_bake_tree_writes_a_compact_index_and_field_detail_sidecars(tmp_path):
    from ada.fem.results.artefacts import STAMP_NAME, read_manifest, write_manifest_tree

    manifest = _split_input(5000)
    written = write_manifest_tree(manifest, tmp_path)
    names = [p.name for p in written]
    # Sidecars first, then the manifest, then the stamp that vouches for it.
    assert names[-2:] == ["fea.manifest.json", STAMP_NAME]
    details = [n for n in names if n.startswith("fea.detail.")]
    # Two fields over one mesh share ONE content-addressed detail file.
    assert len(details) == 1
    assert {"fea.groups.json", "fea.nodelabels.json"} <= set(names)

    index = json.loads((tmp_path / "fea.manifest.json").read_text())
    for f in index["fields"][:2]:
        assert f["detail"] == {"url": details[0], "version": 1}
        (bucket,) = f["per_type"]
        assert "element_labels" not in bucket and "element_node_indices" not in bucket
        # What the pickers read synchronously stays in the index.
        assert bucket["ip_layout"] and bucket["blob"]["url"] == "fea.F.line.bin" and bucket["n_elements"] == 5000
    assert "detail" not in index["fields"][2]
    assert "groups" not in index and index["groups_url"] == "fea.groups.json" and index["n_groups"] == 1
    assert "node_labels" not in index["mesh"] and index["mesh"]["node_labels_url"] == "fea.nodelabels.json"
    # The index is a small fraction of the inline manifest.
    inline_size = len(json.dumps(manifest, separators=(",", ":")))
    assert (tmp_path / "fea.manifest.json").stat().st_size < inline_size / 10

    detail = json.loads((tmp_path / details[0]).read_text())
    assert detail["per_type"][0]["element_labels"][:3] == [1, 2, 3]
    stamp = json.loads((tmp_path / STAMP_NAME).read_text())
    assert stamp["bake_version"] == 6 and stamp["src"] == "deck.sin" and stamp["n_fields"] == 3

    # read_manifest folds every sidecar back: the inline manifest, exactly.
    assert read_manifest(tmp_path) == manifest
    assert read_manifest(tmp_path, inline=False) == index


def test_split_leaves_the_input_and_an_inline_free_manifest_alone():
    from ada.fem.results.artefacts import split_manifest

    manifest = _split_input(10)
    before = json.dumps(manifest)
    index, sidecars = split_manifest(manifest)
    assert json.dumps(manifest) == before  # input not mutated
    again, more = split_manifest(index)
    assert again == index and more == {}


def test_legacy_inline_manifest_reads_unchanged(tmp_path):
    from ada.fem.results.artefacts import read_manifest

    manifest = _split_input(10)
    write_manifest(manifest, tmp_path / "fea.manifest.json")
    assert read_manifest(tmp_path) == manifest

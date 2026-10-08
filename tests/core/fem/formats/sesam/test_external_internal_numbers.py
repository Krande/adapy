"""Sesam decks whose external node and element numbers are not the internal ones.

A Sesam file numbers every node and element twice (Input Interface File, GNODE and
GELMNT1): the EXTERNAL number, "specified or controlled by the user", which GeniE and
Xtract show, and the INTERNAL one, "program defined", running 1..n. Every other record
refers by the internal number: GCOORD, GELMNT1's node references, GELREF1, GSETMEMB,
the B* boundary/load records, and all results (RVNODDIS, RVSTRESS, RVFORCES, RDPOINTS).

On most decks the two agree. On a deck whose external numbers are offset from the
internal ones, the reader used to key the nodes by their external numbers while the
elements and results referred to internal ones: the viewer bake failed ("references
unknown node id") and node lookups by an element's references failed.

The rule: the mesh is keyed by the internal numbers (what the file links by) and
carries the external numbers as labels, which the bake hands the viewer.

The decks here are the two-element shell fixture with its GNODE/GELMNT1 external
numbers rewritten, plus a named element set and node set: once offset, and once
permuted so that each external number is another entity's internal number -- the case
where mixing them up gives a wrong answer instead of an error.
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.formats.sesam.write.write_utils import write_ff

_FIXTURE = "sesam/2EL_SHELL_R1.SIF"

# internal -> external
_NUMBERINGS = {
    "offset": ({n: 1000 + n for n in range(1, 7)}, {1: 5001, 2: 5002}),
    "permuted": ({n: 7 - n for n in range(1, 7)}, {1: 2, 2: 1}),
}


def _field(value: float) -> str:
    return f"{value:16.8E}"


def _sets() -> str:
    # Set 1: element 1 (internal). Set 2: node 1 (internal).
    text = write_ff("TDSETNAM", [(4, 1, 100 + len("plate_one"), 0), ("plate_one",)])
    text += write_ff("GSETMEMB", [(6, 1, 1, 2), (0, 1)])
    text += write_ff("TDSETNAM", [(4, 2, 100 + len("corner"), 0), ("corner",)])
    text += write_ff("GSETMEMB", [(6, 2, 1, 1), (0, 1)])
    return text


def _renumbered(text: str, nodes: dict[int, int], elements: dict[int, int]) -> str:
    """The deck with GNODE NODEX and GELMNT1 ELNOX replaced, and the sets added."""
    out = []
    for line in text.splitlines(keepends=True):
        if line.startswith("GNODE"):
            nodeno = int(float(line[24:40]))
            line = line[:8] + _field(nodes[nodeno]) + line[24:]
        elif line.startswith("GELMNT1"):
            elno = int(float(line[24:40]))
            line = line[:8] + _field(elements[elno]) + line[24:]
        elif line.startswith("GELREF1") and not any(x.startswith("TDSETNAM") for x in out):
            out.append(_sets())
        out.append(line)
    return "".join(out)


@pytest.fixture(params=sorted(_NUMBERINGS))
def decks(request, fem_files, tmp_path):
    src = fem_files / _FIXTURE
    if not src.exists():
        pytest.skip(f"fixture not present: {_FIXTURE}")
    nodes, elements = _NUMBERINGS[request.param]
    text = src.read_text()
    same = tmp_path / "same_R1.SIF"
    same.write_text(_renumbered(text, {n: n for n in nodes}, {e: e for e in elements}))
    other = tmp_path / "other_R1.SIF"
    other.write_text(_renumbered(text, nodes, elements))
    return same, other, nodes, elements


def test_mesh_is_keyed_by_internal_numbers_and_labelled_with_external(decks):
    same, other, nodes, elements = decks
    ref = read_sif_file(same).mesh
    mesh = read_sif_file(other).mesh

    # Same keys and connectivity as the deck whose numberings agree.
    np.testing.assert_array_equal(mesh.nodes.identifiers, ref.nodes.identifiers)
    np.testing.assert_array_equal(mesh.nodes.coords, ref.nodes.coords)
    assert [b.identifiers.tolist() for b in mesh.elements] == [b.identifiers.tolist() for b in ref.elements]
    assert [b.node_refs.tolist() for b in mesh.elements] == [b.node_refs.tolist() for b in ref.elements]

    # Every node an element refers to resolves, to the right coordinates.
    for block in mesh.elements:
        for refs in block.node_refs:
            resolved = mesh.nodes.get_node_by_id(list(refs))
            expected = ref.nodes.get_node_by_id(list(refs))
            assert [tuple(n.p) for n in resolved] == [tuple(n.p) for n in expected]

    # The numbers people read.
    assert ref.nodes.labels is None and all(b.labels is None for b in ref.elements)
    assert ref.node_label_map() is None and ref.element_label_map() is None
    assert mesh.node_label_map() == nodes
    assert mesh.element_label_map() == elements
    assert mesh.nodes.shown_ids().tolist() == [nodes[int(i)] for i in mesh.nodes.identifiers]


def test_results_and_sets_map_to_the_right_entities(decks):
    same, other, nodes, elements = decks
    ref = read_sif_file(same)
    res = read_sif_file(other)

    def by_name(result):
        return {(r.name, str(getattr(r, "elem_type", None)), r.step): np.asarray(r.values) for r in result.results}

    ref_fields, fields = by_name(ref), by_name(res)
    assert fields.keys() == ref_fields.keys()
    for key, values in fields.items():
        # Rows keyed by internal numbers, values unchanged: a nodal field lands on the
        # node the deck computed it for.
        np.testing.assert_array_equal(values, ref_fields[key], err_msg=str(key))

    disp = fields[("RVNODDIS", "None", 1)]
    assert set(disp[:, 0].astype(int)) == set(res.mesh.nodes.identifiers.tolist())

    # Sets list internal members, as GSETMEMB does.
    assert res.mesh.sets["plate_one"]._member_ids == [1]
    assert res.mesh.sets["corner"]._member_ids == [1]


def test_sin_reader_keys_by_internal_numbers(fem_files, monkeypatch):
    """The SIN reader decodes GNODE/GELMNT1 itself; same rule."""
    import ada.fem.formats.sesam.results.read_sin as read_sin
    from ada.fem.formats.sesam.read import cards

    src = fem_files / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
    if not src.exists():
        pytest.skip("fixture not present")

    ref = read_sin.read_sin_file(src, step=1)
    original = read_sin._records_for

    def offset(sin, card, **kwargs):
        rows = original(sin, card, **kwargs)
        if card is cards.GNODE or card is cards.GELMNT1:
            rows = [[row[0] + 100000, *row[1:]] for row in rows]
        return rows

    monkeypatch.setattr(read_sin, "_records_for", offset)
    res = read_sin.read_sin_file(src, step=1)

    np.testing.assert_array_equal(res.mesh.nodes.identifiers, ref.mesh.nodes.identifiers)
    assert res.mesh.node_label_map() == {int(i): int(i) + 100000 for i in ref.mesh.nodes.identifiers}
    ref_elems = np.concatenate([b.identifiers for b in ref.mesh.elements])
    assert res.mesh.element_label_map() == {int(i): int(i) + 100000 for i in ref_elems}
    ref_fields = {(r.name, str(getattr(r, "elem_type", None))): np.asarray(r.values) for r in ref.results}
    for r in res.results:
        np.testing.assert_array_equal(np.asarray(r.values), ref_fields[(r.name, str(getattr(r, "elem_type", None)))])


def test_bake_labels_are_external_numbers(decks, tmp_path):
    from ada.fem.formats.sesam.results.sif_stream import SifStreamReader
    from ada.fem.results.artefacts import ELEM_HEADER_BYTES, bake_artefacts

    same, other, nodes, elements = decks
    out = tmp_path / "bake"
    with SifStreamReader(other) as reader:
        bake_artefacts(reader, out, src="deck")
    manifest = json.loads((out / "fea.manifest.json").read_text())

    # Node labels in point order (GCOORD order = internal 1..n).
    assert manifest["mesh"]["node_labels"] == [nodes[n] for n in range(1, 7)]

    # The per-element draw ranges the viewer picks by.
    data = (out / "fea.mesh.elements.bin").read_bytes()
    n_elements = struct.unpack("<I", data[8:12])[0]
    labels = np.frombuffer(data[ELEM_HEADER_BYTES:], dtype=np.uint32).reshape(n_elements, 3)[:, 0]
    assert sorted(labels.tolist()) == sorted(elements.values())

    # Element fields: values follow their labels.
    element_fields = [f for f in manifest["fields"] if f.get("per_type")]
    assert element_fields
    for field in element_fields:
        for bucket in field["per_type"]:
            assert set(bucket["element_labels"]) <= set(elements.values()), field["name_canonical"]

    # Named sets, as the Groups picker lists them.
    groups = {(g["name"], g["fe_object_type"]): g["members"] for g in manifest["groups"]}
    assert groups[("plate_one", "element")] == [f"EL{elements[1]}"]
    assert groups[("corner", "node")] == [f"P{nodes[1]}"]


def test_bake_element_values_follow_their_labels(decks, tmp_path):
    """An element field's value for label L is the value the deck stores for the element
    whose external number is L."""
    from ada.fem.formats.sesam.results.sif_stream import SifStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    same, other, nodes, elements = decks
    manifests = {}
    blobs = {}
    for tag, path in (("same", same), ("other", other)):
        out = tmp_path / tag
        with SifStreamReader(path) as reader:
            bake_artefacts(reader, out, src="deck")
        manifests[tag] = json.loads((out / "fea.manifest.json").read_text())
        blobs[tag] = out

    def bucket_values(tag, field_name):
        field = next(f for f in manifests[tag]["fields"] if f["name_canonical"] == field_name)
        bucket = field["per_type"][0]
        raw = (blobs[tag] / bucket["blob"]["url"]).read_bytes()[bucket["blob"]["header_bytes"] :]
        values = np.frombuffer(raw, dtype=np.float32)[: bucket["blob"]["stride_bytes"] // 4]
        per_elem = values.reshape(bucket["n_elements"], -1)
        return dict(zip(bucket["element_labels"], per_elem.tolist()))

    name = next(f["name_canonical"] for f in manifests["same"]["fields"] if f.get("per_type"))
    ref = bucket_values("same", name)  # keyed by internal == external
    got = bucket_values("other", name)
    assert got == {elements[int(k)]: v for k, v in ref.items()}

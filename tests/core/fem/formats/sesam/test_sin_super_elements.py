"""Superelement assemblies in a SIN: the default entry, the hierarchy, selection.

A SIN written for a superelement assembly holds the whole assembly: one RESULTS
entry per superelement, plus the hierarchy records on the top level's entry —
which holds no mesh and no results. The reader used to open whichever entry's
PTAB allocated the most slots, the first of a tie; on a superelement assembly
SIN whose top-level entry allocated as many as the entry with the mesh, it
opened the hierarchy and found nothing to read.

The assembly used here is spliced together at test time from the committed
single-superelement cantilever (see ``sin_assembly.py``): a top level of type
100 over two copies of the cantilever as types 10 and 11, the second without
stresses.
"""

from __future__ import annotations

import numpy as np
import pytest

from .sin_assembly import (
    PLACEMENT_ROWS,
    SIN_PATH,
    write_assembly_sin,
    write_header_only_sin,
    write_stub_tie_sin,
)

LABELS = ["R100", "SEL10.IND1", "SEL11.IND1"]


@pytest.fixture(scope="module")
def assembly(tmp_path_factory):
    return write_assembly_sin(tmp_path_factory.mktemp("se") / "ASSEMBLY.SIN")


def _displacements(result):
    by_name = {r.name: r for r in result.results}
    assert "sesam.nodes.displacement" in by_name, f"no displacement field among {sorted(by_name)}"
    return by_name["sesam.nodes.displacement"].values


def test_default_pick_skips_a_hierarchy_only_entry(tmp_path):
    """Two entries allocating the same PTAB slots, the first listing only
    HIERARCH: the entry with elements and results is the one opened."""
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(write_header_only_sin(tmp_path / "TIE.SIN")) as sf:
        assert [iref for iref, _ in sf.super_element_refs] == [1, 2]
        assert sf._active_iref == 2
        assert {"GELMNT1", "RVNODDIS"} <= set(sf.type_blocks)


def test_default_pick_on_an_assembly_is_the_first_level_superelement_with_results(assembly):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(assembly) as sf:
        assert len(sf.super_element_refs) == 3
        assert sf._active_iref == 2
        assert "RVSTRESS" in sf.type_blocks
        assert sf.selected.label == "SEL10.IND1"

    original, chosen = read_sin_file(SIN_PATH), read_sin_file(assembly, super_element="SEL10.IND1")
    assert len(chosen.mesh.nodes.coords) == len(original.mesh.nodes.coords)
    assert sum(len(b.identifiers) for b in chosen.mesh.elements) == sum(
        len(b.identifiers) for b in original.mesh.elements
    )


# ── the whole assembly: read_sin_file's default on an assembly ─────────────────


@pytest.fixture(scope="module")
def assembled(assembly):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    return read_sin_file(assembly)


def test_default_read_of_an_assembly_merges_every_superelement(assembled):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    one = read_sin_file(SIN_PATH)
    n_nodes, n_elems = len(one.mesh.nodes.coords), sum(len(b.identifiers) for b in one.mesh.elements)

    assert len(assembled.mesh.nodes.coords) == 2 * n_nodes
    assert sum(len(b.identifiers) for b in assembled.mesh.elements) == 2 * n_elems
    assert [s["label"] for s in assembled.sesam_super_elements] == ["SEL10.IND1", "SEL11.IND1"]


def test_ids_are_unique_and_readable_per_superelement(assembled):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    one = read_sin_file(SIN_PATH)
    ids = np.asarray(assembled.mesh.nodes.identifiers)
    assert len(np.unique(ids)) == len(ids)
    # Type x 10^6 + the superelement's own number.
    n = len(one.mesh.nodes.identifiers)
    assert np.array_equal(ids[:n], np.asarray(one.mesh.nodes.identifiers) + 10_000_000)
    assert np.array_equal(ids[n:], np.asarray(one.mesh.nodes.identifiers) + 11_000_000)
    elem_labels = np.concatenate([b.shown_ids() for b in assembled.mesh.elements])
    assert len(np.unique(elem_labels)) == len(elem_labels)


def test_each_superelement_is_placed_by_its_transform(assembled):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    xyz = np.asarray(read_sin_file(SIN_PATH).mesh.nodes.coords)
    placed = np.asarray(assembled.mesh.nodes.coords)
    n = len(xyz)
    matrix = np.array(PLACEMENT_ROWS)
    np.testing.assert_allclose(placed[:n], xyz)  # SEL10: identity
    np.testing.assert_allclose(placed[n:], xyz @ matrix[:3, :3].T + matrix[:3, 3])


def test_displacements_turn_with_the_superelement(assembled):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    one = read_sin_file(SIN_PATH)
    raw = next(r for r in one.results if r.name == "RVNODDIS" and r.step == one.get_steps()[0])
    merged = next(r for r in assembled.results if r.name == "RVNODDIS" and r.step == raw.step)
    n = len(raw.values)
    rotation = np.array(PLACEMENT_ROWS)[:3, :3]
    np.testing.assert_allclose(merged.values[:n, 1:], raw.values[:, 1:])
    np.testing.assert_allclose(merged.values[n:, 1:4], raw.values[:, 1:4] @ rotation.T)  # translations
    np.testing.assert_allclose(merged.values[n:, 4:7], raw.values[:, 4:7] @ rotation.T)  # rotations


def test_from_fem_res_takes_a_selection_of_superelements(assembly, assembled):
    import ada
    from ada.fem.formats.sesam.results.sin_reader import SuperElementError

    def n_nodes(result):
        return len(result.mesh.nodes.identifiers)

    whole = n_nodes(assembled)
    # A list (or set, or tuple of names) of superelements: just those, merged.
    assert n_nodes(ada.from_fem_res(assembly, super_element=["SEL10.IND1", "SEL11.IND1"])) == whole
    assert n_nodes(ada.from_fem_res(assembly, super_element=("SEL10.IND1", "SEL11.IND1"))) == whole
    # One superelement, however it is spelled, reads alone with its own numbering.
    alone = ada.from_fem_res(assembly, super_element="SEL11.IND1")
    assert n_nodes(alone) == whole // 2
    assert int(np.asarray(alone.mesh.nodes.identifiers).max()) < 10_000_000
    assert n_nodes(ada.from_fem_res(assembly, super_element=["SEL11.IND1"])) == whole // 2
    # A (type, instance) pair is ONE superelement, not two.
    assert n_nodes(ada.from_fem_res(assembly, super_element=(11, 1))) == whole // 2
    # A name that is not in the file is refused, listing what is.
    with pytest.raises(SuperElementError, match="SEL10.IND1"):
        ada.from_fem_res(assembly, super_element=["SEL10.IND1", "SEL99.IND1"])


def test_a_property_field_says_which_superelement_each_element_belongs_to(assembled):
    fields = [r for r in assembled.results if r.name == "props.super_element"]
    assert fields, "no props.super_element field"
    codes_by_label = {}
    for f in fields:
        for label, code in zip(np.asarray(f.values)[:, 0], np.asarray(f.values)[:, 2]):
            codes_by_label[int(label)] = int(code)
        assert dict(f.presentation.value_labels) == {1.0: "SEL10.IND1", 2.0: "SEL11.IND1"}
    elem_ids = [int(i) for b in assembled.mesh.elements for i in b.identifiers]
    assert sorted(codes_by_label) == sorted(elem_ids)
    assert {codes_by_label[i] for i in elem_ids if i < 11_000_000} == {1}
    assert {codes_by_label[i] for i in elem_ids if i >= 11_000_000} == {2}


def test_material_ids_and_their_legend_are_prefixed_per_superelement(assembled):
    materials = [r for r in assembled.results if r.name == "props.material"]
    assert materials
    for f in materials:
        values = np.asarray(f.values)
        legend = dict(f.presentation.value_labels)
        # Every painted id has a legend entry, and no superelement's ids collide with another's.
        assert set(np.unique(values[:, 2])) <= set(legend)
        assert any(v >= 11_000_000 for v in legend) and any(10_000_000 <= v < 11_000_000 for v in legend)


def test_one_field_per_step_with_a_superelement_lacking_it_contributing_nothing(assembled):
    by_key = {}
    for r in assembled.results:
        key = (r.name, r.step, str(getattr(r, "elem_type", None)))
        assert key not in by_key, f"{key} appears twice"
        by_key[key] = r
    # SEL11 has no RVSTRESS: the stress field holds SEL10's elements only.
    stress = [r for r in assembled.results if r.name == "STRESS"]
    assert stress and all((np.asarray(r.values)[:, 0] < 11_000_000).all() for r in stress)


def test_hierarchy_lists_every_superelement(assembly):
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(assembly) as sf:
        rows = sf.hierarchy()
        assert sf.is_assembly()
        # Listing the hierarchy decodes no entry and leaves the active one alone.
        assert sf._active_iref == 2 and set(sf.super_elements) == {2}

    assert [r.label for r in rows] == LABELS
    top, first, second = rows
    assert [r.iref for r in rows] == [1, 2, 3]
    assert [r.seltyp for r in rows] == [100, 10, 11]
    assert [r.level for r in rows] == [2, 1, 1]
    assert top.is_top and top.children == (2, 3) and top.transform is None
    assert first.parent == second.parent == 1
    assert [r.has_mesh for r in rows] == [False, True, True]
    assert [r.has_results for r in rows] == [False, True, True]
    assert ["RVSTRESS" in r.block_names for r in rows] == [False, True, False]
    assert top.block_names == {"HIERARCH", "HSUPSTAT", "HSUPTRAN"}
    assert (first.element_count, first.node_count) == (360, 403)
    assert (top.element_count, top.node_count) == (0, 0)
    assert first.transform == tuple(np.eye(4).ravel().tolist())


def test_placement_is_a_row_major_matrix(assembly):
    """HSUPTRAN stores the 4x4 matrix column by column; the row reads it as a
    row-major matrix, so the translation is its last column."""
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(assembly) as sf:
        second = sf.hierarchy()[2]
    matrix = np.reshape(second.transform, (4, 4))
    assert np.array_equal(matrix, np.array(PLACEMENT_ROWS))
    assert np.array_equal(matrix @ [1.0, 0.0, 0.0, 1.0], [10.0, 21.0, 30.0, 1.0])


def test_default_pick_skips_an_entry_of_empty_stubs(tmp_path):
    """A summary-only entry listing GELMNT1 and RVNODDIS without records, in a
    PTAB listing more blocks than the dense entry's: still the dense entry
    is opened, and the file is not an assembly."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_metadata
    from ada.fem.formats.sesam.results.sin_reader import SuperElementError, open_sin

    path = write_stub_tie_sin(tmp_path / "STUBS.SIN")
    with open_sin(path) as sf:
        assert sf._active_iref == 2
        assert sf.get_count("GELMNT1") == 1
        rows = sf.hierarchy()
        assert not sf.is_assembly() and sf.selected is None
        # Both entries say type 1: the labels still differ, and the type alone
        # names the one with elements.
        assert [(r.label, r.iref, r.has_mesh) for r in rows] == [("SEL1.IND1", 1, False), ("SEL1.IND2", 2, True)]
        assert sf.resolve_super_element(1).iref == 2
        with pytest.raises(SuperElementError, match="SEL1.IND1 has no elements"):
            sf.resolve_super_element("SEL1.IND1")
    meta = read_sin_metadata(path)
    assert meta.super_element is None and meta.element_count == 1


def test_entries_without_ident_are_listed_by_iref_and_not_an_assembly(tmp_path):
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(write_stub_tie_sin(tmp_path / "NOIDENT.SIN", ident=False)) as sf:
        assert sf._active_iref == 2
        assert [r.label for r in sf.hierarchy()] == ["SEL1.IND1", "SEL2.IND1"]
        assert not sf.is_assembly() and sf.selected is None


def test_repeated_instances_map_to_their_own_entries(tmp_path):
    """Two instances of one type, each with its own RESULTS entry: every
    instance gets an entry of that type in turn, and no label repeats."""
    from ada.fem.formats.sesam.results.sin_reader import SuperElementError, open_sin

    path = write_assembly_sin(tmp_path / "REPEAT.SIN", seltyps=(10, 10), instances=(1, 2))
    with open_sin(path) as sf:
        rows = sf.hierarchy()
        assert [(r.label, r.iref) for r in rows] == [("R100", 1), ("SEL10.IND1", 2), ("SEL10.IND2", 3)]
        assert sf.resolve_super_element("SEL10.IND2").iref == 3
        with pytest.raises(SuperElementError, match="has 2 instances"):
            sf.resolve_super_element(10)


def test_a_single_superelement_sin_is_not_an_assembly():
    from ada.fem.formats.sesam.results.read_sin import read_sin_metadata
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(SIN_PATH) as sf:
        (row,) = sf.hierarchy()
        assert not sf.is_assembly()
        assert sf.selected is None
        assert sf.resolve_super_element(1) == row
    assert row.ihref is None and row.iref == 1 and row.index == 1 and row.level == 1
    assert row.label == f"SEL{row.seltyp}.IND1"

    meta = read_sin_metadata(SIN_PATH)
    assert meta.super_elements == (row,)
    assert meta.super_element is None
    assert meta.node_count == 403 and meta.element_count == 360
    assert meta.field_steps == {"RVNODDIS": [1], "RVSTRESS": [1]}


@pytest.mark.parametrize("spec", [10, (10, 1), "SEL10.IND1", "sel10.ind1", "10.1", "10", " SEL 10 . IND 1 "])
def test_every_spelling_selects_the_same_superelement(assembly, spec):
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(assembly) as sf:
        info = sf.resolve_super_element(spec)
        assert info.label == "SEL10.IND1" and info.iref == 2
        assert sf.resolve_super_element(info) == info


@pytest.mark.parametrize(
    "spec, reason",
    [
        ("R100", "top level"),
        (100, "top level"),
        (99, "no superelement"),
        ("SEL10.IND2", "no superelement"),
        ("ten", "not a superelement"),
        (True, "not a superelement"),
    ],
)
def test_a_selection_naming_nothing_readable_lists_the_alternatives(assembly, spec, reason):
    from ada.fem.formats.sesam.results.sin_reader import SuperElementError, open_sin

    with open_sin(assembly) as sf:
        with pytest.raises(SuperElementError, match=reason) as err:
            sf.select_super_element(spec)
        assert sf._active_iref == 2  # a refused selection changes nothing
    message = str(err.value)
    for label in LABELS:
        assert label in message
    assert "SEL10.IND1: 360 elements, results: yes" in message
    assert "R100: 0 elements, results: no (top level, hierarchy only)" in message
    assert [r.label for r in err.value.alternatives] == LABELS
    assert isinstance(err.value, ValueError)


def test_open_sin_reads_the_chosen_superelement(assembly):
    from ada.fem.formats.sesam.results.sin_reader import SuperElementError, open_sin

    with open_sin(assembly, super_element=11) as sf:
        assert sf._active_iref == 3
        # The default entry is not decoded on the way to the chosen one.
        assert set(sf.super_elements) == {3}
        assert "RVSTRESS" not in sf.type_blocks and "RVNODDIS" in sf.type_blocks
        assert sf.selected.label == "SEL11.IND1"
        sf.use_super_element(2)
        assert sf.selected.label == "SEL10.IND1"
    with pytest.raises(SuperElementError, match="R100"):
        open_sin(assembly, super_element="R100")


def test_metadata_describes_the_chosen_superelement(assembly):
    from ada.fem.formats.sesam.results.read_sin import read_sin_metadata

    default = read_sin_metadata(assembly)
    first = read_sin_metadata(assembly, super_element="SEL10.IND1")
    second = read_sin_metadata(assembly, super_element="SEL11.IND1")
    assert default.super_element == first.super_element == "SEL10.IND1"
    assert second.super_element == "SEL11.IND1"
    assert first.field_steps == read_sin_metadata(SIN_PATH).field_steps
    assert second.field_steps == {"RVNODDIS": [1]}
    assert [r.label for r in second.super_elements] == LABELS
    assert (second.node_count, second.element_count) == (403, 360)


@pytest.mark.parametrize("spec", ["SEL10.IND1", "SEL11.IND1"])
def test_full_read_of_either_superelement_matches_the_original(assembly, spec):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    original = read_sin_file(SIN_PATH)
    chosen = read_sin_file(assembly, super_element=spec)
    assert np.array_equal(chosen.mesh.nodes.coords, original.mesh.nodes.coords)
    assert np.array_equal(_displacements(chosen), _displacements(original))
    assert chosen.sesam_case_names == original.sesam_case_names


def test_step_readers_take_the_superelement(assembly):
    from ada.fem.formats.sesam.results.read_sin import (
        SinStreamReader,
        iter_sin_step_results,
    )
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    ((_, original),) = list(iter_sin_step_results(SIN_PATH, [1]))
    ((step, chosen),) = list(iter_sin_step_results(assembly, [1], super_element=10))
    assert step == 1
    assert np.array_equal(_displacements(chosen), _displacements(original))
    assert sorted(r.name for r in chosen.results) == sorted(r.name for r in original.results)

    with SinStreamReader(open_sin(assembly), super_element="SEL11.IND1") as reader:
        assert reader.sin._active_iref == 3
        streamed = reader._load_step(1)
    assert np.array_equal(_displacements(streamed), _displacements(original))


def test_stream_reader_factory_takes_the_superelement(assembly):
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import make_stream_reader

    with make_stream_reader(assembly, steps=[1], super_element=11) as reader:
        assert isinstance(reader, SinStreamReader)
        assert reader.sin._active_iref == 3
    with make_stream_reader(assembly, super_element="SEL11.IND1") as reader:
        # The full-materialise adapter: SEL11.IND1 has no stresses.
        names = {spec.name for spec in reader.field_specs()}
        assert names and not any("STRESS" in n.upper() for n in names)


def test_writers_take_the_superelement(assembly, tmp_path):
    from ada.fem.formats.sesam.results.export_fem import (
        export_fem_from_sin,
        export_fem_text,
    )
    from ada.fem.formats.sesam.results.sin_to_sif import (
        convert_sin_to_sif_file,
        convert_sin_to_sif_text,
    )

    def without_ident(text):
        return [line for line in text.splitlines() if not line.startswith("IDENT")]

    original = export_fem_text(SIN_PATH)
    for spec in (10, "SEL11.IND1"):
        chosen = export_fem_text(assembly, super_element=spec)
        # The copies differ from the original only by the IDENT that types them.
        assert without_ident(chosen) == without_ident(original)
    fem = export_fem_from_sin(assembly, tmp_path / "chosen.FEM", super_element=11)
    assert without_ident(fem.read_text(encoding="ascii")) == without_ident(original)

    sif = convert_sin_to_sif_text(assembly, super_element="SEL10.IND1")
    assert without_ident(sif) == without_ident(convert_sin_to_sif_text(SIN_PATH))
    assert "RVSTRESS" not in convert_sin_to_sif_text(assembly, super_element=11)
    out = convert_sin_to_sif_file(assembly, tmp_path / "chosen.SIF", super_element=11)
    assert without_ident(out.read_text()) == without_ident(convert_sin_to_sif_text(assembly, super_element=11))


def test_a_single_superelement_sin_reads_as_before():
    """Nothing changes for a SIN with one superelement: the same entry, the
    same blocks, and a selection of its own type is a no-op."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(SIN_PATH) as plain, open_sin(SIN_PATH, super_element=1) as chosen:
        assert plain._active_iref == chosen._active_iref == 1
        assert list(plain.type_blocks) == list(chosen.type_blocks)
    a, b = read_sin_file(SIN_PATH), read_sin_file(SIN_PATH, super_element="SEL1.IND1")
    assert np.array_equal(_displacements(a), _displacements(b))

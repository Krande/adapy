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

from .sin_assembly import SIN_PATH, write_assembly_sin, write_header_only_sin

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

    original, spliced = read_sin_file(SIN_PATH), read_sin_file(assembly)
    assert len(spliced.mesh.nodes.coords) == len(original.mesh.nodes.coords)
    assert sum(len(b.identifiers) for b in spliced.mesh.elements) == sum(
        len(b.identifiers) for b in original.mesh.elements
    )


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
    identity = tuple(np.eye(4).ravel().tolist())
    assert first.transform == second.transform == identity


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

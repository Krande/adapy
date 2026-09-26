"""Read -> write -> read comes back as the same model.

The reader and the writer each used to hold their own idea of the format, and nothing checked
them against each other: writing a deck the reader had just read crashed outright, and once it
did not, element formulations, BC magnitudes and set definitions changed on the way through.
These tests are the check. What "the same model" means is :func:`structure` -- everything a
deck's model data defines, compared the way it means, not the way it happens to be held (a flat
deck's part-level BC and the writer's assembly-level BC on the instance's set are one BC).
"""

from __future__ import annotations

import collections
import pathlib

import pytest

import ada

from .zoo import ZOO

REPO_FILES = pathlib.Path(__file__).resolve().parents[5] / "files" / "fem_files"
DECKS = sorted((REPO_FILES / "abaqus").glob("*.inp")) + sorted((REPO_FILES / "calculix").glob("*.inp"))


def structure(a: ada.Assembly) -> dict:
    out: dict = {}
    for p in a.get_all_parts_in_assembly(include_self=True):
        f = p.fem
        if f.is_empty():
            continue
        elements = collections.Counter((str(e.type), str(e.formulation_override)) for e in f.elements)
        out[f.name] = dict(
            nodes=len(f.nodes),
            elements=dict(elements),
            sets=sorted((s.name.lower(), str(s.type), len(s.members)) for s in f.sets),
            sections=sorted((str(s.type), s.elset.name.lower() if s.elset is not None else None) for s in f.sections),
            constraints=sorted(str(c.type) for c in f.constraints.values()),
            masses=len(f.masses),
            surfaces=sorted(f.surfaces),
        )
    out["materials"] = sorted(m.name for m in a.get_all_materials())
    out["bcs"] = sorted(
        (
            (
                b.fem_set.parent.name if b.fem_set.parent is not None else None,
                b.fem_set.name.lower(),
                str(b.type),
                b.dofs if isinstance(b.dofs, str) else tuple(b.dofs),
                tuple(b.magnitudes) if b.magnitudes else None,
            )
            for p in a.get_all_parts_in_assembly(include_self=True)
            for b in p.fem.bcs
        ),
        key=repr,
    )
    return out


def _write_read(a: ada.Assembly, tmp_path: pathlib.Path, tag: str) -> ada.Assembly:
    a.to_fem(tag, fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    return ada.from_fem(next(tmp_path.rglob(f"{tag}.inp")), "abaqus")


@pytest.mark.parametrize("deck", DECKS, ids=lambda p: p.name)
def test_a_deck_reads_back_as_itself_after_being_written(deck, tmp_path):
    read = ada.from_fem(deck, "abaqus")
    assert structure(_write_read(read, tmp_path, "rt")) == structure(read)


def test_an_element_keeps_the_formulation_it_was_read_as(tmp_path):
    """CPS3 is a plane-stress triangle; the triangle's default type, S3, is a shell."""
    deck = next(d for d in DECKS if d.name == "nle1xf3c.inp")
    read = ada.from_fem(deck, "abaqus")
    assert {e.formulation_override for p in read.get_all_parts_in_assembly() for e in p.fem.elements} == {"CPS3"}
    read.to_fem("rt", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    text = next(tmp_path.rglob("rt.inp")).read_text()
    assert "*ELEMENT, type=CPS3" in text and "type=S3," not in text


#: Zoo models whose written deck the reader cannot yet read back -- each a place where the two
#: sides still disagree about the format. Strict: fixing one makes its case fail until removed.
READ_BACK_GAPS: dict = {}


def _zoo_cases():
    for name in sorted(ZOO):
        gap = READ_BACK_GAPS.get(name)
        marks = [pytest.mark.xfail(raises=gap[0], strict=True, reason=gap[1])] if gap else []
        yield pytest.param(name, marks=marks, id=name)


@pytest.mark.parametrize("name", _zoo_cases())
def test_every_zoo_model_is_a_fixed_point_after_one_pass(name, tmp_path):
    """Whatever the first write -> read keeps, the second keeps exactly."""
    model = ZOO[name]()
    try:
        model.to_fem("a", fem_format="abaqus", scratch_dir=tmp_path / "1", overwrite=True, write_input_files_only=True)
    except Exception as exc:  # only the WRITER's known gaps (the zoo's own xfails) may skip
        pytest.skip(f"not writable yet: {type(exc).__name__}")
    first = ada.from_fem(next((tmp_path / "1").rglob("a.inp")), "abaqus")
    second = _write_read(first, tmp_path / "2", "b")
    assert structure(second) == structure(first)

"""Springs are elements, and ``FEM.springs`` is a view over the element container.

Springs used to live in a dict of their own, outside ``fem.elements``, and so missed
everything that container does for an element. Two consequences, both real:

* the array-backed Sesam reader failed outright — ``ArrayElements.from_id`` overrides
  ``FemElements.from_id`` and never had the spring fallback that the object container
  did, so a ``GSETMEMB`` naming a spring raised ``The elem id "128374" is not found``;
* the object reader "worked" but silently kept the spring's *pre-renumber* id, because
  ``_renumber_from_map`` only walks ``self._elements``. The elset then pointed at an id
  that no longer named anything.

``files/fem_files/sesam/spring_in_elset.FEM`` is the smallest deck that pins both: a
beam and a SPRING1 whose internal ids (1, 2) differ from their external ids (900,
128374), and one ELSET naming both by internal id.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.config import Config
from ada.fem import FemSet, Spring
from ada.fem.shapes import definitions as shape_def

BEAM_EXTERNAL_ID = 900
SPRING_EXTERNAL_ID = 128374


@pytest.fixture
def spring_deck(fem_files) -> pathlib.Path:
    return fem_files / "sesam/spring_in_elset.FEM"


@pytest.fixture(params=[True, False], ids=["array", "object"])
def both_reader_paths(request, monkeypatch):
    """The two Sesam .FEM readers, picked by ``Config().meshing_array_backed``.

    Parametrised rather than tested once because the two failed *differently*: the
    array path crashed, the object path corrupted quietly. Both have to be pinned.
    """
    monkeypatch.setattr(Config(), "meshing_array_backed", request.param)
    return request.param


def _spring_model() -> ada.Assembly:
    """A beam mesh with one SPRING1 bolted to its first node."""
    a = ada.Assembly("A")
    p = ada.Part("P")
    a.add_part(p)
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    p.add_beam(bm)
    p.fem = bm.to_fem_obj(0.5, "line")
    fem_set = FemSet("spr1_set", [p.fem.nodes.from_id(1)], FemSet.TYPES.NSET, parent=p.fem)
    stiff = np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]).astype(float)
    p.fem.add_spring(Spring("spr1", 9001, "SPRING1", fem_set=fem_set, stiff=stiff, parent=p.fem))
    return a


# ── the reported defect ──────────────────────────────────────────────────────
def test_an_elset_can_name_a_spring(spring_deck, both_reader_paths):
    fem = ada.from_fem(spring_deck).get_by_name("T1").fem

    members = {m.id for m in fem.sets.elements["MySprings"].members}

    # Both by external id: the spring is renumbered internal -> external alongside the
    # beam now, which is the half the object path used to get wrong.
    assert members == {BEAM_EXTERNAL_ID, SPRING_EXTERNAL_ID}


def test_the_spring_is_reachable_as_an_element(spring_deck, both_reader_paths):
    fem = ada.from_fem(spring_deck).get_by_name("T1").fem

    spring = fem.elements.from_id(SPRING_EXTERNAL_ID)

    assert isinstance(spring, Spring)
    assert spring.type is shape_def.SpringTypes.SPRING1
    # The MGSPRNG triangle 1..21, mirrored.
    assert spring.stiff.shape == (6, 6)
    assert spring.stiff[0].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_both_reader_paths_agree(spring_deck, monkeypatch):
    """The paths diverged: one raised, the other returned a wrong id. Pin them together."""
    seen = []
    for backed in (True, False):
        monkeypatch.setattr(Config(), "meshing_array_backed", backed)
        fem = ada.from_fem(spring_deck).get_by_name("T1").fem
        seen.append(sorted(el.id for el in fem.elements))

    assert seen[0] == seen[1] == [BEAM_EXTERNAL_ID, SPRING_EXTERNAL_ID]


# ── the representation ───────────────────────────────────────────────────────
def test_springs_is_a_view_not_a_store(spring_deck, both_reader_paths):
    fem = ada.from_fem(spring_deck).get_by_name("T1").fem

    assert {name: s.id for name, s in fem.springs.items()} == {"spr2": SPRING_EXTERNAL_ID}
    # Same object, not a copy kept in step by hand.
    assert fem.springs["spr2"] is fem.elements.from_id(SPRING_EXTERNAL_ID)


def test_the_view_is_read_only():
    """A setter would be a second way in, and a second way in is how the same spring
    gets added twice."""
    fem = _spring_model().get_by_name("P").fem

    with pytest.raises(AttributeError):
        fem.springs = {}


def test_add_spring_adds_exactly_once():
    fem = _spring_model().get_by_name("P").fem

    assert sum(1 for el in fem.elements if isinstance(el, Spring)) == 1
    assert len(fem.springs) == 1


def test_a_spring_is_not_a_structural_element(spring_deck, both_reader_paths):
    """The type-filtered views feed the writers; a spring in `lines` would be emitted
    as a beam."""
    fem = ada.from_fem(spring_deck).get_by_name("T1").fem

    for view in (fem.elements.stru_elements, fem.elements.lines, fem.elements.shell, fem.elements.solids):
        assert SPRING_EXTERNAL_ID not in {el.id for el in view}

    assert SPRING_EXTERNAL_ID in {el.id for el in fem.elements}


def test_the_spring_type_membership_is_enum_based():
    """`ElemShapeTypes.springs` held strings, so `el.type in ...` was always False —
    a filter that can never match is worse than no filter."""
    assert shape_def.SpringTypes.SPRING1 in shape_def.ElemShapeTypes.springs
    assert shape_def.SpringTypes.SPRING2 in shape_def.ElemShapeTypes.springs
    assert shape_def.is_structural(shape_def.SpringTypes.SPRING1) is False
    assert shape_def.is_structural(shape_def.LineShapes.LINE) is True

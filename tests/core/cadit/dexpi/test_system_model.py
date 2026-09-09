"""The three-layer split: document, native model, built assembly.

``ada.SystemModel`` is the middle layer, and the reason it exists is that reading a P&ID and
building a plant are different jobs. A P&ID states what exists and what is connected to what, and
**nothing at all about where any of it stands** -- so the read produces no coordinates, the build
produces them from its own rules, and each reports its own failures.

Two consequences are asserted here because they are the ones that would quietly rot:

* the same model builds twice, with different rules, without being re-read -- which is only true if
  the build genuinely owns placement;
* the export lives on the model rather than the assembly, because DEXPI cannot express a coordinate
  and so a built assembly has nothing to contribute to a write-back.
"""

from __future__ import annotations

import pathlib

import pytest

import ada
from ada.topo_model.build_spec import ProceduralBuildSpec
from ada.topo_model.layout import LayoutRules

_UNIT_SEPARATOR = pathlib.Path(__file__).resolve().parents[4] / "files" / "dexpi_files" / "unit_separator_proteus.xml"


@pytest.fixture(scope="module")
def model():
    return ada.SystemModel.from_dexpi(_UNIT_SEPARATOR)


# -- what the read produces ----------------------------------------------------------------------


def _is_unplaced(eq) -> bool:
    """``Equipment.origin`` is the box's *base centre* -- ``(X + LX/2, Y + LY/2, Z)`` -- so an
    unplaced equipment is not at ``(0,0,0)``; it is at its own half-extents, with the corner X/Y/Z
    the layout would have set still zero. Asserting ``origin == (0,0,0)`` would simply never hold
    and asserting on ``placement.origin`` would hold for placed equipment too."""
    return tuple(eq.origin) == (eq.lx / 2.0, eq.ly / 2.0, 0.0)


def test_the_reader_returns_a_model_not_an_assembly(model):
    assert isinstance(model, ada.SystemModel)
    assert model.equipment and model.systems


def test_the_model_carries_no_coordinates(model):
    """The load-bearing claim of the whole layer."""
    assert all(_is_unplaced(eq) for eq in model.equipment)


def test_the_equipment_have_real_ports(model):
    """Unplaced is not the same as unresolved: the envelope and the ports are settled by the read."""
    ported = [eq for eq in model.equipment if eq.ports]
    assert ported, "no equipment resolved any ports"
    assert all(eq.lx and eq.ly and eq.lz for eq in ported), "an equipment resolved to no envelope"


def test_the_systems_are_wired_to_those_ports(model):
    for system in model.systems:
        assert system.ports, f"{system.name} is not connected to anything"


def test_the_source_document_is_kept_for_the_write_back(model):
    assert model.source_document is not None


# -- what the build produces ---------------------------------------------------------------------


def test_building_places_the_equipment_the_read_left_at_the_origin(model):
    assembly = model.to_assembly(
        ProceduralBuildSpec(layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0))
    )

    placed = [eq for eq in assembly.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment)]
    assert placed
    assert any(not _is_unplaced(eq) for eq in placed), "the build placed nothing"


def test_one_model_builds_twice_with_different_rules(model):
    """Only possible because the build owns the layout. If the read had baked deck bounds in, the
    second build would have to re-read the file to change them."""
    tight = model.to_assembly(ProceduralBuildSpec(layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0)))
    roomy = model.to_assembly(ProceduralBuildSpec(layout=LayoutRules(max_length=60.0, max_width=40.0, deck_height=8.0)))

    def decks(assembly):
        return {
            part.name for part in assembly.get_all_parts_in_assembly(include_self=True) if part.name.startswith("Deck")
        }

    assert decks(tight) != decks(roomy) or _extent(tight) != _extent(roomy)


def _extent(assembly):
    beams = list(assembly.get_all_physical_objects(by_type=ada.Beam))
    return round(max((max(b.n1.p[0], b.n2.p[0]) for b in beams), default=0.0), 3)


def test_the_build_does_not_mutate_the_model(model):
    before = [tuple(eq.origin) for eq in model.equipment]

    model.to_assembly(ProceduralBuildSpec(layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0)))

    assert [tuple(eq.origin) for eq in model.equipment] == before


def test_the_two_reports_are_separate(model):
    """A read gap and a build gap have different causes and different fixes, so they are not one
    list. The read report is on the model; the build's is on the assembly it produced."""
    assembly = model.to_assembly(
        ProceduralBuildSpec(layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0))
    )

    assert hasattr(model.report, "issues")
    assert set(assembly.metadata["build"]) >= {"issues", "stats"}
    assert "dexpi" not in assembly.metadata, "the read report must not be copied onto the build"


def test_a_model_with_no_factory_cannot_be_built():
    """A hand-built model has no procedural input, and saying so beats a confusing failure later."""
    with pytest.raises(ValueError, match="procedural_factory"):
        ada.SystemModel(name="hand-built").to_assembly()


# -- the top-level factory still hands back an assembly -------------------------------------------


def test_ada_from_dexpi_still_returns_an_assembly():
    """Every top-level ``ada.from_*`` produces an Assembly; this one composes the two steps."""
    assembly = ada.from_dexpi(
        _UNIT_SEPARATOR,
        spec=ProceduralBuildSpec(layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0)),
    )

    assert isinstance(assembly, ada.Assembly)
    assert list(assembly.systems)


# -- the definitions escape hatch -----------------------------------------------------------------


def test_definitions_may_be_a_function_of_the_item():
    """For a definition that has to be computed rather than tabulated -- looked up in a vendor
    database, or derived from an attribute the workbook has no column for."""

    def taller(item):
        if "Separator" in item.class_name:
            return {"bbox": {"lx": 3.0, "ly": 3.0, "lz": 9.0}}
        return None

    model = ada.SystemModel.from_dexpi(_UNIT_SEPARATOR, definitions=taller)

    vessel = next(eq for eq in model.equipment if eq.name == "V-201")
    assert (vessel.lx, vessel.ly, vessel.lz) == (3.0, 3.0, 9.0)


def test_a_definitions_function_returning_none_falls_back_to_the_class_default():
    default = next(eq for eq in ada.SystemModel.from_dexpi(_UNIT_SEPARATOR).equipment if eq.name == "V-201")

    model = ada.SystemModel.from_dexpi(_UNIT_SEPARATOR, definitions=lambda item: None)

    vessel = next(eq for eq in model.equipment if eq.name == "V-201")
    assert (vessel.lx, vessel.ly, vessel.lz) == (default.lx, default.ly, default.lz)


def test_a_definitions_function_returning_nonsense_says_which_item():
    """Silently contributing nothing would come back as a model the wrong size, three steps away."""
    with pytest.raises(TypeError, match="must return an equipment document"):
        ada.SystemModel.from_dexpi(_UNIT_SEPARATOR, definitions=lambda item: "3 metres")

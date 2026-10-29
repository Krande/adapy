"""A selection keeps the IFC entity its objects and their ancestors were given."""

import ifcopenshell
import pytest

import ada
from ada.base.ifc_types import IFC_ENTITY_KEY, ifc_entity_spec
from ada.comms.rest.selection_export import scope_to_selection, write_selection


def _model() -> ada.Assembly:
    a = ada.Assembly("model")
    run = ada.Part("run")
    a.add_part(run)
    fitting = ada.Part("fitting")
    fitting.metadata[IFC_ENTITY_KEY] = ifc_entity_spec("IfcDuctFitting", "BEND")
    run.add_part(fitting)
    fitting.add_shape(ada.PrimCyl("body", (0, 0, 0), (0, 0, 1), 0.1))
    return a


@pytest.mark.parametrize("element, path", [(None, ()), ("fitting", ("run", "fitting")), ("body", ("fitting", "body"))])
def test_selection_writes_the_stamped_entity(tmp_path, element, path):
    out = write_selection(scope_to_selection(_model(), element, path), "ifc", tmp_path / "sel.ifc")
    (fit,) = ifcopenshell.open(str(out)).by_type("IfcDuctFitting")
    assert (fit.Name, fit.PredefinedType) == ("fitting", "BEND")

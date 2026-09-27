"""A pressure is a load type like the others: nameable through the validating setter, printable.

Both were pre-existing gaps found while writing BEUSLO: ``LoadTypes.all`` listed every type but
``PRESSURE``, so ``Load.type = "pressure"`` through the setter raised for the one type the Sesam
writer had just learned to carry; and ``Load.__repr__`` formatted ``self.forces``, which a pressure
does not have, so printing one crashed.
"""

from __future__ import annotations

import pytest

from ada.fem import FemSet, Load
from ada.fem.loads.fe_loads import LoadTypes, UnsupportedLoadType


def test_pressure_is_a_listed_load_type():
    assert LoadTypes.PRESSURE in LoadTypes.all


def test_a_pressure_can_be_named_through_the_validating_setter():
    load = Load("p", Load.TYPES.FORCE, 1.0, fem_set=FemSet("n", [], "nset"), dof=[0, 0, 1, 0, 0, 0])

    load.type = "pressure"

    assert load.type == LoadTypes.PRESSURE


def test_a_type_outside_the_list_is_still_refused():
    load = Load("p", Load.TYPES.FORCE, 1.0, fem_set=FemSet("n", [], "nset"), dof=[0, 0, 1, 0, 0, 0])

    with pytest.raises(UnsupportedLoadType):
        load.type = "suction"


def test_printing_a_pressure_load_names_it_instead_of_crashing():
    load = Load("p", LoadTypes.PRESSURE, 1000.0, fem_set=FemSet("s", [], "elset"))
    assert load.forces is None, "precondition: a pressure carries no force vector"

    text = repr(load)

    assert "p" in text and "pressure" in text and "1000.0" in text

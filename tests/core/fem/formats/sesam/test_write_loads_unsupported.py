"""A load the Sesam writer has no card for is reported by name, and the deck still writes.

``load_str`` used to log an error and return ``None`` for anything but gravity and a point force,
and ``case_loads_str``'s ``out_str += load_str(...)`` then raised
``TypeError: can only concatenate str (not "NoneType") to str``: one ``*Dsload`` in the deck cost
the whole conversion, and the traceback pointed at a string concatenation rather than at the load
that caused it. It is an OMITTED finding now -- the same way ``write_bcs`` and
``write_constraints`` name what they cannot carry -- so the deck comes out holding everything
Sesam does have a card for, with the load that did not make it named in the report beside it.
"""

from __future__ import annotations

import ada
from ada.base.types import GeomRepr
from ada.fem import Elem, FemSection, FemSet, Surface
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.write.write_loads import step_loads_str
from ada.fem.loads import Load, LoadGravity, LoadPoint, LoadPressure
from ada.fem.steps import StepImplicitStatic
from ada.materials.metals import CarbonSteel


def _model(*extra: Load) -> ada.Assembly:
    """A 1x1 quad shell whose static step carries gravity, a tip force and ``extra``.

    Gravity (BGRAV) and the point force (BNLOAD) are there so the test can tell "the writer
    skipped the load it cannot hold" from "the writer produced nothing".
    """
    a = ada.Assembly("a")
    p = a.add_part(ada.Part("p"))
    mat = p.add_material(ada.Material("S355", CarbonSteel("S355")))
    fem = p.fem
    coords = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    nodes = [fem.nodes.add(ada.Node(c, i, parent=fem)) for i, c in enumerate(coords, start=1)]
    els = fem.add_set(FemSet("plate", [fem.add_elem(Elem(1, nodes, "QUAD", parent=fem))], "elset", parent=fem))
    fem.add_section(FemSection("sec", GeomRepr.SHELL, els, mat, thickness=0.01, parent=fem))
    tip = fem.add_set(FemSet("tip", [nodes[2]], "nset", parent=fem))

    step = a.fem.add_step(StepImplicitStatic("static", total_time=1.0, init_incr=0.1, max_incr=0.5))
    step.add_load(LoadGravity("grav", -9.81))
    step.add_load(LoadPoint("tip_load", 100.0, tip, [0, 0, -1, 0, 0, 0]))
    surf = fem.add_surface(Surface("press_surf", Surface.TYPES.ELEMENT, els, el_face_index=0, parent=fem))
    step.add_load(LoadPressure("press", 1000.0, surf))
    for load in extra:
        step.add_load(load)
    return a


def test_an_unsupported_load_is_one_omitted_finding_and_the_rest_still_writes():
    a = _model()

    with conversion_report.collect() as report:
        text = step_loads_str(a.fem.steps[0])

    omitted = [f for f in report.of_kind(conversion_report.OMITTED) if f.keyword == "Load"]
    assert len(omitted) == 1
    assert omitted[0].subject == "press"
    # The type is in the reason, so the report says what kind of load it was, not only its name.
    assert "pressure" in omitted[0].reason

    # Still a deck: the two loads Sesam does hold are on it.
    assert "BGRAV" in text
    assert "BNLOAD" in text


def test_every_load_type_with_no_card_is_named_rather_than_raised_on():
    """None of them may return ``None`` into ``case_loads_str``'s ``out_str +=``, and each has to
    reach the report under its own name -- a report saying "a load was dropped" without saying
    which one is not something an engineer can act on."""
    unsupported = [
        Load("rot", Load.TYPES.ACC_ROT, 1.0, dof=[0, 0, 1]),
        Load("mass_load", Load.TYPES.MASS, 2.0),
        Load("set_force", Load.TYPES.FORCE_SET, 3.0),
    ]
    a = _model(*unsupported)

    with conversion_report.collect() as report:
        text = step_loads_str(a.fem.steps[0])

    named = {f.subject for f in report.of_kind(conversion_report.OMITTED) if f.keyword == "Load"}
    assert named == {"press", "rot", "mass_load", "set_force"}
    assert "BGRAV" in text and "BNLOAD" in text

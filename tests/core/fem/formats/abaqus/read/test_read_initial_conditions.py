import ada
from ada.fem.formats.abaqus.read.reader import get_initial_conditions_from_str


def test_read_initial_condition_assembly_set():
    init_c1 = """** PREDEFINED FIELDS
**
** Name: IC-1   Type: Velocity
*Initial Conditions, type=VELOCITY
CONTAINER20FT-10000KG, 1, 0.
CONTAINER20FT-10000KG, 2, 0.
CONTAINER20FT-10000KG, 3, 4.1
**"""
    a = ada.Assembly()
    node = a.fem.nodes.add(ada.Node((0, 0, 0), nid=1))
    a.fem.sets.add(ada.fem.FemSet("CONTAINER20FT-10000KG", [node]))
    get_initial_conditions_from_str(a, init_c1)
    assert len(a.fem.predefined_fields) == 1

    pre_def = a.fem.predefined_fields.get("IC-1")
    assert pre_def is not None

    assert len(pre_def.dofs) == 3
    assert len(pre_def.magnitude) == 3
    assert pre_def.dofs[0] == 1
    assert pre_def.magnitude[0] == 0.0
    assert pre_def.dofs[1] == 2
    assert pre_def.magnitude[1] == 0.0
    assert pre_def.dofs[2] == 3
    assert pre_def.magnitude[2] == 4.1


def test_read_initial_condition_part_set():
    init_c2 = """** 
** PREDEFINED FIELDS
** 
** Name: IC-1   Type: Velocity
*Initial Conditions, type=VELOCITY
EXAMPLEMODEL-1.POINT, 1, -1.
** ----------------------------------------------------------------
** 
** STEP: dynamic
    """

    p = ada.Part("EXAMPLEMODEL")
    node = p.fem.nodes.add(ada.Node((0, 0, 0), nid=1))
    p.fem.sets.add(ada.fem.FemSet("POINT", [node]))
    a = ada.Assembly() / p
    get_initial_conditions_from_str(a, init_c2)
    assert len(a.fem.predefined_fields) == 1

    pre_def = a.fem.predefined_fields.get("IC-1")
    assert pre_def is not None

    assert len(pre_def.dofs) == 1
    assert len(pre_def.magnitude) == 1
    assert pre_def.dofs[0] == 1
    assert pre_def.magnitude[0] == -1.0


def test_read_predefined_field_v3():
    init_c3 = """** PREDEFINED FIELDS
**
** Name: InitState   Type: VELOCITY
*Initial Conditions, type=VELOCITY
DC-1.move_point, 1, -1.73

"""

    p = ada.Part("DC")
    node = p.fem.nodes.add(ada.Node((0, 0, 0), nid=1))
    p.fem.sets.add(ada.fem.FemSet("move_point", [node]))
    a = ada.Assembly() / p
    get_initial_conditions_from_str(a, init_c3)
    assert len(a.fem.predefined_fields) == 1

    pre_field = a.fem.predefined_fields.get("InitState")
    assert pre_field is not None

    assert len(pre_field.dofs) == 1
    assert len(pre_field.magnitude) == 1

    assert pre_field.dofs[0] == 1
    assert pre_field.magnitude[0] == -1.73

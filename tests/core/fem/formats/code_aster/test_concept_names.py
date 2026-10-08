"""Code_Aster concept names: a user's load, Bc, coupling or material never takes a name the command
file binds itself, and is always a Python identifier.

Measured on main with Code_Aster 18.1.8 (cantilever shell under gravity): a load or Bc named
``model`` was written ``model = AFFE_CHAR_MECA(`` after ``model = AFFE_MODELE(`` and the run stopped at
``<SUPERVIS_4>``; a load named ``my grav`` was written ``my grav = AFFE_CHAR_MECA(`` and the run stopped
at ``<F>_SYNTAX_ERROR``.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import ada
from ada.api.fem_tasks import design_cantilever, mesh_cantilever
from ada.fem import Bc, FemSet, LoadGravity
from ada.fem.formats.code_aster.write import names
from ada.fem.formats.code_aster.write.names import RESERVED, ConceptNames

WRITE_DIR = pathlib.Path(names.__file__).parent

#: A statement binding a name, at the start of a line of command-file text: ``mesh = LIRE_MAILLAGE(``,
#: ``dofs = dict(``, ``bm_sets = (``, ``Traction=DEFI_FONCTION(``. (A keyword argument is indented.)
_BINDING = re.compile(r"^([A-Za-z_]\w*)[ \t]*=[ \t]*[A-Za-z_]*[(\[]", re.M)
#: A concept an operator creates: ``NUME_DDL=CO('dofs_eig')``.
_CO = re.compile(r"\bCO\(\s*['\"](\w+)['\"]\s*\)")
#: Python variables whose string value the writer binds in the file (``{output_mesh} = CREA_MAILLAGE``,
#: ``{elset} = (...)``), and the result name of a non-linear step (``StatNonLin("result", ...)``).
_NAME_VARIABLES = {"output_mesh", "input_mesh", "elset"}


def _bound_by_the_writer() -> dict[str, str]:
    """Every name the writer's own templates bind, with the file it is bound in."""
    found = {}
    for path in sorted(WRITE_DIR.rglob("*.py")):
        if path.name == "names.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in list(_BINDING.finditer(node.value)) + list(_CO.finditer(node.value)):
                    found.setdefault(m.group(1), path.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str) and re.fullmatch(r"[A-Za-z_]\w*", node.value.value):
                    if any(isinstance(t, ast.Name) and t.id in _NAME_VARIABLES for t in node.targets):
                        found.setdefault(node.value.value, path.name)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "StatNonLin" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    found.setdefault(node.args[0].value, path.name)
    return found


def test_every_name_the_writer_binds_itself_is_reserved():
    bound = _bound_by_the_writer()
    # the scan sees what it is meant to see
    assert {"mesh", "model", "material", "element", "result", "dofs", "stiff", "Traction", "sh_sets"} <= set(bound)
    unreserved = {name: where for name, where in bound.items() if name not in RESERVED}
    assert not unreserved, f"names the command file binds itself but RESERVED does not list: {unreserved}"


def _set(name="s"):
    return FemSet(name, [1], FemSet.TYPES.NSET)


def test_a_concept_name_is_the_prefixed_sanitised_name():
    reg = ConceptNames()
    assert reg.name(LoadGravity("model", -9.81), "load") == "ld_model"
    assert reg.name(LoadGravity("my grav", -9.81), "load") == "ld_my_grav"
    assert reg.name(Bc("model", _set(), [1]), "bc") == "bc_model"
    assert reg.name(ada.Material("mesh"), "material") == "mt_mesh"


def test_a_reserved_name_gets_a_suffix():
    reg = ConceptNames()
    # bc_step is the non-linear step's load-ramp function
    assert reg.name(Bc("step", _set(), [1]), "bc") == "bc_step_2"


def test_two_objects_of_one_name_get_two_names_and_one_object_keeps_its_own():
    reg = ConceptNames()
    first, second = LoadGravity("grav", -9.81), LoadGravity("grav", -1.0)
    assert reg.name(first, "load") == "ld_grav"
    assert reg.name(second, "load") == "ld_grav_2"
    assert reg.name(first, "load") == "ld_grav"
    # a name that sanitises onto another one's is told apart the same way
    assert reg.name(LoadGravity("my grav", -9.81), "load") == "ld_my_grav"
    assert reg.name(LoadGravity("my_grav", -9.81), "load") == "ld_my_grav_2"


def test_a_material_is_one_concept_per_name():
    """The writer writes one DEFI_MATERIAU per material name, and a section names its material by it."""
    reg = ConceptNames()
    assert reg.name(ada.Material("S355"), "material") == "mt_S355"
    assert reg.name(ada.Material("S355"), "material") == "mt_S355"


def _cantilever_comm(tmp_path, load_name="grav", bc_name=None, material_name=None) -> str:
    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    p = a.get_part("MyPart")
    if bc_name is not None:
        for bc in p.fem.bcs + a.fem.bcs:
            bc.name = bc_name
    if material_name is not None:
        for mat in p.materials:
            mat.name = material_name
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity(load_name, -9.81 * 80))
    a.to_fem("names", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=False)
    return (tmp_path / "names" / "names.comm").read_text(encoding="utf-8")


def _bindings(comm: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^([A-Za-z_]\w*)\s*=", comm, re.M)]


@pytest.mark.parametrize(
    "load_name, bc_name, material_name, concept",
    [
        ("model", None, None, "ld_model"),
        ("grav", "model", None, "bc_model"),
        ("grav", None, "model", "mt_model"),
        ("result", None, None, "ld_result"),
    ],
)
def test_a_user_name_the_deck_uses_itself_is_bound_once_under_its_own_name(
    tmp_path, load_name, bc_name, material_name, concept
):
    comm = _cantilever_comm(tmp_path, load_name, bc_name, material_name)
    bound = _bindings(comm)
    assert bound.count("model") == 1
    assert bound.count(concept) == 1
    # every concept a step or the model uses is one the file binds
    for used in re.findall(r"CHARGE=(\w+)", comm) + re.findall(r"MATER=\((\w+),\)", comm):
        assert used in bound, used
    ast.parse(comm)


def test_a_load_name_that_is_no_identifier_gives_a_command_file_python_reads(tmp_path):
    comm = _cantilever_comm(tmp_path, "my grav")
    ast.parse(comm)
    assert "ld_my_grav = AFFE_CHAR_MECA(" in comm
    assert "_F(CHARGE=ld_my_grav)" in comm


def test_writing_a_deck_leaves_the_part_s_bcs_as_they_were(tmp_path):
    """The writer joined the assembly's Bcs to the part's with ``+=`` on the part's own list: every
    write appended them to the user's model once more."""
    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    p = a.get_part("MyPart")
    tip = [n for n in p.fem.nodes][:1]
    a.fem.add_bc(Bc("asm_hold", FemSet("asm_hold_set", tip, FemSet.TYPES.NSET), [3]))
    before = [bc.name for bc in p.fem.bcs]
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))
    for i in range(2):
        a.to_fem(f"twice{i}", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=False)
    assert [bc.name for bc in p.fem.bcs] == before
    comm = (tmp_path / "twice1" / "twice1.comm").read_text(encoding="utf-8")
    assert _bindings(comm).count("bc_asm_hold") == 1


def test_a_part_bc_and_an_assembly_bc_of_one_name_are_two_concepts(tmp_path):
    """Two Bcs named "Fixed" (one on the part, one on the assembly) were one concept: the second
    definition replaced the first and the support it stood for was gone from the solve."""
    a = design_cantilever()
    a = mesh_cantilever(a, geom_repr="shell", elem_order=1, use_hex_quad=True, reduced_integration=False, mesh_size=0.2)
    p = a.get_part("MyPart")
    (part_bc,) = p.fem.bcs
    a.fem.add_bc(Bc(part_bc.name, FemSet("asm_hold_set", [n for n in p.fem.nodes][:1], FemSet.TYPES.NSET), [3]))
    step = a.fem.add_step(ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))
    a.to_fem("two_fixed", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=False)
    comm = (tmp_path / "two_fixed" / "two_fixed.comm").read_text(encoding="utf-8")
    bound = _bindings(comm)
    assert bound.count("bc_Fixed") == 1 and bound.count("bc_Fixed_2") == 1
    assert re.findall(r"CHARGE=(\w+)", comm) == ["bc_Fixed", "bc_Fixed_2", "ld_grav"]

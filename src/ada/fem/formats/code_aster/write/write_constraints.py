"""Couplings for Code_Aster.

A coupling (or rigid body) ties its dependent nodes rigidly to a reference node, written as ``LIAISON_SOLIDE``
over the reference node set and the dependent node set. A reference node that belongs to no element has no
dofs in Code_Aster, so each one gets a ``POI1`` cell with a zero-stiffness ``DIS_TR`` discrete element. That
gives it the 6 dofs a support or a load can act on, without adding stiffness or mass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem import Constraint

from .names import concept_name

if TYPE_CHECKING:
    from ada.api.spatial import Part

#: The element group the reference points of all couplings are created in
REF_POINTS_GROUP = "ref_pts"


def get_couplings(part: Part) -> list[Constraint]:
    couplings = []
    for fem in _fems(part):
        for con in fem.constraints.values():
            if con.type not in (Constraint.TYPES.COUPLING, Constraint.TYPES.RIGID_BODY):
                logger.warning(f'Constraint "{con.name}" of type "{con.type}" is not written to Code_Aster')
                continue
            if con not in couplings:
                couplings.append(con)
    return couplings


def model_bcs(part: Part) -> list:
    """The boundary conditions of the part's FEM and of its assembly's, each once."""
    out = []
    for fem in _fems(part):
        out += [bc for bc in fem.bcs if all(bc is not b for b in out)]
    return out


def step_bcs(part: Part, step) -> list:
    """The boundary conditions in force in ``step``: the model's (:func:`model_bcs`), then the own ``Bc``s of every
    step from the first through ``step``, in deck order (:func:`.write_steps.all_steps`).

    A ``Bc`` added to a step (``Step.add_bc``; where the Abaqus reader puts a ``*Boundary`` found inside a ``*Step``)
    holds in that step and in every later one, as in Abaqus and in CalculiX, whose ``*BOUNDARY`` in a step carries
    into the next (the CalculiX writer writes it in its step, :func:`ada.fem.formats.calculix.write.write_steps._bcs_str`).
    Code_Aster solves each step from its own ``EXCIT`` list, so a step lists them again. They were left out of the
    deck without a word: a cantilever held in dx, dy, dz by the model and in rx, ry, rz by its step stopped at
    <FACTOR_11> (a mechanism), and with any other support holding the rotations it would have been solved wrongly.
    ``step`` None: the model's only."""
    from .write_steps import all_steps

    out = model_bcs(part)
    if step is None:
        return out
    for st in all_steps(part.get_assembly()):
        out += [bc for bc in st.bcs.values() if all(bc is not b for b in out)]
        if st is step:
            break
    return out


@dataclass
class StepCharges:
    """The supports a step is solved with."""

    #: Every charge of the supports: the one of all supports, the one holding the prescribed dofs at zero (a static
    #: step giving them values replaces it with its own) and the couplings.
    names: list[str]
    #: The name among ``names`` of the charge holding the prescribed dofs at zero; None for none.
    zero: str | None
    #: The ``AFFE_CHAR_MECA`` of the step's own charges, to go before the step's solve; empty when the step is solved
    #: with the model's (written once, by the writer).
    definitions: str
    #: The boundary conditions in force (:func:`step_bcs`).
    bcs: list


def step_charges(part: Part, step=None) -> StepCharges:
    """The charges ``step`` is solved with (``None``: the model's).

    A step with no ``Bc`` of its own in force (:func:`step_bcs`) uses the model's :data:`.write_bc.SUPPORTS` and
    :data:`.write_bc.PRESCRIBED_AT_ZERO`. One with them gets its own two, named after its position in the deck
    (``supports_2``, ``prescribed_zero_2``), holding the model's supports and the steps' together: one charge of all
    supports, as a dof held by two charges stops Code_Aster (<ASSEMBLA_26>, see :mod:`.write_bc`).
    """
    from ada.fem.formats.prescribed import prescribed_dofs

    from .write_bc import (
        PRESCRIBED_AT_ZERO,
        SUPPORTS,
        prescribed_at_zero_str,
        prescribed_charge_str,
        supports_str,
    )
    from .write_steps import all_steps

    bcs = step_bcs(part, step)
    prescribed = prescribed_dofs(bcs)
    own = len(bcs) > len(model_bcs(part))
    sup_name, zero_name = SUPPORTS, PRESCRIBED_AT_ZERO
    sup, zero = supports_str(bcs, prescribed), prescribed_at_zero_str(bcs)
    definitions = []
    if own:
        k = next(i for i, st in enumerate(all_steps(part.get_assembly()), start=1) if st is step)
        sup_name, zero_name = f"{SUPPORTS}_{k}", f"{PRESCRIBED_AT_ZERO}_{k}"
        if sup:
            definitions.append(supports_str(bcs, prescribed, sup_name))
        if zero:
            definitions.append(prescribed_charge_str(zero_name, {key: 0.0 for key in prescribed}, bcs))
    names = []
    if sup:
        names.append(sup_name)
    if zero:
        names.append(zero_name)
    names += [concept_name(con, "coupling") for con in get_couplings(part)]
    return StepCharges(names, zero_name if zero else None, "\n".join(definitions), bcs)


def get_charge_names(part: Part, step=None) -> list[str]:
    """The names of all mechanical loads that make up the supports ``step`` is solved with (:func:`step_charges`)."""
    return step_charges(part, step).names


def has_cara_elem(part: Part) -> bool:
    """Whether the model needs element characteristics (AFFE_CARA_ELEM)"""
    fem = part.fem
    return len(fem.sections.lines) > 0 or len(fem.sections.shells) > 0 or len(get_couplings(part)) > 0


def create_ref_points_mesh_str(part: Part, input_mesh: str, output_mesh: str = "mesh_ref") -> str:
    ref_sets = ", ".join(f"'{con.m_set.name}'" for con in get_couplings(part))
    return f"""{output_mesh} = CREA_MAILLAGE(
    MAILLAGE={input_mesh},
    CREA_POI1=_F(NOM_GROUP_MA='{REF_POINTS_GROUP}', GROUP_NO=({ref_sets},)),
)
"""


def create_ref_points_model_str() -> str:
    return f"_F(GROUP_MA='{REF_POINTS_GROUP}', PHENOMENE='MECANIQUE', MODELISATION='DIS_TR',),"


def create_ref_points_discrete_str() -> str:
    return f"""
        DISCRET=(
            _F(GROUP_MA='{REF_POINTS_GROUP}', CARA='K_TR_D_N', VALE=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            _F(GROUP_MA='{REF_POINTS_GROUP}', CARA='M_TR_D_N', VALE=(0.0,) * 10),
        ),"""


def create_coupling_str(con: Constraint) -> str:
    if con.dofs is not None and sorted(con.dofs) != [1, 2, 3, 4, 5, 6]:
        logger.warning(
            f'Coupling "{con.name}" couples dofs {con.dofs}. Code_Aster writes it as a rigid link in all dofs'
        )
    return f"""{concept_name(con, "coupling")} = AFFE_CHAR_MECA(
    MODELE=model, LIAISON_SOLIDE=_F(GROUP_NO=('{con.m_set.name}', '{con.s_set.name}'))
)"""


def _fems(part: Part):
    yield part.fem
    assembly = part.get_assembly()
    if assembly is not part:
        yield assembly.fem

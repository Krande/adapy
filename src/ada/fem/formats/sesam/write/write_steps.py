from __future__ import annotations

import datetime
from typing import Sequence

from ada.core.utils import get_current_user
from ada.fem.steps import Step, StepEigen, StepImplicitStatic

from .not_held import STAGE, report
from .templates import (
    sestra_buckle_inp_str,
    sestra_eig_inp_str,
    sestra_header_inp_str,
    sestra_static_inp_str,
)

#: The EIGA shift of a buckling run: positive (Sestra UM, CMAS LBCK); Sestra returns the ENR stability factors nearest
#: it, so one below it is found too. Measured, Sestra 11.3-01, the pinned IPE300 column (factors ~3.5e5): a shift of
#: 1e-3 put its first factor 2.0e-4 off the Timoshenko closed form, any shift from 1 to 3e5 3.4e-6 -- the mesh's own
#: error. 1.0, Sestra's own default for buckling (its log reads "shift = 1.000000" when none is read).
BUCKLING_SHIFT = 1.0


def _sestra_writers() -> dict:
    return {
        Step.TYPES.EIGEN: write_sestra_eig_str,
        Step.TYPES.STATIC: write_sestra_static_str,
        Step.TYPES.BUCKLING: write_sestra_buckle_str,
    }


def write_sestra_inp(name, step: StepEigen | StepImplicitStatic, supnr: int = 1, free_dofs: int | None = None):
    """The Sestra control file for ``step``, analysing super element ``supnr`` (ITOP) -- the
    number the deck is written as, or Sestra would look for another super element. ``free_dofs``: a lower bound on
    the deck's free dofs, which caps a buckling run's Lanczos basis (:func:`buckling_nlvec`)."""
    step_str_writer = _sestra_writers().get(step.type, None)
    if step_str_writer is None:
        raise ValueError(f'Step type "{step.type}" is not supported yet for Ada-Sestra ')

    now = datetime.datetime.now()
    date_str = now.strftime("%d-%b-%Y")
    clock_str = now.strftime("%H:%M:%S")
    user = get_current_user()
    head_str = sestra_header_inp_str.format(date_str=date_str, clock_str=clock_str, user=user)
    if step.type == Step.TYPES.BUCKLING:
        return head_str + step_str_writer(name, step, supnr, free_dofs=free_dofs)
    return head_str + step_str_writer(name, step, supnr)


def free_dofs_lower_bound(fem) -> int:
    """At least the deck's free dofs: three per node (a solid's node has three, others six), less every dof a BC
    names at each of its nodes."""
    fixed = 0
    for bc in fem.bcs:
        members = getattr(getattr(bc, "fem_set", None), "members", None) or []
        fixed += len(bc.dofs or ()) * len(members)
    return max(0, 3 * len(fem.nodes) - fixed)


#: A free-vibration run's EIGA shift, in Hz: the ENR eigenpairs nearest it, so 0 gives the lowest (Sestra's default).
EIGEN_SHIFT = 0.0


def write_sestra_eig_str(name: str, step: StepEigen, supnr: int = 1):
    # On EIGA's own columns (eiga_line). The template's "0." used to land in EHIGH (61-70) with SHIFT blank: the same
    # zero by luck, and an ENR of three digits ran into the next field.
    return sestra_eig_inp_str.format(name=name, eiga=eiga_line(step.num_eigen_modes, EIGEN_SHIFT), supnr=supnr)


def write_sestra_static_str(name: str, step: StepImplicitStatic, supnr: int = 1):
    return sestra_static_inp_str.format(name=name, supnr=supnr)


def eiga_line(modes: int, shift: float, nlvec: int | None = None) -> str:
    """The EIGA command on Sestra's fixed columns (UM 11.3.1, EIGA): ENR in 6-10, NLVEC in 21-25, SHIFT in 71-80, all
    right-aligned. A shift written across 67-71, as this once was, is read as EHIGH ``0.00`` and SHIFT ``1`` (Sestra's
    log: "shift = 1.000000"). ``nlvec`` None leaves Sestra's default, 2 ENR."""
    enr, shift_text = f"{int(modes)}.", f"{shift:g}"
    nl = "" if nlvec is None else f"{int(nlvec)}."
    if len(enr) > 5 or len(nl) > 5 or len(shift_text) > 10:
        raise ValueError(f"EIGA: {modes} modes, {nlvec} vectors or shift {shift} does not fit its field")
    return f"{'EIGA':<5}{enr:>5}{'':10}{nl:>5}{'':45}{shift_text:>10}"


#: The Lanczos basis of a buckling run, in eigenpairs asked for. Measured, Sestra 11.3-01, the pinned IPE300 column
#: asking for 4: Sestra's default 2 ENR returned a 4th factor 6.75 % off (an unconverged Ritz value, though the log said
#: "converged after 1 iterations"); 3 ENR and more all four within the mesh's own error.
BUCKLING_LANCZOS_PER_MODE = 4


def buckling_nlvec(modes: int, free_dofs: int | None) -> int | None:
    """NLVEC for ``modes`` stability factors: ``BUCKLING_LANCZOS_PER_MODE`` vectors each, or None (Sestra's default) when
    that may exceed the free dofs, which Sestra stops on ("terminates with an error if NLVEC > n")."""
    nlvec = BUCKLING_LANCZOS_PER_MODE * int(modes)
    return nlvec if free_dofs is not None and nlvec <= free_dofs else None


def write_sestra_buckle_str(name: str, step, supnr: int = 1, free_dofs: int | None = None):
    eiga = eiga_line(step.num_modes, BUCKLING_SHIFT, buckling_nlvec(step.num_modes, free_dofs))
    return sestra_buckle_inp_str.format(name=name, supnr=supnr, eiga=eiga)


def written_step(steps: Sequence[Step]) -> Step | None:
    """The one step a Sesam conversion writes: the first, whatever its type.

    Its loads go into the FEM file as the load case(s) and, when Sestra has an analysis
    for its type, its control data into ``sestra.inp``. A Sesam file is one superelement
    with no history, so a second step has nowhere to go.
    """
    return steps[0] if steps else None


def written_steps(steps: Sequence[Step]) -> list[Step]:
    """The steps a Sesam conversion writes: the first (:func:`written_step`), and when it is static every static step
    after it up to the first that is not, or that has boundary conditions of its own -- each becomes load cases of the
    one deck (``write_loads.step_loads_str``).

    Sestra solves every load case of a deck with the same supports and one analysis (``sestra.inp``), so a static step
    joins only when it would be solved with the supports the earlier ones are: a step's own ``Bc`` holds from that
    step on, and in BNBCD it would hold in the earlier cases too. The first step's own boundary conditions hold in
    every step after it, so they are written with the model's. An eigenvalue or dynamic step is another analysis.
    """
    first = written_step(steps)
    if first is None:
        return []
    out = [first]
    if first.type != Step.TYPES.STATIC:
        return out
    for step in steps[1:]:
        if step.type != Step.TYPES.STATIC or len(step.bcs) > 0:
            break
        out.append(step)
    return out


def report_steps(steps: Sequence[Step], sestra_inp_written: bool, model_data_only: bool = False) -> None:
    """Name every step in the report: a Sesam FEM file holds no analysis steps at all.

    The first step is written in part -- its loads as the load case, and for a linear
    static or eigenvalue step a Sestra control file -- and this says what of it is not; the
    rest are left out whole, loads and boundary conditions with them.
    """
    from .not_held import HELD_BC_TYPES

    rep = report()
    first = written_step(steps)
    written = written_steps(steps)
    for step in steps:
        for name in step.interactions:
            rep.omitted(STAGE, "Interaction", name, "a Sesam file has no contact", step=step.name)
        if all(step is not w for w in written):
            rep.omitted(
                STAGE,
                "Step",
                step.name,
                "a Sesam file holds one analysis with one set of supports; the static steps from the first are "
                "written as its load cases up to one that is not static or has boundary conditions of its own, and "
                "this one is not among them",
                n_loads=len(step.loads),
                n_bcs=len(step.bcs),
            )
            continue
        if step is not first:
            if step.nl_geom:
                rep.approximated(STAGE, "Step", step.name, "Sestra is linear; geometric nonlinearity is not written")
            continue

        if sestra_inp_written:
            rep.note(
                STAGE,
                "Step",
                step.name,
                "the FEM file holds no steps; its loads are the load case, its analysis control is sestra.inp",
                type=step.type,
            )
        elif model_data_only:
            rep.note(
                STAGE,
                "Step",
                step.name,
                "model data only: its loads are the load case; no sestra.inp was asked for",
                type=step.type,
            )
        else:
            rep.omitted(
                STAGE,
                "Step",
                step.name,
                "Sestra has no analysis of this type; only the step's loads are written, as the load case",
                type=step.type,
            )
        if step.nl_geom:
            rep.approximated(STAGE, "Step", step.name, "Sestra is linear; geometric nonlinearity is not written")
        n_out = len(step.field_outputs) + len(step.hist_outputs)
        if n_out:
            rep.omitted(STAGE, "Step", step.name, "output requests have no Sesam form", n_outputs=n_out)
        for bc in step.bcs.values():
            # The first step's own BCs hold in it and every step after it: in BNBCD, with the model's (writer.to_fem).
            if str(bc.type).lower() in HELD_BC_TYPES:
                continue
            rep.omitted(STAGE, "Bc", bc.name, "a step's boundary condition BNBCD cannot hold", step=step.name)

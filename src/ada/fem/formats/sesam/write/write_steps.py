from __future__ import annotations

import datetime
from typing import Sequence

from ada.core.utils import get_current_user
from ada.fem.steps import Step, StepEigen, StepImplicitStatic

from .not_held import STAGE, report
from .templates import sestra_eig_inp_str, sestra_header_inp_str, sestra_static_inp_str


def _sestra_writers() -> dict:
    return {Step.TYPES.EIGEN: write_sestra_eig_str, Step.TYPES.STATIC: write_sestra_static_str}


def write_sestra_inp(name, step: StepEigen | StepImplicitStatic):
    step_str_writer = _sestra_writers().get(step.type, None)
    if step_str_writer is None:
        raise ValueError(f'Step type "{step.type}" is not supported yet for Ada-Sestra ')

    now = datetime.datetime.now()
    date_str = now.strftime("%d-%b-%Y")
    clock_str = now.strftime("%H:%M:%S")
    user = get_current_user()
    head_str = sestra_header_inp_str.format(date_str=date_str, clock_str=clock_str, user=user)
    return head_str + step_str_writer(name, step)


def write_sestra_eig_str(name: str, step: StepEigen):
    return sestra_eig_inp_str.format(name=name, modes=step.num_eigen_modes, supnr=1)


def write_sestra_static_str(name: str, step: StepImplicitStatic):
    return sestra_static_inp_str.format(name=name, supnr=1)


def written_step(steps: Sequence[Step]) -> Step | None:
    """The one step a Sesam conversion writes: the first, whatever its type.

    Its loads go into the FEM file as the load case(s) and, when Sestra has an analysis
    for its type, its control data into ``sestra.inp``. A Sesam file is one superelement
    with no history, so a second step has nowhere to go.
    """
    return steps[0] if steps else None


def report_steps(steps: Sequence[Step], sestra_inp_written: bool) -> None:
    """Name every step in the report: a Sesam FEM file holds no analysis steps at all.

    The first step is written in part -- its loads as the load case, and for a linear
    static or eigenvalue step a Sestra control file -- and this says what of it is not; the
    rest are left out whole, loads and boundary conditions with them.
    """
    rep = report()
    first = written_step(steps)
    for step in steps:
        for name in step.interactions:
            rep.omitted(STAGE, "Interaction", name, "a Sesam file has no contact", step=step.name)
        if step is not first:
            rep.omitted(
                STAGE,
                "Step",
                step.name,
                "a Sesam file holds one load set; only the first step is written",
                n_loads=len(step.loads),
                n_bcs=len(step.bcs),
            )
            continue

        if sestra_inp_written:
            rep.note(
                STAGE,
                "Step",
                step.name,
                "the FEM file holds no steps; its loads are the load case, its analysis control is sestra.inp",
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
            # BNBCD is the model's, not a load case's. A step BC written there would hold in
            # every analysis of the superelement, not just this one.
            rep.omitted(STAGE, "Bc", bc.name, "a step's boundary condition; BNBCD is not per step", step=step.name)

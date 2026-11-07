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

#: The EIGA shift of a buckling run: "positive and smaller than the smallest eigenvalue" (Sestra UM, CMAS LBCK). The
#: stability factor of a sensible reference load is well above it.
BUCKLING_SHIFT = 1.0e-3


def _sestra_writers() -> dict:
    return {
        Step.TYPES.EIGEN: write_sestra_eig_str,
        Step.TYPES.STATIC: write_sestra_static_str,
        Step.TYPES.BUCKLING: write_sestra_buckle_str,
    }


def write_sestra_inp(name, step: StepEigen | StepImplicitStatic, supnr: int = 1):
    """The Sestra control file for ``step``, analysing super element ``supnr`` (ITOP) -- the
    number the deck is written as, or Sestra would look for another super element."""
    step_str_writer = _sestra_writers().get(step.type, None)
    if step_str_writer is None:
        raise ValueError(f'Step type "{step.type}" is not supported yet for Ada-Sestra ')

    now = datetime.datetime.now()
    date_str = now.strftime("%d-%b-%Y")
    clock_str = now.strftime("%H:%M:%S")
    user = get_current_user()
    head_str = sestra_header_inp_str.format(date_str=date_str, clock_str=clock_str, user=user)
    return head_str + step_str_writer(name, step, supnr)


def write_sestra_eig_str(name: str, step: StepEigen, supnr: int = 1):
    return sestra_eig_inp_str.format(name=name, modes=step.num_eigen_modes, supnr=supnr)


def write_sestra_static_str(name: str, step: StepImplicitStatic, supnr: int = 1):
    return sestra_static_inp_str.format(name=name, supnr=supnr)


def write_sestra_buckle_str(name: str, step, supnr: int = 1):
    return sestra_buckle_inp_str.format(name=name, supnr=supnr, modes=step.num_modes, shift=f"{BUCKLING_SHIFT:g}")


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

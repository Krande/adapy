from __future__ import annotations

import datetime

from ada.core.utils import get_current_user
from ada.fem.steps import Step, StepEigen, StepImplicitStatic

from .templates import sestra_eig_inp_str, sestra_header_inp_str, sestra_static_inp_str


def write_sestra_inp(name, step: StepEigen | StepImplicitStatic):
    step_map = {Step.TYPES.EIGEN: write_sestra_eig_str, Step.TYPES.STATIC: write_sestra_static_str}
    step_str_writer = step_map.get(step.type, None)
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

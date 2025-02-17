from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ada.comms.fb_wrap_model_gen import ProcedureStoreDC
from ada.config import Config
from ada.procedural_modelling.load_procedures import (
    get_procedure_from_function,
    get_procedures_from_script_dir,
)
from ada.procedural_modelling.procedure_model import Procedure


@dataclass
class ProcedureStore:
    procedures: dict[str, Procedure] = field(default_factory=dict)

    def register(self, name: str, func: Callable[..., bool]):
        self.procedures[name] = get_procedure_from_function(func)

    def get(self, name: str) -> Procedure:
        return self.procedures.get(name)

    def update_procedures(self):
        proc_script_dir = Config().procedures_script_dir
        local_scripts = {}
        if proc_script_dir is not None:
            local_scripts.update(get_procedures_from_script_dir(proc_script_dir))

        self.procedures = local_scripts

    def to_procedure_dc(self) -> ProcedureStoreDC:
        return ProcedureStoreDC(procedures=[proc.to_procedure_dc() for proc in self.procedures.values()])

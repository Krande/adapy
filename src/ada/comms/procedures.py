from __future__ import annotations

import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from ada.config import Config


@dataclass
class Procedure:
    name: str
    description: str
    func: Callable
    script_path: Optional[pathlib.Path] = None
    params: dict[str, str] = field(default_factory=dict)
    return_type: str = "None"

    def __post_init__(self):
        if self.script_path is not None:
            self.func = self._call_script_subprocess

    def _call_script_subprocess(self, *args, **kwargs):
        call_args = ["python", str(self.script_path)]
        script_args = [f"--{k} {v}" for k, v in self.params.items()]
        result = subprocess.run(call_args + script_args, check=True)
        if result.returncode != 0:
            raise Exception(f"Error running script {self.script_path}")

    def __call__(self, *args, **kwargs) -> bool:
        return self.func(*args, **kwargs)


@dataclass
class ProcedureStore:
    procedures: dict[str, Procedure] = field(default_factory=dict)

    def __post_init__(self):
        # check for env variable SCRIPT_DIR and load all procedures from there
        proc_script_dir = Config().procedures_script_dir
        if proc_script_dir is not None:
            self.procedures.update(get_procedures_from_script_dir(proc_script_dir))

    def register(self, name: str, func: Callable[..., bool]):
        self.procedures[name] = get_procedure_from_function(func)

    def get(self, name: str) -> Procedure:
        return self.procedures.get(name)


def get_procedures_from_script_dir(script_dir: pathlib.Path) -> dict[str, Procedure]:
    procedures = {}
    for script in script_dir.glob("*.py"):
        ...  # Parse the script and extract procedures

    return procedures


def get_procedure_from_function(func: Callable) -> Procedure:
    return Procedure(name=func.__name__, description=func.__doc__, func=func)
from __future__ import annotations

import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from ada.comms.fb_wrap_model_gen import (
    FileArgDC,
    FileTypeDC,
    ParameterDC,
    ParameterTypeDC,
    ProcedureDC,
    ValueDC,
)
from ada.config import logger


@dataclass
class Procedure:
    name: str
    description: str
    func: Callable | None = None
    script_path: Optional[pathlib.Path] = None
    params: dict[str, ParameterDC] = field(default_factory=dict)
    inputs: list[FileArgDC] | None = None
    outputs: list[FileArgDC] | None = None
    options: dict[str, list[ValueDC]] | None = None
    is_component: bool = False

    def __post_init__(self):
        if self.script_path is not None:
            self.func = self._call_script_subprocess

    def _call_script_subprocess(self, *args, **kwargs):
        call_args = ["python", str(self.script_path)]
        # Add args
        for arg in args:
            call_args.append(str(arg))

        # Build the command-line arguments
        for arg_name, param_dc in kwargs.items():
            # Convert underscores to hyphens
            arg_name_cli = arg_name.replace("_", "-")
            call_args.append(f"--{arg_name_cli}")
            if isinstance(param_dc, ParameterDC):
                value = make_param_value(param_dc)
                if param_dc.type == ParameterTypeDC.ARRAY:
                    call_args.extend(value)
                else:
                    call_args.append(str(value))
            else:
                value = param_dc

                if isinstance(value, bool):
                    pass
                else:
                    call_args.append(str(value))

        logger.debug(f"Running script {self.script_path} with args: {call_args}")
        result = subprocess.run(call_args, check=False, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise Exception(f"Error running script {self.script_path} due to {result.stderr}")

        return result.stdout.strip()

    def __call__(self, *args, **kwargs) -> str:
        return self.func(*args, **kwargs)

    def to_procedure_dc(self) -> ProcedureDC:
        params = list(self.params.values())
        return ProcedureDC(
            name=self.name,
            description=self.description,
            script_file_location=self.script_path.as_posix() if self.script_path is not None else "",
            parameters=(params if self.params is not None else None),
            file_inputs=self.inputs,
            file_outputs=self.outputs,
            is_component=self.is_component,
        )

    def get_component_output_dir(self):
        from ada.comms.scene_model import SceneBackend

        temp_dir = SceneBackend.get_temp_dir()
        component_dir = temp_dir / "components"

        return component_dir / self.name

    def get_procedure_output_dir(self) -> pathlib.Path:
        from ada.comms.scene_model import SceneBackend

        temp_dir = SceneBackend.get_temp_dir()
        return temp_dir / "procedural" / self.name

    def get_procedure_output(self, input_file_name: str):
        output_file_path = None
        for fp in self.get_procedure_output_dir().iterdir():
            if not fp.is_dir():
                continue
            dir_name = fp.stem
            if dir_name == input_file_name:
                output_file_path = fp
                break

        if output_file_path is None:
            raise FileNotFoundError(f"Output file for procedure {self.name} not found")

        if self.outputs == FileTypeDC.IFC:
            return (output_file_path / self.name).with_suffix(".ifc")
        elif self.outputs == FileTypeDC.GLB:
            return (output_file_path / self.name).with_suffix(".glb")
        else:
            raise NotImplementedError(f"Export file type {self.outputs} not implemented")

    def get_output_dir(self) -> pathlib.Path:
        if self.is_component is False:
            return self.get_procedure_output_dir()
        else:
            return self.get_component_output_dir()


def make_param_value(param: ParameterDC) -> str | list[str]:
    if param.type == ParameterTypeDC.STRING:
        return param.value.string_value
    elif param.type == ParameterTypeDC.INTEGER:
        return str(param.value.integer_value)
    elif param.type == ParameterTypeDC.FLOAT:
        return str(param.value.float_value)
    elif param.type == ParameterTypeDC.BOOLEAN:
        return str(param.value.boolean_value)
    elif param.type == ParameterTypeDC.ARRAY:
        if param.value.array_value is None:
            raise ValueError("Array value is None")
        values = []

        for val in param.value.array_value:
            if param.value.array_value_type == ParameterTypeDC.STRING:
                values.append(val.string_value)
            elif param.value.array_value_type == ParameterTypeDC.INTEGER:
                values.append(str(val.integer_value))
            elif param.value.array_value_type == ParameterTypeDC.FLOAT:
                values.append(str(val.float_value))
            elif param.value.array_value_type == ParameterTypeDC.BOOLEAN:
                values.append(str(val.boolean_value))
            else:
                raise NotImplementedError(f"Parameter type {param.value.array_value_type} not implemented")

        return values
    else:
        if param.type == ParameterTypeDC.UNKNOWN and param.value.string_value is not None:
            return param.value.string_value
        raise NotImplementedError(f"Parameter type {param.type} not implemented")

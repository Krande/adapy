from __future__ import annotations

import ast
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from typer import Typer
except ImportError:
    Typer = None

from ada.comms.fb_model_gen import (
    FileTypeDC,
    ParameterDC,
    ProcedureDC,
    ProcedureStoreDC, ParameterTypeDC, ValueDC, ArrayTypeDC,
)
from ada.config import Config, logger


@dataclass
class Procedure:
    name: str
    description: str
    func: Callable | None = None
    script_path: Optional[pathlib.Path] = None
    params: dict[str, ParameterDC] = field(default_factory=dict)
    input_file_var: str | None = None
    input_file_type: FileTypeDC | None = None
    export_file_type: FileTypeDC | None = None
    return_type: str = "None"

    def __post_init__(self):
        if self.script_path is not None:
            self.func = self._call_script_subprocess

    def _call_script_subprocess(self, *args, **kwargs):
        call_args = ["python", str(self.script_path)]
        # Add args
        for arg in args:
            call_args.append(str(arg))
        # Build the command-line arguments
        for arg_name, value in kwargs.items():
            # Convert underscores to hyphens
            if isinstance(value, ParameterDC):
                value = value.value
            arg_name_cli = arg_name.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    call_args.append(f"--{arg_name_cli}")
            else:
                call_args.append(f"--{arg_name_cli}")
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
            parameters=(
                params
                if self.params is not None
                else None
            ),
            input_file_var=self.input_file_var,
            input_file_type=self.input_file_type,
            export_file_type=self.export_file_type,
        )

    def get_procedure_output(self, input_file_name: str):
        from ada.comms.scene_model import Scene

        temp_dir = Scene.get_temp_dir()
        procedure_dir = temp_dir / "procedural"

        output_file_path = None
        for fp in procedure_dir.iterdir():
            if not fp.is_dir():
                continue
            dir_name = fp.stem
            if dir_name == input_file_name:
                output_file_path = fp
                break

        if output_file_path is None:
            raise FileNotFoundError(f"Output file for procedure {self.name} not found")

        if self.export_file_type == FileTypeDC.IFC:
            return (output_file_path / self.name).with_suffix(".ifc")
        elif self.export_file_type == FileTypeDC.GLB:
            return (output_file_path / self.name).with_suffix(".glb")
        else:
            raise NotImplementedError(f"Export file type {self.export_file_type} not implemented")


@dataclass
class ProcedureStore:
    procedures: dict[str, Procedure] = field(default_factory=dict)

    def register(self, name: str, func: Callable[..., bool]):
        self.procedures[name] = get_procedure_from_function(func)

    def get(self, name: str) -> Procedure:
        return self.procedures.get(name)

    def update_procedures(self):
        proc_script_dir = Config().procedures_script_dir
        components_dir = Config().procedures_components_dir
        local_scripts = {}
        if proc_script_dir is not None:
            local_scripts.update(get_procedures_from_script_dir(proc_script_dir))
        if components_dir is not None:
            local_scripts.update(get_procedures_from_script_dir(components_dir))

        self.procedures.update(local_scripts)

    def to_procedure_dc(self) -> ProcedureStoreDC:
        return ProcedureStoreDC(procedures=[proc.to_procedure_dc() for proc in self.procedures.values()])


def get_procedures_from_script_dir(script_dir: pathlib.Path) -> dict[str, Procedure]:
    procedures = {}
    for script in script_dir.glob("*.py"):
        if script.stem == "__init__":
            continue
        procedures[script.stem] = get_procedure_from_script(script)

    return procedures


def get_procedure_from_function(func: Callable) -> Procedure:
    return Procedure(name=func.__name__, description=func.__doc__, func=func)


def procedure_decorator(
    app: Typer,
    input_file_var: str | None = None,
    input_file_type: FileTypeDC | None = None,
    export_file_type: FileTypeDC | None = None,
) -> Callable:
    def wrapper(func: Callable) -> Callable:
        func.input_file_var = input_file_var
        func.input_file_type = input_file_type
        func.export_file_type = export_file_type
        if Typer is not None:
            app.command()(func)  # Apply the app.command decorator
        return func

    return wrapper


def str_to_filetype(filetype: str) -> FileTypeDC:
    if filetype == "IFC":
        return FileTypeDC.IFC
    elif filetype == "GLB":
        return FileTypeDC.GLB
    else:
        raise NotImplementedError(f"Filetype {filetype} not implemented")


def extract_decorator_options(decorator: ast.Call) -> dict[str, str | FileTypeDC | None]:
    options = dict(input_file_var=None, input_file_type=None, export_file_type=None)

    for keyword in decorator.keywords:
        if keyword.arg == "input_file_var":
            options["input_file_var"] = keyword.value.s
        elif keyword.arg == "input_file_type":
            options["input_file_type"] = str_to_filetype(keyword.value.attr)
        elif keyword.arg == "export_file_type":
            options["export_file_type"] = str_to_filetype(keyword.value.attr)

    return options

_param_type_map = {
    "str": ParameterTypeDC.STRING,
    "pathlib.Path": ParameterTypeDC.STRING,
    "int": ParameterTypeDC.INTEGER,
    "float": ParameterTypeDC.FLOAT,
    "bool": ParameterTypeDC.BOOLEAN,
    "list": ParameterTypeDC.ARRAY,
    "tuple": ParameterTypeDC.ARRAY,
    "set": ParameterTypeDC.ARRAY,
}

def arg_to_param(arg: ast.arg, default: ast.expr) -> ParameterDC:
    arg_name = arg.arg
    if arg.annotation:
        arg_type = ast.unparse(arg.annotation)
    else:
        arg_type = "Any"
    if isinstance(default, (ast.Tuple, ast.List)):
        default_arg_value = [constant.value for constant in default.elts]
    else:
        default_arg_value = default.value

    param_type = _param_type_map.get(arg_type, ParameterTypeDC.UNKNOWN)
    if param_type == ParameterTypeDC.STRING:
        default_value = ValueDC(string_value=default_arg_value)
    elif param_type == ParameterTypeDC.INTEGER:
        default_value = ValueDC(integer_value=default_arg_value)
    elif param_type == ParameterTypeDC.FLOAT:
        default_value = ValueDC(float_value=default_arg_value)
    elif param_type == ParameterTypeDC.BOOLEAN:
        default_value = ValueDC(boolean_value=default_arg_value)
    elif arg_type.startswith('tuple') or arg_type.startswith('list') or arg_type.startswith('set'):
        param_type = ParameterTypeDC.ARRAY
        result = arg_type.split('[')[1].split(']')[0]
        value_types = [r.strip() for r in result.split(',')]
        value_type = value_types[0]
        array_value_type = _param_type_map.get(value_type, ParameterTypeDC.UNKNOWN)
        if arg_type.startswith('tuple'):
            array_type = ArrayTypeDC.TUPLE
        elif arg_type.startswith('list'):
            array_type = ArrayTypeDC.LIST
        elif arg_type.startswith('set'):
            array_type = ArrayTypeDC.SET
        else:
            raise NotImplementedError(f"Parameter type {arg_type} not implemented")
        if array_value_type == ParameterTypeDC.FLOAT:
            default_values = [ValueDC(float_value=x) for x in default_arg_value]
        elif array_value_type == ParameterTypeDC.INTEGER:
            default_values = [ValueDC(integer_value=x) for x in default_arg_value]
        elif array_value_type == ParameterTypeDC.STRING:
            default_values = [ValueDC(string_value=x) for x in default_arg_value]
        elif array_value_type == ParameterTypeDC.BOOLEAN:
            default_values = [ValueDC(boolean_value=x) for x in default_arg_value]
        else:
            raise NotImplementedError(f"Parameter type {arg_type} not implemented")

        default_value = ValueDC(array_value=default_values, array_length=len(value_types), array_type=array_type, array_value_type=array_value_type)
    else:
        raise NotImplementedError(f"Parameter type {arg_type} not implemented")

    return ParameterDC(name=arg_name, type=param_type, default_value=default_value)

def get_procedure_from_script(script_path: pathlib.Path) -> Procedure:
    with open(script_path, "r") as f:
        source_code = f.read()
    tree = ast.parse(source_code, filename=str(script_path))

    main_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break

    if main_func is None:
        raise Exception(f"No 'main' function found in {script_path}")


    # Extract parameters
    params: dict[str, ParameterDC] = {}
    for arg, default in zip(main_func.args.args, main_func.args.defaults):
        arg_name = arg.arg
        params[arg_name] = arg_to_param(arg, default)

    # Extract return type
    if main_func.returns:
        return_type = ast.unparse(main_func.returns)
    else:
        return_type = "None"

    # Extract docstring
    description = ast.get_docstring(main_func) or ""

    # extract decorator (if any)
    decorator_config = {}
    if main_func.decorator_list:
        custom_decorator = [d for d in main_func.decorator_list if d.func.id == "procedure_decorator"]
        if custom_decorator:
            decorator = custom_decorator[0]
            decorator_config = extract_decorator_options(decorator)

    return Procedure(
        name=script_path.stem,
        description=description,
        script_path=script_path,
        params=params,
        return_type=return_type,
        **decorator_config,
    )

from __future__ import annotations

import ast
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

import typer

from ada.comms.fb_model_gen import (
    FileTypeDC,
    ParameterDC,
    ProcedureDC,
    ProcedureStoreDC,
)
from ada.config import Config, logger


@dataclass
class Procedure:
    name: str
    description: str
    func: Callable | None = None
    script_path: Optional[pathlib.Path] = None
    params: dict[str, str] = field(default_factory=dict)
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
        return ProcedureDC(
            name=self.name,
            description=self.description,
            script_file_location=self.script_path.as_posix() if self.script_path is not None else "",
            parameters=(
                [ParameterDC(key, value=val) for key, val in self.params.items()] if self.params is not None else None
            ),
            input_file_var=self.input_file_var,
            input_file_type=self.input_file_type,
            export_file_type=self.export_file_type,
        )


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
    app: typer.Typer,
    input_file_var: str | None = None,
    input_file_type: FileTypeDC | None = None,
    export_file_type: FileTypeDC | None = None,
) -> Callable:
    def wrapper(func: Callable) -> Callable:
        func.input_file_var = input_file_var
        func.input_file_type = input_file_type
        func.export_file_type = export_file_type
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
    params = {}
    for arg in main_func.args.args:
        arg_name = arg.arg
        if arg.annotation:
            arg_type = ast.unparse(arg.annotation)
        else:
            arg_type = "Any"
        params[arg_name] = arg_type

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

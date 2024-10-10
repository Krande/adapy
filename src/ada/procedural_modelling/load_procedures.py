from __future__ import annotations

import ast
import pathlib
from typing import Callable

from ada.comms.fb_model_gen import (
    ArrayTypeDC,
    FileTypeDC,
    ParameterDC,
    ParameterTypeDC,
    ValueDC,
)

from .procedure_model import Procedure


def get_procedures_from_script_dir(script_dir: pathlib.Path) -> dict[str, Procedure]:
    procedures = {}
    for script in script_dir.glob("*.py"):
        if script.stem == "__init__":
            continue
        procedures[script.stem] = get_procedure_from_script(script)

    return procedures


def get_procedure_from_function(func: Callable) -> Procedure:
    return Procedure(name=func.__name__, description=func.__doc__, func=func)


def str_to_filetype(filetype: str) -> FileTypeDC:
    if filetype == "IFC":
        return FileTypeDC.IFC
    elif filetype == "GLB":
        return FileTypeDC.GLB
    else:
        raise NotImplementedError(f"Filetype {filetype} not implemented")


def extract_decorator_options(decorator: ast.Call) -> dict[str, str | FileTypeDC | None]:
    options = dict(input_file_type=None, export_file_type=None)
    if decorator.func.id == "component_decorator":
        options["is_component"] = True
    else:
        options["is_component"] = False

    for keyword in decorator.keywords:
        if keyword.arg == "input_file_type":
            options["input_file_type"] = str_to_filetype(keyword.value.attr)
        elif keyword.arg == "export_file_type":
            options["export_file_type"] = str_to_filetype(keyword.value.attr)
        elif keyword.arg == "options":
            opts_dict = {}
            for key, value in zip(keyword.value.keys, keyword.value.values):
                values = []
                for elt in value.elts:
                    if isinstance(elt.value, str):
                        values.append(ValueDC(string_value=elt.value))
                    elif isinstance(elt.value, int):
                        values.append(ValueDC(integer_value=elt.value))
                    elif isinstance(elt.value, float):
                        values.append(ValueDC(float_value=elt.value))
                    elif isinstance(elt.value, bool):
                        values.append(ValueDC(boolean_value=elt.value))
                    else:
                        raise NotImplementedError(f"Value type {type(elt.value)} not implemented")
                opts_dict[key.s] = values

            options["options"] = opts_dict

    return options


def arg_to_param(arg: ast.arg, default: ast.expr, decorator_config: dict) -> ParameterDC:
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
    elif arg_type.startswith("tuple") or arg_type.startswith("list") or arg_type.startswith("set"):
        param_type = ParameterTypeDC.ARRAY
        result = arg_type.split("[")[1].split("]")[0]
        value_types = [r.strip() for r in result.split(",")]
        value_type = value_types[0]
        array_value_type = _param_type_map.get(value_type, ParameterTypeDC.UNKNOWN)
        if arg_type.startswith("tuple"):
            array_type = ArrayTypeDC.TUPLE
        elif arg_type.startswith("list"):
            array_type = ArrayTypeDC.LIST
        elif arg_type.startswith("set"):
            array_type = ArrayTypeDC.SET
        else:
            raise NotImplementedError(f"Parameter type {arg_type} not implemented")

        array_is_any_length = False
        if len(value_types) > 1 and value_types[1] == "...":
            array_is_any_length = True

        default_values = None
        if default_arg_value is not None:
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

        default_value = ValueDC(
            array_value=default_values,
            array_length=len(value_types),
            array_type=array_type,
            array_value_type=array_value_type,
            array_any_length=array_is_any_length,
        )
    else:
        raise NotImplementedError(f"Parameter type {arg_type} not implemented")

    options = decorator_config.get("options", {}).get(arg_name, None)

    return ParameterDC(name=arg_name, type=param_type, default_value=default_value, options=options)


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

    # extract decorator (if any)
    decorator_config = {}
    if main_func.decorator_list:
        custom_decorator = [
            d for d in main_func.decorator_list if d.func.id in ("procedure_decorator", "component_decorator")
        ]
        if custom_decorator:
            decorator = custom_decorator[0]
            decorator_config = extract_decorator_options(decorator)

    # Extract parameters
    params: dict[str, ParameterDC] = {}
    for arg, default in zip(main_func.args.args, main_func.args.defaults):
        arg_name = arg.arg
        params[arg_name] = arg_to_param(arg, default, decorator_config)

    # Extract docstring
    description = ast.get_docstring(main_func) or ""

    return Procedure(
        name=script_path.stem,
        description=description,
        script_path=script_path,
        params=params,
        **decorator_config,
    )


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

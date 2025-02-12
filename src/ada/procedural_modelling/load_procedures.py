from __future__ import annotations

import ast
import io
import pathlib
import tokenize
from typing import Callable

from ada.comms.fb_wrap_model_gen import (
    ArrayTypeDC,
    FileArgDC,
    FileTypeDC,
    ParameterDC,
    ParameterTypeDC,
    ValueDC,
)

from .procedure_model import Procedure


def remove_comments_from_code(code: str) -> str:
    """Remove comments from code while preserving the code structure."""

    output_tokens = []
    last_lineno = -1
    last_col = 0

    g = tokenize.generate_tokens(io.StringIO(code).readline)
    for toknum, tokval, start, end, line in g:
        if toknum == tokenize.COMMENT:
            continue  # Skip comments
        elif toknum == tokenize.NL:
            output_tokens.append("\n")  # Add newlines
            last_lineno += 1
            last_col = 0
        else:
            if start[0] > last_lineno:
                # New line
                output_tokens.append("\n" * (start[0] - last_lineno))
                last_col = 0
            if start[1] > last_col:
                # Indentation or spaces
                output_tokens.append(" " * (start[1] - last_col))
            output_tokens.append(tokval)
            last_lineno, last_col = end

    return "".join(output_tokens)


def get_procedures_from_script_dir(script_dir: pathlib.Path) -> dict[str, Procedure]:
    procedures = {}
    for script in script_dir.rglob("*.py"):
        if script.stem == "__init__":
            continue
        procedure = get_procedure_from_script(script)
        if procedure is None:
            continue
        procedures[script.stem] = procedure

    return procedures


def get_procedure_from_function(func: Callable) -> Procedure:
    return Procedure(name=func.__name__, description=func.__doc__, func=func)


def str_to_filetype(filetype: str) -> FileTypeDC:
    if filetype == "IFC":
        return FileTypeDC.IFC
    elif filetype == "GLB":
        return FileTypeDC.GLB
    elif filetype == "XLSX":
        return FileTypeDC.XLSX
    elif filetype == "CSV":
        return FileTypeDC.CSV
    else:
        raise NotImplementedError(f"Filetype {filetype} not implemented")


def keyword_to_file_args(key_value: ast.Call) -> list[FileArgDC]:
    output = []
    for keyword in key_value.keywords:
        output.append(FileArgDC(keyword.arg, str_to_filetype(keyword.value.attr)))

    return output


def extract_decorator_options(decorator: ast.Call) -> dict[str, str | list[FileArgDC] | None]:
    options = dict(inputs={}, outputs={})
    if decorator.func.id == "ComponentDecorator":
        options["is_component"] = True
    else:
        options["is_component"] = False

    for keyword in decorator.keywords:
        if keyword.arg == "inputs":
            options["inputs"] = keyword_to_file_args(keyword.value)
        elif keyword.arg == "outputs":
            options["outputs"] = keyword_to_file_args(keyword.value)
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


def arg_to_param(arg: ast.arg, default: ast.expr | None, decorator_config: dict) -> ParameterDC:
    arg_name = arg.arg
    if arg.annotation:
        arg_type = ast.unparse(arg.annotation)
    else:
        arg_type = "Any"

    if default is None:
        default_arg_value = None
    elif isinstance(default, (ast.Tuple, ast.List)):
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


def get_procedure_from_script(script_path: pathlib.Path) -> Procedure | None:
    """This looks for functions with the @ComponentDecorator or @ProcedureDecorator decorator and returns a Procedure"""
    with open(script_path, "r") as f:
        # Step 1: Remove comments
        filtered_code = remove_comments_from_code(f.read())

    tree = ast.parse(filtered_code)

    main_func = None
    custom_decorator = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            main_func = node
            if main_func.decorator_list:
                custom_decorator = [
                    d for d in main_func.decorator_list if d.func.id in ("ProcedureDecorator", "ComponentDecorator")
                ]
            if custom_decorator is not None and len(custom_decorator) == 1:
                break

    if custom_decorator is None:
        return None

    # extract decorator (if any)
    decorator_config = {}
    if custom_decorator:
        decorator = custom_decorator[0]
        decorator_config = extract_decorator_options(decorator)

    # Extract parameters
    params: dict[str, ParameterDC] = {}
    if len(main_func.args.defaults) > 0:
        defaults = main_func.args.defaults
    else:
        defaults = [None] * len(main_func.args.args)

    for arg, default in zip(main_func.args.args, defaults):
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

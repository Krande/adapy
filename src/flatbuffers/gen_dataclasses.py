import pathlib
from collections import defaultdict

from fbs_serializer import FlatBufferSchema, load_fbs_file


def create_top_level_import_string(schema: FlatBufferSchema) -> str:
    import_str = "from __future__ import annotations\nfrom typing import Optional, List\n"
    if len(schema.enums) > 0:
        import_str += "from enum import Enum\n"
    if len(schema.tables) > 0:
        import_str += "from dataclasses import dataclass\n"

    has_paths = False
    for tbl in schema.tables:
        for field in tbl.fields:
            python_type, is_optional = convert_flatbuffer_type_to_python(field.field_type)
            if python_type == "str" and "path" in field.name:
                has_paths = True
                break
    if has_paths:
        import_str += "import pathlib\n"
    return import_str


# Function to generate Python dataclasses and enums from the FlatBufferSchema object
def generate_dataclasses_from_schema(schema: FlatBufferSchema, output_file: str | pathlib.Path = None) -> str:
    import_str = create_top_level_import_string(schema)
    result = [import_str]

    # add imports from other namespaces
    import_map = defaultdict(list)
    for tbl in schema.tables:
        for field in tbl.fields:
            if field.namespace is not None and field.namespace != schema.namespace:
                import_map[field.namespace].append(f"{field.field_type}DC")

    for namespace, values in import_map.items():
        imports = ", ".join(values)
        result.append(f"from {schema.py_root}.fb_{namespace}_gen import {imports}")

    result.append("\n\n")

    # Process Enums
    for enum_def in schema.enums:
        result.append(f"class {enum_def.name}DC(Enum):")
        for enum_field in enum_def.values:
            result.append(f"    {enum_field.name} = {enum_field.value}")
        result.append("")

    # Process Tables
    for table_def in schema.tables:
        result.append("@dataclass")
        result.append(f"class {table_def.name}DC:")
        for field in table_def.fields:
            python_type, is_optional = convert_flatbuffer_type_to_python(field.field_type)
            if python_type == "str" and "path" in field.name:
                python_type = "pathlib.Path | str"

            default_value = f" = {field.default_value}" if field.default_value else (" = None" if is_optional else "")

            # Mark fields as Optional if they're nullable
            if is_optional and not field.default_value:
                python_type = f"Optional[{python_type}]"

            if python_type == "str":
                default_value = ' = ""'
            elif python_type == "pathlib.Path | str":
                default_value = ' = ""'
            elif python_type == "int":
                default_value = " = None"
            elif python_type == "float":
                default_value = " = None"
            elif python_type == "bool":
                default_value = (
                    " = None"
                    if field.default_value is None
                    else f" = {field.default_value.replace('true', 'True').replace('false', 'False')}"
                )
            elif python_type == "bytes":
                default_value = " = None"
            elif python_type == "List[float]":
                default_value = " = None"
            elif python_type == "List[int]":
                default_value = " = None"
            elif python_type == "List[uint32]":
                default_value = " = None"

            result.append(f"    {field.name}: {python_type}{default_value}")
        result.append("")

    python_str = "\n".join(result)
    if output_file is not None:
        with open(output_file, "w") as ofile:
            ofile.write(python_str)

    return python_str


# Helper function to convert FlatBuffers type to Python types
def convert_flatbuffer_type_to_python(flat_type: str) -> tuple[str, bool]:
    flat_to_python = {
        "byte": "int",
        "[ubyte]": "bytes",
        "int": "int",
        "[uint32]": "List[int]",
        "string": "str",
        "bool": "bool",
        "float": "float",
        "[float]": "List[float]",
    }

    # If the field type is a table (i.e., a complex type), it's considered optional
    if flat_type not in flat_to_python.keys():
        if flat_type.startswith("["):
            return "List[" + flat_type[1:-1] + "DC]", True

        return flat_type + "DC", True  # Tables are optional (nullable) by default

    return flat_to_python.get(flat_type, flat_type), False


# Example usage:
if __name__ == "__main__":
    # Assuming the schema is already parsed into FlatBufferSchema object
    fbs_file = "schemas/commands.fbs"  # Replace with your .fbs file path
    fbs_schema = load_fbs_file(fbs_file)

    # Write the generated code to a Python file
    tmp_dir = pathlib.Path("temp")
    tmp_dir.mkdir(exist_ok=True)

    python_code = generate_dataclasses_from_schema(fbs_schema, tmp_dir / "fb_model_gen.py")
    print("Python dataclasses and enums generated successfully.")

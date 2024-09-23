import pathlib

from fbs_serializer import FlatBufferSchema, parse_fbs_file

import_str = """from enum import Enum
from dataclasses import dataclass
from typing import Optional, List
import pathlib


"""


# Function to generate Python dataclasses and enums from the FlatBufferSchema object
def generate_dataclasses_from_schema(schema: FlatBufferSchema, output_file: str | pathlib.Path = None) -> str:
    result = [import_str]

    # Process Enums
    for enum_def in schema.enums:
        result.append(f'class {enum_def.name}DC(Enum):')
        for enum_field in enum_def.values:
            result.append(f'    {enum_field.name} = {enum_field.value}')
        result.append('')

    # Process Tables
    for table_def in schema.tables:
        result.append(f'@dataclass')
        result.append(f'class {table_def.name}DC:')
        has_optional = False
        for field in table_def.fields:
            python_type, is_optional = convert_flatbuffer_type_to_python(field.field_type)
            if python_type == 'str' and 'path' in field.name:
                python_type = 'pathlib.Path | str'

            default_value = f' = {field.default_value}' if field.default_value else (' = None' if is_optional else '')

            # Mark fields as Optional if they're nullable
            if is_optional and not field.default_value:
                python_type = f'Optional[{python_type}]'
                has_optional = True
            else:
                if has_optional:
                    if python_type == "str":
                        default_value = ' = ""'
                    elif python_type == "pathlib.Path | str":
                        default_value = ' = ""'
                    elif python_type == "int":
                        default_value = ' = None'
                    elif python_type == "bool":
                        default_value = ' = None'
                    elif python_type == "bytes":
                        default_value = ' = None'

            result.append(f'    {field.name}: {python_type}{default_value}')
        result.append('')

    python_str = '\n'.join(result)
    if output_file is not None:
        with open(output_file, 'w') as ofile:
            ofile.write(python_str)

    return python_str


# Helper function to convert FlatBuffers type to Python types
def convert_flatbuffer_type_to_python(flat_type: str) -> tuple[str, bool]:
    flat_to_python = {
        'byte': 'int',
        '[ubyte]': 'bytes',
        'int': 'int',
        'string': 'str',
        'bool': 'bool'
    }

    # If the field type is a table (i.e., a complex type), it's considered optional
    if flat_type not in flat_to_python.keys():
        if flat_type.startswith('['):
            return 'List[' + flat_type[1:-1] + 'DC]', True

        return flat_type + 'DC', True  # Tables are optional (nullable) by default

    return flat_to_python.get(flat_type, flat_type), False


# Example usage:
if __name__ == "__main__":
    # Assuming the schema is already parsed into FlatBufferSchema object
    fbs_file = 'schemas/commands.fbs'  # Replace with your .fbs file path
    fbs_schema = parse_fbs_file(fbs_file)

    # Write the generated code to a Python file
    tmp_dir = pathlib.Path('temp')
    tmp_dir.mkdir(exist_ok=True)

    python_code = generate_dataclasses_from_schema(fbs_schema, tmp_dir / 'fb_model_gen.py')
    print("Python dataclasses and enums generated successfully.")

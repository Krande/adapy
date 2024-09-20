import pathlib
import re

import_str = """from enum import Enum
from dataclasses import dataclass
from typing import Optional

"""


# Function to strip comments starting with '//' from the FlatBuffers schema
def strip_comments(fbs_content: str) -> str:
    # Remove lines starting with '//' and any inline comments
    return re.sub(r'//.*', '', fbs_content)


# Function to parse an .fbs file and generate Python dataclasses and enums
def parse_fbs_file(fbs_file: str) -> str:
    with open(fbs_file, 'r') as file:
        fbs_content = file.read()

    # Remove comments from the fbs content
    fbs_content = strip_comments(fbs_content)

    # Pattern to match enum blocks
    enum_pattern = r'enum (\w+) : \w+ \{([^\}]+)\}'
    # Pattern to match table blocks
    table_pattern = r'table (\w+) \{([^\}]+)\}'

    enums = re.findall(enum_pattern, fbs_content)
    tables = re.findall(table_pattern, fbs_content)

    result = []

    # Process Enums
    for enum_name, enum_values in enums:
        result.append(f'class {enum_name}DC(Enum):')
        values = [v.strip() for v in enum_values.split(',') if v.strip()]
        for value in values:
            name, val = value.split('=') if '=' in value else (value, None)
            val = val.strip() if val else values.index(value)
            result.append(f'    {name.strip()} = {val}')
        result.append('')

    # Process Tables
    for table_name, table_fields in tables:
        result.append(f'@dataclass')
        result.append(f'class {table_name}DC:')
        fields = [f.strip() for f in table_fields.split(';') if f.strip()]
        has_optional = False
        for field in fields:
            field_name, field_type = field.split(':')
            field_type = field_type.strip().replace(' ', '')

            # Handle field types and optional fields
            field_name = field_name.strip()
            field_default = ''
            if '=' in field_type:
                field_type, field_default = field_type.split('=')
                field_type, field_default = field_type.strip(), field_default.strip()

            python_type, is_optional = convert_flatbuffer_type_to_python(field_type)
            default_value = f' = {field_default}' if field_default else (' = None' if is_optional else '')

            # Mark fields as Optional if they're nullable
            if is_optional and not field_default:
                python_type = f'Optional[{python_type}]'
                has_optional = True
            else:
                if has_optional:
                    if python_type == "str":
                        default_value = ' = None'
                    elif python_type == "int":
                        default_value = ' = None'
                    elif python_type == "bool":
                        default_value = ' = None'
                    elif python_type == "bytes":
                        default_value = ' = None'

            result.append(f'    {field_name}: {python_type}{default_value}')

        result.append('')

    return import_str + '\n'.join(result)


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
    if flat_type not in flat_to_python:
        return flat_type + 'DC', True  # Tables are optional (nullable) by default

    return flat_to_python.get(flat_type, flat_type), False

# Example usage:
if __name__ == "__main__":
    fbs_file = 'schemas/commands.fbs'  # Replace with your .fbs file path
    python_code = parse_fbs_file(fbs_file)

    # Write the generated code to a Python file
    tmp_dir = pathlib.Path('temp')
    tmp_dir.mkdir(exist_ok=True)

    with open(tmp_dir / 'generated_dataclasses.py', 'w') as output_file:
        output_file.write(python_code)

    print("Python dataclasses and enums generated successfully.")

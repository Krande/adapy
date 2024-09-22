import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EnumField:
    name: str
    value: int


@dataclass
class TableField:
    name: str
    field_type: str  # Can be a primitive type, a complex type (another table), or an enum
    default_value: str | None = None
    has_default: bool = False


@dataclass
class EnumDefinition:
    name: str
    values: List[EnumField]


@dataclass
class TableDefinition:
    name: str
    fields: List[TableField]


@dataclass
class FlatBufferSchema:
    enums: List[EnumDefinition]
    tables: List[TableDefinition]
    root_type: Optional[str]  # The root table type<

# Function to parse enums and tables from the .fbs file and represent them as dataclasses
def parse_fbs_file(fbs_file: str) -> FlatBufferSchema:
    with open(fbs_file, 'r') as file:
        fbs_content = file.read()

    # Remove comments
    fbs_content = re.sub(r'//.*', '', fbs_content)

    # Pattern to match enum blocks
    enum_pattern = r'enum (\w+) : \w+ \{([^\}]+)\}'
    # Pattern to match table blocks
    table_pattern = r'table (\w+) \{([^\}]+)\}'
    # Pattern to find root_type declaration
    root_pattern = r'root_type (\w+);'

    # Find all enums and tables
    enums = re.findall(enum_pattern, fbs_content)
    tables = re.findall(table_pattern, fbs_content)
    root_match = re.search(root_pattern, fbs_content)

    root_type = root_match.group(1) if root_match else None

    # Process enums
    parsed_enums = []
    for enum_name, enum_values in enums:
        enum_fields = []
        for enum_value in enum_values.split(','):
            enum_value = enum_value.strip()
            if '=' in enum_value:
                name, value = enum_value.split('=')
                if name.strip() == "":
                    continue
                enum_fields.append(EnumField(name.strip(), int(value.strip())))
            else:
                name = enum_value
                if name.strip() == "":
                    continue
                value = len(enum_fields)  # Default enum value (0-based)
                enum_fields.append(EnumField(name.strip(), value))
        parsed_enums.append(EnumDefinition(name=enum_name, values=enum_fields))

    # Process tables
    parsed_tables = []
    for table_name, table_fields in tables:
        fields = []
        for field in table_fields.split(';'):
            field = field.strip()
            if not field:
                continue
            field_name, field_type = field.split(':')
            has_default = False
            default_value = None
            if "=" in field_type:
                field_type, default_value = field_type.split("=")
                field_type = field_type.strip()
                default_value = default_value.strip()
                has_default = True
            fields.append(
                TableField(
                    name=field_name.strip(),
                    field_type=field_type.strip(),
                    default_value=default_value,
                    has_default=has_default
                )
            )
        parsed_tables.append(TableDefinition(name=table_name, fields=fields))

    return FlatBufferSchema(enums=parsed_enums, tables=parsed_tables, root_type=root_type)

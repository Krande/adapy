from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import List, Optional

from config import logger


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
    namespace: str | None = None

    @property
    def is_array(self) -> bool:
        return self.field_type.startswith("[") and self.field_type.endswith("]")

    @property
    def field_type_without_array(self) -> str:
        if self.is_array:
            return self.field_type[1:-1]
        return self.field_type


@dataclass
class EnumDefinition:
    name: str
    values: List[EnumField]
    schema: Optional["FlatBufferSchema"] = None


@dataclass
class TableDefinition:
    name: str
    fields: List[TableField]
    schema: Optional["FlatBufferSchema"] = None

    def get_all_tables(self) -> list[TableDefinition]:
        if self.schema is None:
            logger.warning("parent schema is None")
            return [self]
        all_included_tables = [tbl for tbl in self.schema.tables]
        for incl in self.schema.includes:
            all_included_tables.extend(incl.tables)
        return all_included_tables


@dataclass
class FlatBufferSchema:
    file_path: pathlib.Path
    namespace: str | None
    py_root: str
    enums: List[EnumDefinition]
    tables: List[TableDefinition]
    root_type: Optional[str]  # The root table type<
    includes: List[FlatBufferSchema]

    def __post_init__(self):
        for tbl in self.tables:
            tbl.schema = self

        for enum in self.enums:
            enum.schema = self

    def get_include_from_namespace(self, namespace: str) -> Optional[FlatBufferSchema]:
        for incl in self.includes:
            if incl.namespace == namespace:
                return incl

    def get_all_namespaces(self) -> list[str]:
        all_namespaces = [self.namespace] if self.namespace else []
        for incl in self.includes:
            if incl.namespace:
                all_namespaces.append(incl.namespace)
        return all_namespaces

    def get_all_included_tables(self) -> list[TableDefinition]:
        all_tables = [tbl for tbl in self.tables]
        for incl in self.includes:
            all_tables.extend(incl.tables)
        return all_tables

    def get_all_included_enums(self) -> list[EnumDefinition]:
        all_enums = [enum for enum in self.enums]
        for incl in self.includes:
            all_enums.extend(incl.enums)
        return all_enums


# Function to parse enums and tables from the .fbs file and represent them as dataclasses
def load_fbs_file(
    fbs_file: str, py_root: str, includes: list[FlatBufferSchema] | None = None
) -> FlatBufferSchema:
    if isinstance(fbs_file, str):
        fbs_file = pathlib.Path(fbs_file)

    with open(fbs_file, "r") as file:
        fbs_content = file.read()

    parsed_enums = []
    parsed_tables = []
    includes = [] if includes is None else includes

    # get namespace
    namespace_pattern = r"namespace\s+(\w+)"
    namespace_result = re.search(namespace_pattern, fbs_content)
    namespace = None
    if namespace_result:
        namespace = namespace_result.group(1)
        namespace = namespace.strip()

    # loop over include files
    include_pattern = r'include "(.*?)"'
    include_files = re.findall(include_pattern, fbs_content)
    included_files = [incf.file_path.name for incf in includes]
    for include_file in include_files:
        if include_file in included_files:
            continue
        fbs_schema = load_fbs_file(fbs_file.parent / include_file, py_root=py_root, includes=includes)
        includes.append(fbs_schema)

    # Remove comments
    fbs_content = re.sub(r"//.*", "", fbs_content)

    # Pattern to match enum blocks
    enum_pattern = r"enum (\w+) : \w+ \{([^\}]+)\}"
    # Pattern to match table blocks
    table_pattern = r"table (\w+) \{([^\}]+)\}"
    # Pattern to find root_type declaration
    root_pattern = r"root_type (\w+);"

    # Find all enums and tables
    enums = re.findall(enum_pattern, fbs_content)
    tables = re.findall(table_pattern, fbs_content)
    root_match = re.search(root_pattern, fbs_content)

    root_type = root_match.group(1) if root_match else None

    # Process enums
    for enum_name, enum_values in enums:
        enum_fields = []
        for enum_value in enum_values.split(","):
            enum_value = enum_value.strip()
            if "=" in enum_value:
                name, value = enum_value.split("=")
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
    for table_name, table_fields in tables:
        fields = []
        for field in table_fields.split(";"):
            field = field.strip()
            if not field:
                continue
            field_name, field_type = field.split(":")
            has_default = False
            default_value = None
            if "=" in field_type:
                field_type, default_value = field_type.split("=")
                field_type = field_type.strip()
                default_value = default_value.strip()
                has_default = True

            field_namespace = None
            if "." in field_type:
                field_namespace, field_type = field_type.split(".")
                if "]" in field_type:
                    field_type = "[" + field_type
                field_namespace = field_namespace.strip().replace("[", "").replace("]", "")

            fields.append(
                TableField(
                    name=field_name.strip(),
                    field_type=field_type.strip(),
                    default_value=default_value,
                    has_default=has_default,
                    namespace=field_namespace,
                )
            )
        parsed_tables.append(TableDefinition(name=table_name, fields=fields))

    return FlatBufferSchema(
        fbs_file,
        namespace,
        py_root=py_root,
        enums=parsed_enums,
        tables=parsed_tables,
        root_type=root_type,
        includes=includes,
    )

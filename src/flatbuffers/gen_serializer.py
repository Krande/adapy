import pathlib
from collections import defaultdict

from fbs_serializer import FlatBufferSchema, TableDefinition, load_fbs_file
from config import logger
from utils import make_camel_case


# Function to strip comments from the FlatBuffers schema

def uint32_serialize_code(field_name: str, builder_name: str, head_spacing: int) -> str:
    space = " " * head_spacing
    s = ""
    s += f"{space}{builder_name}.Start{make_camel_case(field_name)}Vector(builder, len(obj.{field_name}))"
    s += f"\n{space}for item in reversed(obj.{field_name}):"
    s += f"\n{space}    builder.PrependUint32(item)"
    s += f"\n{space}{field_name}_vector = builder.EndVector(len(obj.{field_name}))\n"

    return s


def float32_serialize_code(field_name: str, builder_name: str, head_spacing: int) -> str:
    space = " " * head_spacing
    s = ""
    s += f"{space}{builder_name}.Start{make_camel_case(field_name)}Vector(builder, len(obj.{field_name}))"
    s += f"\n{space}for item in reversed(obj.{field_name}):"
    s += f"\n{space}    builder.PrependFloat32(item)"
    s += f"\n{space}{field_name}_vector = builder.EndVector(len(obj.{field_name}))\n"

    return s


def generate_serialize_function(table: TableDefinition) -> str:
    table_names = [tbl.name for tbl in table.schema.tables]
    enum_names = [enum.name for enum in table.schema.enums]

    serialize_code = f"def serialize_{table.name.lower()}(builder: flatbuffers.Builder, obj: Optional[{table.name}DC]) -> Optional[int]:\n"
    serialize_code += "    if obj is None:\n        return None\n"

    # Handle string and vector serialization first
    for field in table.fields:
        if field.field_type == "string":
            serialize_code += f"    {field.name}_str = None\n"
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {field.name}_str = builder.CreateString(str(obj.{field.name}))\n"
        elif field.field_type.startswith("["):
            field_type_value = field.field_type[1:-1]
            if field_type_value == "float":
                serialize_code += float32_serialize_code(field.name, table.name, 4)
            elif field_type_value == "ubyte":
                serialize_code += f"    {field.name}_vector = None\n"
                serialize_code += f"    if obj.{field.name} is not None:\n"
                serialize_code += f"        {field.name}_vector = builder.CreateByteVector(obj.{field.name})\n"
            elif field_type_value in table_names:
                serialize_code += f"    {field.name}_vector = None\n"
                serialize_code += f"    if obj.{field.name} is not None and len(obj.{field.name}) > 0:\n"
                serialize_code += f"        {field.name}_list = [serialize_{field_type_value.lower()}(builder, item) for item in obj.{field.name}]\n"
                serialize_code += (
                    f"        {table.name}.Start{make_camel_case(field.name)}Vector(builder, len({field.name}_list))\n"
                )
                serialize_code += f"        for item in reversed({field.name}_list):\n"
                serialize_code += "            builder.PrependUOffsetTRelative(item)\n"
                serialize_code += f"        {field.name}_vector = builder.EndVector(len({field.name}_list))\n"
            elif field_type_value == "uint32":
                serialize_code += uint32_serialize_code(field.name, table.name, 4)
            else:
                raise NotImplementedError(f"Unknown field type: {field.field_type}")
        elif field.field_type in table_names:
            serialize_code += f"    {field.name}_obj = None\n"
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += (
                f"        {field.name}_obj = serialize_{field.field_type.lower()}(builder, obj.{field.name})\n"
            )
        elif field.field_type in enum_names:
            pass
        elif field.field_type in ["byte", "ubyte", "int", "bool", "float"]:
            pass
        else:
            logger.info(f"Unknown field type: {field.field_type}")

    serialize_code += f"\n    {table.name}.Start(builder)\n"

    # Add fields to FlatBuffer
    for field in table.fields:
        if field.field_type == "string":
            serialize_code += f"    if {field.name}_str is not None:\n"
            serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_str)\n"
        elif field.field_type.startswith("["):
            field_type_value = field.field_type[1:-1]
            if field_type_value == "ubyte":
                serialize_code += f"    if {field.name}_vector is not None:\n"
                serialize_code += (
                    f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_vector)\n"
                )
            elif field_type_value == "float":
                serialize_code += f"    if obj.{field.name} is not None:\n"
                serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_vector)\n"
            elif field_type_value in table_names:
                serialize_code += f"    if obj.{field.name} is not None and len(obj.{field.name}) > 0:\n"
                serialize_code += (
                    f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_vector)\n"
                )
            elif field_type_value == "uint32":
                serialize_code += f"    if {field.name}_vector is not None:\n"
                serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_vector)\n"
            else:
                raise NotImplementedError(f"Unknown field type: {field.field_type}")

        elif field.field_type in ["byte", "ubyte", "int", "bool", "float"]:
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, obj.{field.name})\n"
        elif field.field_type in table_names:
            # Handle enum or nested table
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_obj)\n"
        elif field.field_type in enum_names:
            # Handle enum or nested table
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += (
                f"        {table.name}.Add{make_camel_case(field.name)}(builder, obj.{field.name}.value)\n"
            )
        elif field.namespace is not None:
            # the serialized object is from another namespace and therefore the serialization function is imported
            pass
        else:
            logger.warning(f"Unknown field type: {field.field_type}")

    serialize_code += f"    return {table.name}.End(builder)\n"
    return serialize_code


# Function to generate the serialize function for the root type (previously "Message")
def generate_serialize_root_function(schema: FlatBufferSchema) -> str:
    if schema.root_type is None:
        logger.info(f"No root_type declared in the .fbs schema file {schema.file_path}")
        return ""

    table_names = [tbl.name for tbl in schema.tables]
    enum_names = [enum.name for enum in schema.enums]
    included_table_names = [tbl.name for incl in schema.includes for tbl in incl.tables]
    included_enum_names = [enum.name for incl in schema.includes for enum in incl.enums]

    # Find the root table
    root_table = next(table for table in schema.tables if table.name == schema.root_type)

    serialize_code = f"def serialize_root_{root_table.name.lower()}(message: {root_table.name}DC, builder: flatbuffers.Builder=None) -> bytes:\n"
    serialize_code += "    if builder is None:\n        builder = flatbuffers.Builder(1024)\n"

    for field in root_table.fields:
        if field.field_type == "string":
            serialize_code += f"    {field.name}_str = None\n"
            serialize_code += f"    if message.{field.name} is not None:\n"
            serialize_code += f"        {field.name}_str = builder.CreateString(message.{field.name})\n"
        elif field.field_type in table_names:
            serialize_code += f"    {field.name}_obj = None\n"
            serialize_code += f"    if message.{field.name} is not None:\n"
            serialize_code += (
                f"        {field.name}_obj = serialize_{field.field_type.lower()}(builder, message.{field.name})\n"
            )
        elif field.field_type in enum_names:
            pass
        elif field.field_type in ["int", "byte", "ubyte"]:
            pass
        elif field.namespace is not None:
            if field.field_type in included_table_names:
                serialize_code += f"    {field.name}_obj = None\n"
                serialize_code += f"    if message.{field.name} is not None:\n"
                serialize_code += (
                    f"        {field.name}_obj = serialize_{field.field_type.lower()}(builder, message.{field.name})\n"
                )
            elif field.field_type in included_enum_names:
                pass
        else:
            logger.info(f"Unknown field type: {field.field_type}")
    # Handle string serialization first
    serialize_code += f"\n    {root_table.name}.Start(builder)\n"

    # Add fields to FlatBuffer
    for field in root_table.fields:
        serialize_code += f"    if message.{field.name} is not None:\n"
        if field.field_type in ["int", "byte", "ubyte"]:
            serialize_code += (
                f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, message.{field.name})\n"
            )
        elif field.field_type == "string":
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_str)\n"
        elif field.field_type in table_names:
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_obj)\n"
        elif field.field_type in enum_names:
            serialize_code += (
                f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, message.{field.name}.value)\n"
            )
        elif field.field_type.startswith("["):
            field_type_value = field.field_type[1:-1].lower()
            serialize_code += f"        {field_type_value}_list = [serialize_{field_type_value}(builder, item) for item in message.{field.name}]\n"
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, builder.CreateByteVector({field_type_value}_list))\n"
        elif field.namespace is not None:
            if field.field_type in included_table_names:
                serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_obj)\n"
            elif field.field_type in included_enum_names:
                serialize_code += (
                    f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, message.{field.name}.value)\n"
                )
        else:
            raise ValueError(f"Unknown field type: {field.field_type}")

    serialize_code += f"\n    {schema.root_type.lower()}_flatbuffer = {root_table.name}.End(builder)\n"
    serialize_code += f"    builder.Finish({schema.root_type.lower()}_flatbuffer)\n"
    serialize_code += "    return bytes(builder.Output())\n"

    return serialize_code


def add_imports(schema: FlatBufferSchema, wsock_model_root, dc_model_root) -> str:
    imports = f"from {wsock_model_root} import "
    imports += ", ".join([f"{table.name}" for table in schema.tables])
    imports += "\n\n"
    imports += f"from {dc_model_root} import "
    imports += ", ".join([f"{table.name}DC" for table in schema.tables])
    imports += "\n\n"

    # add serialization function from other namespaces
    import_map = defaultdict(list)
    for tbl in schema.tables:
        for field in tbl.fields:
            if field.namespace is not None and field.namespace != schema.namespace:
                import_map[field.namespace].append(field)

    for namespace, values in import_map.items():
        imports += f"from {schema.py_root}.fb_{namespace}_serializer import "
        imports += ", ".join([f"serialize_{field.field_type.lower()}" for field in values])
        imports += "\n"

    imports += "\n"

    return imports


def generate_serialization_code(fbs_schema: str | FlatBufferSchema, output_file: str | pathlib.Path, import_root, dc_model_root):
    if isinstance(fbs_schema, str | pathlib.Path):
        fbs_schema = load_fbs_file(fbs_schema)

    imports_str = add_imports(fbs_schema, import_root, dc_model_root)

    with open(output_file, "w") as out_file:
        out_file.write("import flatbuffers\nfrom typing import Optional\n\n")
        out_file.write(imports_str)
        # Write serialization functions for each table
        for table in fbs_schema.tables:
            # if table.name == fbs_schema.root_type:
            #     continue
            out_file.write(generate_serialize_function(table))
            out_file.write("\n\n")

        # Write the serialize function for the root_type
        serialize_str = generate_serialize_root_function(fbs_schema)
        if serialize_str is not None:
            out_file.write(serialize_str)

    print(f"Serialization code generated and saved to {output_file}")


# Example usage
if __name__ == "__main__":
    # Write the generated code to a Python file
    tmp_dir = pathlib.Path("temp")
    tmp_dir.mkdir(exist_ok=True)

    generate_serialization_code("schemas/commands.fbs", tmp_dir / "fb_serializer.py", "ada.comms.wsock", "fb_model_gen")

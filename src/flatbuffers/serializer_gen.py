import pathlib

from fbs_serializer import TableDefinition, FlatBufferSchema, parse_fbs_file
from utils import make_camel_case


# Function to strip comments from the FlatBuffers schema


def generate_serialize_function(table: TableDefinition) -> str:
    serialize_code = f"def serialize_{table.name.lower()}(builder: flatbuffers.Builder, obj: Optional[{table.name}DC]) -> Optional[int]:\n"
    serialize_code += "    if obj is None:\n        return None\n"

    # Handle string and vector serialization first
    for field in table.fields:
        if field.field_type not in ["string", "[ubyte]"]:
            continue

        if field.field_type == "string":
            serialize_code += f"    {field.name}_str = None\n"
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {field.name}_str = builder.CreateString(obj.{field.name})\n"
        elif field.field_type.startswith("[") and "ubyte" in field.field_type:
            serialize_code += f"    {field.name}_vector = None\n"
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {field.name}_vector = builder.CreateByteVector(obj.{field.name})\n"

    serialize_code += f"\n    {table.name}.Start(builder)\n"

    # Add fields to FlatBuffer
    for field in table.fields:
        if field.field_type == "string":
            serialize_code += f"    if {field.name}_str is not None:\n"
            serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_str)\n"
        elif field.field_type.startswith("["):
            if "ubyte" in field.field_type:
                serialize_code += f"    if {field.name}_vector is not None:\n"
                serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_vector)\n"
            else:
                raise NotImplementedError()
        elif field.field_type in ["byte", "ubyte", "int", "bool"]:
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, obj.{field.name})\n"
        else:
            # Handle enum or nested table
            serialize_code += f"    if obj.{field.name} is not None:\n"
            serialize_code += f"        {table.name}.Add{make_camel_case(field.name)}(builder, obj.{field.name}.value)\n"

    serialize_code += f"    return {table.name}.End(builder)\n"
    return serialize_code


# Function to generate the serialize function for the root type (previously "Message")
def generate_serialize_root_function(schema: FlatBufferSchema) -> str:
    if schema.root_type is None:
        raise ValueError("No root_type declared in the .fbs schema")

    table_names = [tbl.name for tbl in schema.tables]
    enum_names = [enum.name for enum in schema.enums]

    # Find the root table
    root_table = next(table for table in schema.tables if table.name == schema.root_type)

    serialize_code = f"def serialize_{root_table.name.lower()}(message: {root_table.name}DC, builder: flatbuffers.Builder=None) -> bytes:\n"
    serialize_code += "    if builder is None:\n        builder = flatbuffers.Builder(1024)\n"

    for field in root_table.fields:
        if field.field_type == "string":
            serialize_code += f"    {field.name}_str = None\n"
            serialize_code += f"    if message.{field.name} is not None:\n"
            serialize_code += f"        {field.name}_str = builder.CreateString(message.{field.name})\n"
        elif field.field_type in table_names:
            serialize_code += f"    {field.name}_obj = None\n"
            serialize_code += f"    if message.{field.name} is not None:\n"
            serialize_code += f"        {field.name}_obj = serialize_{field.field_type.lower()}(builder, message.{field.name})\n"

    # Handle string serialization first
    serialize_code += f"\n    {root_table.name}.Start(builder)\n"

    # Add fields to FlatBuffer
    for field in root_table.fields:
        serialize_code += f"    if message.{field.name} is not None:\n"
        if field.field_type in ["int", "byte", "ubyte"]:
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, message.{field.name})\n"
        elif field.field_type == "string":
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_str)\n"
        elif field.field_type in table_names:
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, {field.name}_obj)\n"
        elif field.field_type in enum_names:
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, message.{field.name}.value)\n"
        elif field.field_type.startswith("["):
            field_type_value = field.field_type[1:-1].lower()
            serialize_code += f"        {field_type_value}_list = [serialize_{field_type_value}(builder, item) for item in message.{field.name}]\n"
            serialize_code += f"        {root_table.name}.Add{make_camel_case(field.name)}(builder, builder.CreateByteVector({field_type_value}_list))\n"
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
    return imports


def generate_serialization_code(fbs_file: str, output_file: str | pathlib.Path, wsock_model_root, dc_model_root):
    schema = parse_fbs_file(fbs_file)
    imports_str = add_imports(schema, wsock_model_root, dc_model_root)

    with open(output_file, 'w') as out_file:
        out_file.write("import flatbuffers\nfrom typing import Optional\n\n")
        out_file.write(imports_str)
        # Write serialization functions for each table
        for table in schema.tables:
            if table.name == "Message":
                continue
            out_file.write(generate_serialize_function(table))
            out_file.write("\n\n")

        # Write the serialize function for the root_type
        out_file.write(generate_serialize_root_function(schema))

    print(f"Serialization code generated and saved to {output_file}")


# Example usage
if __name__ == '__main__':
    # Write the generated code to a Python file
    tmp_dir = pathlib.Path('temp')
    tmp_dir.mkdir(exist_ok=True)

    generate_serialization_code(
        'schemas/commands.fbs',
        tmp_dir / "fb_serializer.py",
        "ada.comms.wsock",
        "fb_model_gen"
    )

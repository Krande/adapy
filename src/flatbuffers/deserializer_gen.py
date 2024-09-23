import pathlib

from fbs_serializer import TableDefinition, FlatBufferSchema, parse_fbs_file
from utils import make_camel_case


def generate_deserialize_function(schema: FlatBufferSchema, table: TableDefinition) -> str:
    deserialize_code = f"def deserialize_{table.name.lower()}(fb_obj) -> {table.name}DC | None:\n"
    deserialize_code += f"    if fb_obj is None:\n        return None\n\n"
    deserialize_code += f"    return {table.name}DC(\n"

    # Loop over each field and generate deserialization logic
    for field in table.fields:
        if field.field_type == "string":
            deserialize_code += f"        {field.name}=fb_obj.{make_camel_case(field.name)}().decode('utf-8') if fb_obj.{make_camel_case(field.name)}() is not None else None,\n"
        elif field.field_type in ["int", "byte", "ubyte", "bool"]:
            deserialize_code += f"        {field.name}=fb_obj.{make_camel_case(field.name)}(),\n"
        elif field.field_type.startswith("["):
            if "ubyte" in field.field_type:
                deserialize_code += f"        {field.name}=bytes(fb_obj.{make_camel_case(field.name)}AsNumpy()) if fb_obj.{make_camel_case(field.name)}Length() > 0 else None,\n"
            else:
                field_type_value = field.field_type[1:-1].lower()
                deserialize_code += f"        {field.name}=[deserialize_{field_type_value}(fb_obj.{make_camel_case(field.name)}(i)) for i in range(fb_obj.{make_camel_case(field.name)}Length())] if fb_obj.{make_camel_case(field.name)}Length() > 0 else None,\n"
        else:
            # Handle nested tables or enums
            if field.field_type in [en.name for en in schema.enums]:
                deserialize_code += f"        {field.name}={field.field_type}DC(fb_obj.{make_camel_case(field.name)}()),\n"
            else:
                deserialize_code += f"        {field.name}=deserialize_{field.field_type.lower()}(fb_obj.{make_camel_case(field.name)}()),\n"

    deserialize_code = deserialize_code.rstrip(",\n") + "\n"
    deserialize_code += "    )\n"

    return deserialize_code


def generate_deserialize_root_function(schema: FlatBufferSchema) -> str:
    if schema.root_type is None:
        raise ValueError("No root_type declared in the .fbs schema")

    # Find the root table
    root_table = next(table for table in schema.tables if table.name == schema.root_type)
    deserialize_code = f"def deserialize_root_{root_table.name.lower()}(bytes_obj: bytes) -> {root_table.name}DC:\n"
    deserialize_code += f"    fb_obj = {root_table.name}.{root_table.name}.GetRootAs{root_table.name}(bytes_obj, 0)\n"
    deserialize_code += f"    return deserialize_{root_table.name.lower()}(fb_obj)\n"

    return deserialize_code


def add_imports(schema: FlatBufferSchema, wsock_model_root, dc_model_root) -> str:
    imports = f"from {wsock_model_root} import "
    imports += ", ".join([f"{table.name}" for table in schema.tables])
    imports += "," + ", ".join([f"{en.name}" for en in schema.enums])
    imports += "\n\n"
    imports += f"from {dc_model_root} import "
    imports += ", ".join([f"{table.name}DC" for table in schema.tables])
    imports += "," + ", ".join([f"{en.name}DC" for en in schema.enums])
    imports += "\n\n"
    return imports


def generate_deserialization_code(fbs_file: str, output_file: str | pathlib.Path, wsock_model_root, dc_model_root):
    schema = parse_fbs_file(fbs_file)
    imports_str = add_imports(schema, wsock_model_root, dc_model_root)

    with open(output_file, 'w') as out_file:
        out_file.write("import flatbuffers\nfrom typing import List\n\n")
        out_file.write(imports_str)

        # Write deserialization functions for each table
        for table in schema.tables:
            out_file.write(generate_deserialize_function(schema, table))
            out_file.write("\n\n")

        # Write the deserialize function for the root_type
        out_file.write(generate_deserialize_root_function(schema))

    print(f"Deserialization code generated and saved to {output_file}")


if __name__ == '__main__':
    tmp_dir = pathlib.Path('temp')
    tmp_dir.mkdir(exist_ok=True)

    generate_deserialization_code(
        'schemas/commands.fbs',
        tmp_dir / "fb_deserializer.py",
        "ada.comms.wsock",
        "fb_model_gen"
    )

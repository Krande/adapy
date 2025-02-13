import pathlib
from collections import defaultdict

from config import logger
from fbs_serializer import FlatBufferSchema, TableDefinition, load_fbs_file
from utils import make_camel_case


def generate_deserialize_function(schema: FlatBufferSchema, table: TableDefinition) -> str:
    table_names = [tbl.name for tbl in schema.tables]
    deserialize_code = f"def deserialize_{table.name.lower()}(fb_obj) -> {table.name}DC | None:\n"
    deserialize_code += "    if fb_obj is None:\n        return None\n\n"
    deserialize_code += f"    return {table.name}DC(\n"

    # Loop over each field and generate deserialization logic
    for field in table.fields:
        if field.field_type == "string":
            deserialize_code += f"        {field.name}=fb_obj.{make_camel_case(field.name)}().decode('utf-8') if fb_obj.{make_camel_case(field.name)}() is not None else None,\n"
        elif field.field_type in ["int", "byte", "ubyte", "bool", "float"]:
            deserialize_code += f"        {field.name}=fb_obj.{make_camel_case(field.name)}(),\n"
        elif field.field_type.startswith("["):
            field_type_value = field.field_type[1:-1]
            if field_type_value == "ubyte":
                deserialize_code += f"        {field.name}=bytes(fb_obj.{make_camel_case(field.name)}AsNumpy()) if fb_obj.{make_camel_case(field.name)}Length() > 0 else None,\n"
            elif field_type_value == "float":
                deserialize_code += f"        {field.name}=[fb_obj.{make_camel_case(field.name)}(i) for i in range(fb_obj.{make_camel_case(field.name)}Length())] if fb_obj.{make_camel_case(field.name)}Length() > 0 else None,\n"
            elif field_type_value in table_names:
                deserialize_code += f"        {field.name}=[deserialize_{field_type_value.lower()}(fb_obj.{make_camel_case(field.name)}(i)) for i in range(fb_obj.{make_camel_case(field.name)}Length())] if fb_obj.{make_camel_case(field.name)}Length() > 0 else None,\n"
            elif field_type_value == "uint32":
                deserialize_code += f"        {field.name}=[fb_obj.{make_camel_case(field.name)}(i) for i in range(fb_obj.{make_camel_case(field.name)}Length())] if fb_obj.{make_camel_case(field.name)}Length() > 0 else None,\n"
            else:
                raise NotImplementedError(f"Unsupported field type: {field.field_type}")
        else:
            # Handle nested tables or enums
            if field.field_type in [en.name for en in schema.enums]:
                deserialize_code += (
                    f"        {field.name}={field.field_type}DC(fb_obj.{make_camel_case(field.name)}()),\n"
                )
            else:
                deserialize_code += f"        {field.name}=deserialize_{field.field_type.lower()}(fb_obj.{make_camel_case(field.name)}()),\n"

    deserialize_code = deserialize_code.rstrip(",\n") + "\n"
    deserialize_code += "    )\n"

    return deserialize_code


def generate_deserialize_root_function(schema: FlatBufferSchema) -> str:
    if schema.root_type is None:
        logger.info(f"No root_type declared in the .fbs schema file {schema.file_path}")
        return ""

    # Find the root table
    root_table = next(table for table in schema.tables if table.name == schema.root_type)
    deserialize_code = f"def deserialize_root_{root_table.name.lower()}(bytes_obj: bytes) -> {root_table.name}DC:\n"
    deserialize_code += f"    fb_obj = {root_table.name}.{root_table.name}.GetRootAs{root_table.name}(bytes_obj, 0)\n"
    deserialize_code += f"    return deserialize_{root_table.name.lower()}(fb_obj)\n"

    return deserialize_code


def add_imports(schema: FlatBufferSchema, wsock_model_root, dc_model_root) -> str:
    imports = ""
    root_schemas = [f"{table.name}" for table in schema.tables if table.name == schema.root_type]
    if len(root_schemas) > 0:
        imports = f"from {wsock_model_root} import "
        imports += ", ".join(root_schemas)
        imports += "\n\n"
    if len(schema.tables) > 0:
        imports += f"from {dc_model_root} import "
        imports += ", ".join([f"{table.name}DC" for table in schema.tables])
    if len(schema.enums) > 0:
        imports += "," + ", ".join([f"{en.name}DC" for en in schema.enums])

    namespace_map = defaultdict(list)
    for tbl in schema.tables:
        for field in tbl.fields:
            if field.namespace is not None:
                namespace_map[field.namespace].append(field.field_type)

    for namespace, values in namespace_map.items():
        import_func_str = ", ".join([f"deserialize_{field_type.lower()}" for field_type in values])
        if len(values) > 0:
            imports += f"\nfrom {schema.py_root}.fb_{namespace}_deserializer import {import_func_str}\n"

    imports += "\n\n"
    return imports


def generate_deserialization_code(
    fbs_schema: str | FlatBufferSchema, output_file: str | pathlib.Path, wsock_model_root, dc_model_root
):
    if isinstance(fbs_schema, str | pathlib.Path):
        fbs_schema = load_fbs_file(fbs_schema)

    imports_str = add_imports(fbs_schema, wsock_model_root, dc_model_root)

    with open(output_file, "w") as out_file:
        # out_file.write("import flatbuffers\nfrom typing import List\n\n")
        out_file.write(imports_str)

        # Write deserialization functions for each table
        for table in fbs_schema.tables:
            out_file.write(generate_deserialize_function(fbs_schema, table))
            out_file.write("\n\n")

        # Write the deserialize function for the root_type
        out_file.write(generate_deserialize_root_function(fbs_schema))

    print(f"Deserialization code generated and saved to {output_file}")


if __name__ == "__main__":
    tmp_dir = pathlib.Path("temp")
    tmp_dir.mkdir(exist_ok=True)

    generate_deserialization_code(
        "schemas/commands.fbs", tmp_dir / "fb_deserializer.py", "ada.comms.wsock", "fb_model_gen"
    )

import pathlib
import re

from fbs_serializer import FlatBufferSchema


def sub_wrong_py_imports(namespace: str, py_txt: str) -> str:
    return re.sub(f"from {namespace}", rf"from ada.comms.fb.{namespace}", py_txt)


def update_py_imports(namespace: str, file_dir: pathlib.Path):
    for py_file in file_dir.rglob("*.py"):
        txt = py_file.read_text()
        new_txt = sub_wrong_py_imports(namespace, txt)
        if txt != new_txt:
            py_file.write_text(new_txt)


def sub_wrong_ts_imports(ts_txt: str) -> str:
    """Substitute importing Package/subdir/*.js with Package/subdir/*"""
    # Regex to find imports that end with `.js` and replace them with the correct format
    return re.sub(r"import\s+{\s*(\w+)\s*}\s+from\s+['\"].*/([^/]+)\.js['\"];", r"import { \1 } from './\2';", ts_txt)


def update_ts_imports(file_dir: pathlib.Path):
    for ts_file in file_dir.rglob("*.ts"):
        txt = ts_file.read_text()
        new_txt = sub_wrong_ts_imports(txt)
        if txt != new_txt:
            ts_file.write_text(new_txt)


if __name__ == "__main__":
    from update_flatbuffers import _GEN_DIR

    # update_py_imports(_WSOCK_DIR)
    update_ts_imports(_GEN_DIR)


def substitute_class_in_instantiation(input_string, new_class_name):
    # Use regex to match the pattern 'obj = Scene()' and substitute only the class name
    pattern = r"(\w+)\s*=\s*(\w+)\(\)"  # Matches 'obj = Scene()' or similar
    return re.sub(pattern, r"obj = " + new_class_name + "()", input_string)


def add_class_import(input_string, new_class_name, module_name):
    # Regex to match instantiation and capture indentation
    pattern = rf"^(\s*)(\w+)\s*=\s*({new_class_name})\(\)"  # Capture indentation and object instantiation

    import_statement = f"from {module_name}.{new_class_name} import {new_class_name}\n"

    def add_import(match):
        # Capture the indentation
        indent = match.group(1)
        # Insert the import statement before the instantiation
        return f"{indent}{import_statement}{match.group(0)}"

    # Substitute the matching instantiations with import statements added
    result = re.sub(pattern, add_import, input_string, flags=re.MULTILINE)

    return result


def update_gen_py_imports(comms_dir: pathlib.Path, fbs_schema: FlatBufferSchema):
    namespaces = fbs_schema.get_all_namespaces()
    for fp in comms_dir.rglob("*.py"):
        txt = fp.read_text()
        for ns in namespaces:
            new_txt = re.sub(f"from {ns}\.", f"from ada.comms.fb.{ns}", txt, flags=re.MULTILINE)
            if txt != new_txt:
                fp.write_text(new_txt)

import pathlib
import re


def sub_wrong_py_imports(py_txt: str) -> str:
    return re.sub("from wsock", r"from ada.comms.wsock", py_txt)


def update_py_imports(file_dir: pathlib.Path):
    for py_file in file_dir.rglob("*.py"):
        txt = py_file.read_text()
        new_txt = sub_wrong_py_imports(txt)
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

import importlib
import json
import logging
import pathlib
import re
from dataclasses import dataclass, field

import pytest

from ada.core.file_system import get_list_of_files

_re_from_module_import = re.compile("from (?P<module_path>.*?) import (?P<ref>.*?)$")
_re_module_import = re.compile("^import (?P<ref>.*?)$")


@dataclass
class Notebook:
    name: str
    imports: list[str] = field(repr=False)
    code: str = field(repr=False)
    path: pathlib.Path

    def execute(self):
        for import_string in self.imports:
            res_from = _re_from_module_import.search(import_string)
            res_import = _re_module_import.search(import_string)
            mpath = None

            if res_from is not None:
                d = res_from.groupdict()
                mpath = d["module_path"]

            if res_import is not None:
                d = res_import.groupdict()
                mpath = d["ref"]

            if mpath is None:
                continue

            split_res = mpath.split(" as ")
            if len(split_res) == 2:
                # alias = split_res[-1]
                module_name = split_res[0]
                package = importlib.import_module(module_name)
                globals().update(package.__dict__)
            else:
                package = importlib.import_module(mpath)
                globals().update(package.__dict__)
        try:
            exec(self.code)
        except ModuleNotFoundError as e:
            logging.error(e)
        except FileNotFoundError as e:
            logging.error(e)
        except BaseException as e:
            logging.exception(self.code)
            raise e


def get_notebooks() -> list[Notebook]:
    notebooks = []
    for notebook_path in get_list_of_files(pathlib.Path(__file__).parent / "../../examples", ".ipynb"):
        if ".ipynb_checkpoints" in notebook_path:
            continue

        notebook_path = pathlib.Path(notebook_path).resolve().absolute()
        with open(notebook_path, "r") as f:
            notebook_data = json.load(f)

        executable_code = ""
        import_lines = []
        for cell in notebook_data["cells"]:
            if cell["cell_type"] != "code":
                continue
            is_multiline_import_statement = False
            for source in cell["source"]:
                if "import " in source or is_multiline_import_statement is True:
                    if "(" in source:
                        is_multiline_import_statement = True
                    import_lines.append(source)
                    if ")" in source:
                        is_multiline_import_statement = False
                else:
                    executable_code += source

            executable_code += "\n"

        notebooks.append(Notebook(notebook_path.name, import_lines, executable_code, notebook_path))
    return notebooks


all_notebooks = get_notebooks()
all_names = [x.name for x in all_notebooks]


@pytest.fixture
def dummy():
    return None


# @pytest.mark.parametrize("notebook", all_notebooks, ids=all_names)
# def test_examples(notebook: Notebook):
#     notebook.execute()

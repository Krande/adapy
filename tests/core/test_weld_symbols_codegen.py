"""The viewer's copy of the weld symbols is current with ``ada.api.weld_symbols``.

The frontend draws the symbols from generated geometry (``src/frontend/src/utils/weldSymbols.generated.ts``)
and its test compares what it draws with a generated fixture of Python's SVGs. Both are written by
``scripts/codegen/gen_weld_symbols.py``; after changing a symbol, run it again.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _generator():
    script = ROOT / "scripts" / "codegen" / "gen_weld_symbols.py"
    spec = importlib.util.spec_from_file_location("gen_weld_symbols", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_generated_frontend_weld_symbols_are_current():
    gen = _generator()
    for path, text in gen.outputs(ROOT).items():
        assert gen.is_current(path, text), f"{path} is missing or stale: run scripts/codegen/gen_weld_symbols.py"


def test_the_generated_module_has_every_symbol():
    from ada.api.fasteners import WeldType

    module = _generator().build_module()
    for wt in WeldType:
        assert (f"  {wt.value}: {{" in module) is (wt is not WeldType.NONE), wt

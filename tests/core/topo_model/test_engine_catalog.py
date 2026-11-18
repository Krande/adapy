"""Per-engine capability registry (``supports_grouping``) + the adapy-default
engine's tolerate-and-ignore of a grouped doc.

The capability flag is advertised per engine and rides in the worker heartbeat
(``procedural_engine_specs``); the built-ins report ``supports_grouping=False``,
a capability engine registers ``True``. adapy-default ignores any
``groups``/``STRUCTURE_NAME`` a doc carries — it compiles a single blueprint —
but must not choke on them."""

from __future__ import annotations

import pytest

from ada.topo_model import (
    procedural_engine_specs,
    register_procedural_engine_capabilities,
)
from ada.topo_model.compile import compile_procedural_doc
from ada.topo_model.engines import BUILTIN_ENGINES

_DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
}


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


# ── capability registry / advertising ────────────────────────────────


def test_builtin_engines_do_not_support_grouping():
    for slug, spec in BUILTIN_ENGINES.items():
        assert spec.get("supports_grouping") is False, slug


def test_specs_advertise_builtins_as_non_grouping():
    specs = {s["slug"]: s for s in procedural_engine_specs()}
    # Every built-in engine is advertised, all non-grouping.
    assert set(BUILTIN_ENGINES) <= set(specs)
    for slug in BUILTIN_ENGINES:
        assert specs[slug]["supports_grouping"] is False


def test_register_capabilities_is_idempotent_and_flags_grouping():
    register_procedural_engine_capabilities("cap-engine", supports_grouping=True)
    register_procedural_engine_capabilities("cap-engine", supports_grouping=True)  # replace, not dup
    matches = [s for s in procedural_engine_specs() if s["slug"] == "cap-engine"]
    assert len(matches) == 1
    assert matches[0] == {"slug": "cap-engine", "supports_grouping": True}
    # A built-in is unaffected and stays non-grouping.
    assert {"slug", "supports_grouping"} <= set(matches[0])
    default = next(s for s in procedural_engine_specs() if s["slug"] == "adapy-default")
    assert default["supports_grouping"] is False


# ── adapy-default tolerates but ignores a grouped doc ────────────────


def test_adapy_default_ignores_groups_and_structure_name():
    grouped = {
        "spaces": [
            {**_DOC["spaces"][0], "STRUCTURE_NAME": "Group A"},
            {**_DOC["spaces"][1], "STRUCTURE_NAME": "Group B"},
        ],
        "groups": [
            {"name": "Group A", "blueprint": "steel_stru"},
            {"name": "Group B", "blueprint": "none"},
        ],
    }
    glb = compile_procedural_doc(grouped)
    assert _is_glb(glb)
    # Grouping is ignored: identical geometry to the same doc without groups.
    assert len(glb) == len(compile_procedural_doc(_DOC))


# ── Excel format, declared by the registration ───────────────────────


def test_builtins_declare_their_excel_format_through_their_specs():
    specs = {s["slug"]: s for s in procedural_engine_specs()}
    assert specs["adapy-default"]["supports_excel"] is True
    assert specs["echo"]["supports_excel"] is False


def test_a_registration_carries_its_excel_handlers(monkeypatch):
    from ada.topo_model import engine_catalog

    monkeypatch.setattr(engine_catalog, "_ENGINE_CAPABILITY_REGISTRY", dict(engine_catalog._ENGINE_CAPABILITY_REGISTRY))
    register_procedural_engine_capabilities(
        "xl-engine",
        name="XL",
        entrypoint="xl.mod:compile",
        export_entrypoint="xl.mod:write_book",
        import_entrypoint="xl.mod:read_book",
        supports_excel=True,
    )
    spec = next(s for s in procedural_engine_specs() if s["slug"] == "xl-engine")
    assert spec["export_entrypoint"] == "xl.mod:write_book"
    assert spec["import_entrypoint"] == "xl.mod:read_book"
    assert spec["supports_excel"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"export_entrypoint": "no_colon"},
        {"import_entrypoint": "no_colon"},
        {"supports_excel": False, "export_entrypoint": "xl.mod:write_book"},
    ],
)
def test_a_registration_with_unusable_excel_arguments_is_refused(kwargs):
    with pytest.raises(ValueError):
        register_procedural_engine_capabilities("bad-xl", name="Bad", entrypoint="xl.mod:compile", **kwargs)


def test_supports_excel_false_in_a_manifest_means_no_excel_format():
    from ada.topo_model.engines import (
        EngineHasNoExcelFormat,
        engine_supports_excel,
        export_doc_to_xlsx,
        import_xlsx_to_doc,
    )

    manifest = {"kind": "server", "entrypoint": "xl.mod:compile", "supports_excel": False}
    assert engine_supports_excel("xl-engine", manifest) is False
    with pytest.raises(EngineHasNoExcelFormat):
        export_doc_to_xlsx("xl-engine", _DOC, name="m", manifest_doc=manifest)
    with pytest.raises(EngineHasNoExcelFormat):
        import_xlsx_to_doc("xl-engine", b"", manifest_doc=manifest)
    # Saying nothing keeps the Excel format an entrypoint has always implied.
    assert engine_supports_excel("xl-engine", {"kind": "server", "entrypoint": "xl.mod:compile"}) is True
    assert engine_supports_excel("echo") is False and engine_supports_excel(None) is True

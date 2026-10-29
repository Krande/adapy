"""A Sesam results interface file (.SIN) read as a model, and through ``ada convert``."""

from ada.fem.formats.sesam.results.read_sin import read_sin_file

_SIN = "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"


def _n_elems(res) -> int:
    return sum(len(b.identifiers) for b in res.mesh.elements)


def test_from_fem_reads_a_sin_as_a_model(fem_files):
    import ada

    sin = fem_files / _SIN
    a = ada.from_fem(sin)
    fem = next(p.fem for p in a.get_all_parts_in_assembly(include_self=True) if len(p.fem.nodes) > 0)
    expected = read_sin_file(sin)

    assert len(fem.nodes) == len(expected.mesh.nodes.identifiers)
    assert len(fem.elements) == _n_elems(expected)


def test_convert_sin_to_fem(fem_files, tmp_path):
    import argparse

    from ada.api.cli import _cmd_convert

    out = tmp_path / "out.FEM"
    args = argparse.Namespace(
        input=str(fem_files / _SIN),
        output=str(out),
        from_format=None,
        to_format=None,
        split=False,
        limit=None,
        strict=False,
        superelement=None,
    )
    assert _cmd_convert(args) == 0
    assert out.is_file()
    text = out.read_text()
    assert "GELMNT1" in text and "RVNODDIS" not in text

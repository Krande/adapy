"""Per-representation length-unit resolution in the streaming STEP reader.

Mixed-unit files (some CAD systems author sub-assemblies in metres inside an
mm file) must be scaled per representation, else parts in a non-global unit are
mis-sized — e.g. metre-context fasteners in an mm file shrink 1000x and
tessellate to near-zero-area slivers.
"""

from ada.cadit.step.read import stream_reader as sr
from ada.cadit.step.read.stream_reader import (
    _COMPLEX,
    _Enum,
    _Rec,
    _Ref,
    _representation_length_scale,
    _si_unit_length_scale,
    _unit_length_scale,
    detect_step_length_unit_scale,
)


def test_si_unit_length_scale_from_enums():
    assert _si_unit_length_scale([_Enum("MILLI"), _Enum("METRE")]) == 0.001
    assert _si_unit_length_scale([_Enum("CENTI"), _Enum("METRE")]) == 0.01
    assert _si_unit_length_scale([None, _Enum("METRE")]) == 1.0  # $ prefix -> base metre
    assert _si_unit_length_scale([_Enum("MILLI"), _Enum("RADIAN")]) is None  # not a length unit


def test_unit_length_scale_complex_and_plain():
    pool = {
        1: _Rec(_COMPLEX, {"LENGTH_UNIT": [], "SI_UNIT": [_Enum("MILLI"), _Enum("METRE")]}),
        2: _Rec("SI_UNIT", [None, _Enum("METRE")]),
        3: _Rec(_COMPLEX, {"LENGTH_UNIT": [], "CONVERSION_BASED_UNIT": ["INCH"]}),
    }
    assert _unit_length_scale(pool.get, 1) == 0.001
    assert _unit_length_scale(pool.get, 2) == 1.0
    assert _unit_length_scale(pool.get, 3) == 0.0254


def test_representation_length_scale_uses_own_context():
    # rep -> GEOMETRIC_REPRESENTATION_CONTEXT(+GLOBAL_UNIT_ASSIGNED_CONTEXT) -> metre unit
    pool = {
        10: _Rec(_COMPLEX, {"LENGTH_UNIT": [], "SI_UNIT": [None, _Enum("METRE")]}),
        20: _Rec(
            _COMPLEX,
            {
                "GEOMETRIC_REPRESENTATION_CONTEXT": [3],
                "GLOBAL_UNIT_ASSIGNED_CONTEXT": [[_Ref(10)]],
            },
        ),
        # ADVANCED_BREP_SHAPE_REPRESENTATION('', (items), context)
        30: _Rec("ADVANCED_BREP_SHAPE_REPRESENTATION", ["", [_Ref(99)], _Ref(20)]),
    }
    assert _representation_length_scale(pool.get, 30) == 1.0


def test_representation_length_scale_none_when_no_unit():
    pool = {30: _Rec("ADVANCED_BREP_SHAPE_REPRESENTATION", ["", [_Ref(99)]])}
    assert _representation_length_scale(pool.get, 30) is None


def _step_with_unit_at_end(tmp_path, unit_stmt: str, pad_bytes: int) -> str:
    # A STEP file whose LENGTH_UNIT record sits at the very END of the DATA section,
    # past a large body — the real-world layout (e.g. ~99.7% into a 778 MB reference file)
    # that made the old whole-file mmap.find fault the entire file into RSS.
    head = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
    body = "".join(f"#{i}=CARTESIAN_POINT('p',(0.,0.,0.));\n" for i in range(1, pad_bytes // 40 + 2))
    tail = unit_stmt + "\nENDSEC;\nEND-ISO-10303-21;\n"
    p = tmp_path / "unit_at_end.step"
    p.write_text(head + body + tail)
    return str(p)


def test_detect_unit_scale_pread_finds_unit_at_end(tmp_path):
    # mm (0.001), inch conversion (0.0254), metre ($ prefix -> 1.0), and absent -> 1.0.
    # pad past several pread chunks so the chunk-boundary stitching is exercised.
    pad = 5 << 20  # ~5 MiB body, several 1 MiB pread chunks before the unit
    mm = _step_with_unit_at_end(tmp_path, "#9=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );", pad)
    assert detect_step_length_unit_scale(mm) == 0.001

    inch = _step_with_unit_at_end(
        tmp_path,
        "#9=( CONVERSION_BASED_UNIT('INCH',#10) LENGTH_UNIT() NAMED_UNIT(#11) );",
        pad,
    )
    assert detect_step_length_unit_scale(inch) == 0.0254

    metre = _step_with_unit_at_end(tmp_path, "#9=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.) );", pad)
    assert detect_step_length_unit_scale(metre) == 1.0


def test_detect_unit_scale_absent_returns_one(tmp_path):
    p = tmp_path / "no_unit.step"
    p.write_text("ISO-10303-21;\nDATA;\n#1=CARTESIAN_POINT('p',(0.,0.,0.));\nENDSEC;\n")
    assert detect_step_length_unit_scale(str(p)) == 1.0


def test_detect_unit_scale_needle_across_chunk_boundary(tmp_path, monkeypatch):
    # Force a tiny scan chunk so "LENGTH_UNIT" straddles a chunk boundary, exercising the
    # tail-stitch path; and a tiny statement-read window for good measure.
    monkeypatch.setattr(sr, "_UNIT_SCAN_CHUNK", 13, raising=True)
    monkeypatch.setattr(sr, "_PREAD_CHUNK", 8, raising=True)
    p = _step_with_unit_at_end(tmp_path, "#9=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.CENTI.,.METRE.) );", 1 << 10)
    assert detect_step_length_unit_scale(p) == 0.01

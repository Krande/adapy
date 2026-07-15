"""Every ADA_STREAM_TESS_* read goes through ada.cad.registry — one nominal config, one density.

These knobs used to be re-implemented inline at ~10 call sites (the native STEP->GLB / ->OBJ / ->STL
/ ->IFC / ->STEP paths, the object stream path, the AP242 writer). The literals happened to agree,
but the *boolean spellings* did not: scene_from_step_stream omitted "" from its falsy set, so an
explicitly-empty ADA_STREAM_TESS_ADAPTIVE read as ON there and OFF everywhere else. These tests pin
the parse so the same env can't mean two densities on two call paths again.
"""

from __future__ import annotations

import pathlib

import pytest

from ada.cad.registry import (
    DEFAULT_STREAM_TESS_ADAPTIVE,
    DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE,
    DEFAULT_STREAM_TESS_ANGULAR_DEG,
    DEFAULT_STREAM_TESS_DEFLECTION,
    stream_tess_adaptive,
    stream_tess_defaults,
    stream_tess_face_regions,
    stream_tess_model_scale,
    stream_tess_model_scale_env,
    stream_tess_strict,
)

# Spellings that mean "off" for every ADA_STREAM_TESS_* boolean. "" is falsy: an explicitly-empty
# env var means off, not on.
FALSY = ("0", "false", "FALSE", "no", "off", "", "  ")
TRUTHY = ("1", "true", "yes", "on")


def test_defaults_unset(monkeypatch):
    monkeypatch.delenv("ADA_STREAM_TESS_DEFLECTION", raising=False)
    monkeypatch.delenv("ADA_STREAM_TESS_ANGULAR", raising=False)
    assert stream_tess_defaults() == (DEFAULT_STREAM_TESS_DEFLECTION, DEFAULT_STREAM_TESS_ANGULAR_DEG)


def test_defaults_env_override(monkeypatch):
    monkeypatch.setenv("ADA_STREAM_TESS_DEFLECTION", "0.5")
    monkeypatch.setenv("ADA_STREAM_TESS_ANGULAR", "3.5")
    assert stream_tess_defaults() == (0.5, 3.5)


@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize("value", FALSY)
def test_adaptive_falsy_spellings_are_off_regardless_of_default(monkeypatch, value, default):
    """The regression: "" must be OFF on every path. scene_from_step_stream used to read it as ON."""
    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", value)
    assert stream_tess_adaptive(default=default) is False


@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize("value", TRUTHY)
def test_adaptive_truthy_spellings_are_on_regardless_of_default(monkeypatch, value, default):
    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", value)
    assert stream_tess_adaptive(default=default) is True


def test_adaptive_unset_honours_the_callers_default(monkeypatch):
    """The default differs by call path on purpose: OFF for the object stream path (a library API
    whose density must not shift), ON for the whole-file native converters (transfer-size/timeout
    sensitive). An explicit env setting overrides both — see the parametrized tests above."""
    monkeypatch.delenv("ADA_STREAM_TESS_ADAPTIVE", raising=False)
    assert stream_tess_adaptive() is DEFAULT_STREAM_TESS_ADAPTIVE is False
    assert stream_tess_adaptive(default=DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE) is True


def test_model_scale_requires_adaptive(monkeypatch):
    monkeypatch.setenv("ADA_STREAM_TESS_MODEL_SCALE", "32.0")
    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", "0")
    assert stream_tess_model_scale() == 0.0  # adaptive off => no reference scale
    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", "1")
    assert stream_tess_model_scale() == 32.0


def test_model_scale_tolerates_garbage(monkeypatch):
    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", "1")
    monkeypatch.setenv("ADA_STREAM_TESS_MODEL_SCALE", "not-a-float")
    assert stream_tess_model_scale() == 0.0
    assert stream_tess_model_scale_env() == 0.0


def test_model_scale_env_is_ungated(monkeypatch):
    """A pool worker reads the scale the parent exported and must NOT re-gate on the adaptive env.

    stream_tess_model_scale() defaults adaptive OFF, so if the ungated reader didn't exist a worker
    that never inherited ADA_STREAM_TESS_ADAPTIVE would silently tessellate at a fixed angle while
    the parent believed adaptive was on — a whole-model density change with no error.
    """
    monkeypatch.delenv("ADA_STREAM_TESS_ADAPTIVE", raising=False)
    monkeypatch.setenv("ADA_STREAM_TESS_MODEL_SCALE", "32.0")
    assert stream_tess_model_scale_env() == 32.0  # presence of the scale IS the signal
    assert stream_tess_model_scale() == 0.0  # the gated reader honours its own OFF default


@pytest.mark.parametrize(
    "fn, key",
    [(stream_tess_face_regions, "ADA_STREAM_TESS_FACE_REGIONS"), (stream_tess_strict, "ADA_STREAM_TESS_STRICT")],
)
def test_opt_in_flags_default_off(monkeypatch, fn, key):
    monkeypatch.delenv(key, raising=False)
    assert fn() is False
    for v in FALSY:
        monkeypatch.setenv(key, v)
        assert fn() is False, f"{key}={v!r} must be off"
    for v in TRUTHY:
        monkeypatch.setenv(key, v)
        assert fn() is True, f"{key}={v!r} must be on"


def test_no_call_site_reimplements_the_env_parse():
    """Source-level guard: only registry.py may parse an ADA_STREAM_TESS_* value.

    Other modules may still *route* the pipeline selector (os.environ.get("ADA_STREAM_TESS_PIPELINE"))
    or set/pop vars for a subprocess — what they must not do is re-derive a default or a falsy set,
    which is how the density silently diverged by call path.
    """
    src = pathlib.Path(__file__).resolve().parents[3] / "src" / "ada"
    if not src.is_dir():
        # Only a repo checkout has src/; when the suite runs against an installed package (the
        # conda-forge feedstock copies tests/ alone) there is no source tree to lint.
        pytest.skip(f"source-tree lint; no src/ada at {src}")
    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "registry.py" and py.parent.name == "cad":
            continue
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "ADA_STREAM_TESS_" not in line or line.lstrip().startswith("#"):
                continue
            # A default= argument to environ.get on a TESS var re-derives a default.
            if "environ.get(" in line and line.count('"') >= 4 and "PIPELINE" not in line:
                offenders.append(f"{py.relative_to(src.parent.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "re-implemented ADA_STREAM_TESS_* defaults; use ada.cad.registry helpers:\n" + "\n".join(
        offenders
    )

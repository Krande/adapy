"""The IfcOpenShell OCC kernel (create_shape) aborts the process on macOS for some
representations (mapped items etc.) — an uncatchable native SIGABRT. _kernel_occ_shape skips
the kernel fallback on macOS (overridable with ADA_IFC_MACOS_KERNEL=1) so products with no
native reader degrade to no-geom instead of crashing. Tested cross-platform by faking
platform.system + the kernel.
"""

import platform

import pytest

from ada.cadit.ifc.read import read_shapes


class _FakeShape:
    geometry = "OCC_BODY"


class _Settings:
    USE_PYTHON_OPENCASCADE = 1
    USE_WORLD_COORDS = 2

    def set(self, *_a):
        pass


@pytest.fixture
def fake_kernel(monkeypatch):
    import ifcopenshell.geom

    calls = []

    def fake_create_shape(_settings, product):
        calls.append(product)
        return _FakeShape()

    monkeypatch.setattr(ifcopenshell.geom, "create_shape", fake_create_shape)
    monkeypatch.setattr(ifcopenshell.geom, "settings", lambda: _Settings())
    return calls


def test_kernel_skipped_on_macos(monkeypatch, fake_kernel):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.delenv("ADA_IFC_MACOS_KERNEL", raising=False)
    assert read_shapes._kernel_occ_shape(object()) is None
    assert fake_kernel == []  # create_shape never invoked -> no native abort


def test_kernel_forced_on_macos_with_override(monkeypatch, fake_kernel):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setenv("ADA_IFC_MACOS_KERNEL", "1")
    assert read_shapes._kernel_occ_shape(object()) == "OCC_BODY"
    assert len(fake_kernel) == 1


def test_kernel_runs_on_non_macos(monkeypatch, fake_kernel):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.delenv("ADA_IFC_MACOS_KERNEL", raising=False)
    assert read_shapes._kernel_occ_shape(object()) == "OCC_BODY"
    assert len(fake_kernel) == 1

"""The shipped IFC asset provider -- the only place in ``ada.assets`` allowed to know IFC.

Registers the ``asset-build-ifc`` builder from :mod:`ada.assets.builders`. This import itself must
stay CHEAP: :func:`ada.assets.builders.ensure_core_builders` imports this module from every
process that touches the asset store (Decision 1's dispatch-by-capability needs every builder
registered so it can answer "no builder for X" accurately, even on a pool that will never serve
``asset-build-ifc``). Neither ``ifcopenshell`` nor ``adacpp`` is imported at module scope here --
only inside :func:`_make_builder`, which runs once, lazily, the first time something actually asks
for this capability.

``walk`` / ``index`` / ``publish`` / ``build`` are separate submodules on purpose: a caller that
only needs the publish-time logic (a future ``asset-publish-ifc`` job handler, or a test) imports
``ada.assets.ifc.publish`` directly without pulling in the builder's own lazy import of the native
GLB path.
"""

from __future__ import annotations

import importlib.util

from ada.assets.builders import register_asset_builder

__all__ = ["IFC_BUILD_CAPABILITY"]

IFC_BUILD_CAPABILITY = "asset-build-ifc"


def _available() -> bool:
    """True only where this builder can actually run.

    ``ifcopenshell`` is a cheap ``find_spec`` (no import); the subset-streaming probe is the one
    already established for exactly this question (``ada.cad.registry.native_ifc_subset_available``
    -- it does `import adacpp` internally, but only once this far, and only on a pool that has it).
    Advertising the capability without either would let a pool take a job it can only time out on
    (``ada.assets.builders`` module docstring).
    """
    if importlib.util.find_spec("ifcopenshell") is None:
        return False
    try:
        from ada.cad.registry import native_ifc_subset_available

        return native_ifc_subset_available()
    except Exception:
        return False


def _make_builder():
    from ada.assets.ifc.build import IfcAssetBuilder

    return IfcAssetBuilder()


register_asset_builder(
    IFC_BUILD_CAPABILITY,
    _make_builder,
    label="IFC (core)",
    available=_available,
)

"""The shipped IFC asset provider -- the only place in ``ada.assets`` allowed to know IFC.

Registers the ``asset-build-ifc`` builder (:mod:`ada.assets.builders`) and the ``ifc`` publisher
(:mod:`ada.assets.publishers`, Phase 4). This import itself must stay CHEAP:
:func:`ada.assets.builders.ensure_core_builders` AND :func:`ada.assets.publishers.
ensure_core_publishers` both import this module from every process that touches the asset store
(Decision 1's dispatch-by-capability needs every builder/publisher registered so it can answer "no
builder/publisher for X" accurately, even on a process that will never serve ``asset-build-ifc`` or
publish IFC). Neither ``ifcopenshell`` nor ``adacpp`` is imported at module scope here -- only
inside :func:`_make_builder` / :func:`_make_publisher`, which run once, lazily, the first time
something actually asks for the capability or the provider id.

``walk`` / ``index`` / ``publish`` / ``publisher`` / ``build`` are separate submodules on purpose:
a caller that only needs the publish-time logic (a job handler, or a test) imports
``ada.assets.ifc.publish`` directly without pulling in the builder's own lazy import of the native
GLB path.
"""

from __future__ import annotations

import importlib.util

from ada.assets.builders import register_asset_builder
from ada.assets.publishers import register_asset_publisher

__all__ = ["IFC_BUILD_CAPABILITY"]

IFC_BUILD_CAPABILITY = "asset-build-ifc"
IFC_PROVIDER_ID = "ifc"


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


def _make_publisher():
    from ada.assets.ifc.publisher import IfcAssetPublisher

    return IfcAssetPublisher()


register_asset_builder(
    IFC_BUILD_CAPABILITY,
    _make_builder,
    label="IFC (core)",
    available=_available,
)

register_asset_publisher(
    IFC_PROVIDER_ID,
    _make_publisher,
    label="IFC (core)",
)

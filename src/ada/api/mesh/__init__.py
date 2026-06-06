"""Array-backed FEM mesh substrate (``MeshArrays``) with lazy ``Node`` proxies.

The analysis-side FEM mesh historically stores millions of Python ``Node``/``Elem``
objects. This package provides a packed-numpy substrate (coords + id->index map +
per-type int32 connectivity) that the same ``Node``/``Elem`` API can sit on as thin
lazy proxies — ~4-6x less memory on large meshes. Gated by
``Config().meshing_array_backed`` while it is rolled out and parity-tested.
"""

from ada.api.mesh.store import ElemArrayBlock, MeshArrays

__all__ = ["MeshArrays", "ElemArrayBlock"]

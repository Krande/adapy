"""Bridge-only translation tables for the meshio adapter.

The canonical string-name dicts live in `ada.fem.shapes.mesh_types`
(`ada_to_str_type` / `str_to_ada_type`). This module re-exports
them under the historical `ada_to_meshio` / `meshio_to_ada` names
so the meshio bridge code keeps working unchanged.
"""

from ada.fem.shapes.mesh_types import ada_to_str_type, str_to_ada_type

ada_to_meshio = ada_to_str_type
meshio_to_ada = str_to_ada_type

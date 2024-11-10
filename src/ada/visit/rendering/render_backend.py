import gzip
import pathlib
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import trimesh


@dataclass
class MeshInfo:
    mesh_id: str = field(repr=False)
    parent_id: str = field(repr=False)
    full_name: str
    start: int
    end: int
    buffer_id: int
    glb_file_name: str


@dataclass
class SelectedMeshData:
    selected_mesh: trimesh.Trimesh
    modified_mesh: trimesh.Trimesh


def create_selected_meshes_from_mesh_info(mesh_info: MeshInfo, indices, positions, dim: int = 3) -> SelectedMeshData:
    """Returns the cut buffer from a mesh info object."""
    s, e = mesh_info.start // dim, (mesh_info.end + 1) // dim
    selected_indices = list(range(s, e))
    new_mesh_indices = [indices[x] for x in selected_indices]
    selected_mesh = trimesh.Trimesh(vertices=positions, faces=new_mesh_indices)

    modified_mesh_indices = np.delete(indices, selected_indices, axis=0)
    modified_mesh = trimesh.Trimesh(vertices=positions, faces=modified_mesh_indices)
    return SelectedMeshData(selected_mesh, modified_mesh)


class RenderBackend:
    """A backend that stores mesh reference data."""

    def _init_db(self):
        raise NotImplementedError()

    def commit(self):
        raise NotImplementedError()

    def add_trimesh_scene(self, scene: trimesh.Scene, tag: str, commit=True) -> trimesh.Scene:
        """Adds a trimesh scene to the backend."""
        raise NotImplementedError()

    def add_metadata(self, metadata: dict, tag: str) -> None:
        """Adds metadata to the database."""
        raise NotImplementedError()

    def on_pick(self, *args, **kwargs):
        """Called when a mesh is picked and performs a certain action."""
        raise NotImplementedError()

    def get_mesh_data_from_face_index(self, face_index, buffer_id, glb_file_name) -> MeshInfo:
        """Returns the mesh id from a face index."""
        raise NotImplementedError()

    def glb_to_trimesh_scene(self, glb_file) -> trimesh.Scene:
        if is_gzip_file(glb_file):
            with gzip.open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")
        else:
            with open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")
        return scene


def is_gzip_file(file_path):
    with open(file_path, "rb") as file:
        header = file.read(2)
        return header == b"\x1F\x8B"


class SqLiteBackend(RenderBackend):
    """A backend that uses a SQLite database to store mesh reference data."""

    def __init__(self, db_path: str | pathlib.Path = ":memory:", overwrite=True):
        if db_path == ":memory:":  # In memory database
            pass
        else:
            if isinstance(db_path, str):
                db_path = pathlib.Path(db_path)
            if db_path.exists() and overwrite:
                db_path.unlink()
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.path = db_path
        self.conn = sqlite3.connect(db_path)
        self.c = self.conn.cursor()
        self._init_db()

    def _init_db(self):
        self.c.execute(
            """CREATE TABLE if not exists mesh 
            (mesh_id text, parent_id test, full_name text, start int, end int, buffer_id int, glb_file_name text)"""
        )
        self.commit()

    def commit(self):
        self.conn.commit()

    def add_metadata(self, metadata: dict, tag: str, buffer_prefix="draw_ranges_node", tree_name="id_hierarchy") -> str:
        """Adds metadata to the database."""
        # For really large models this might provide a speedbump
        self.c.execute("""BEGIN TRANSACTION;""")
        id_sequence_data = {}
        for key, value in metadata.items():
            if not key.startswith(buffer_prefix):
                continue
            buffer_id = int(float(key.replace(buffer_prefix, "")))
            for mesh_id, values in value.items():
                id_sequence_data[mesh_id] = list(values) + [buffer_id]

        for mesh_id, values in metadata.get(tree_name).items():
            full_name, parent_id = values
            start, length, buffer_id = id_sequence_data.get(mesh_id, [None, None, None])  # Get start, end values
            if start is None:
                continue
            end = start + length
            row = (mesh_id, parent_id, full_name, start, end, buffer_id, tag)
            self.c.execute("INSERT INTO mesh VALUES (?,?,?,?,?,?,?)", row)

        self.commit()
        return tag

    def get_mesh_data_from_face_index(self, face_index, buffer_id, tag) -> MeshInfo | None:
        """Returns the mesh id from a face index."""
        self.c.execute(
            "SELECT * FROM mesh WHERE buffer_id=? AND glb_file_name=? AND start<=? AND end >=?",
            (buffer_id, tag, face_index, face_index),
        )
        result = self.c.fetchone()
        if result is None:
            return None

        return MeshInfo(*result)

    def close(self):
        self.commit()
        self.conn.close()

    def __del__(self):
        if self.conn:
            self.conn.close()

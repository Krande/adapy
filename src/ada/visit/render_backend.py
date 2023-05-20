import gzip
import pathlib
import sqlite3
from typing import Iterable

import trimesh


class RenderBackend:
    """A backend that stores mesh reference data."""

    def _init_db(self):
        raise NotImplementedError()

    def commit(self):
        raise NotImplementedError()

    def add_glb(self, file_path: str | pathlib.Path, commit: bool):
        """Adds a glb file to the backend."""
        raise NotImplementedError()

    def add_from_glb_iterable(self, glb_files: Iterable[pathlib.Path | str]) -> Iterable[trimesh.Scene]:
        """Adds multiple glb files to the backend."""
        raise NotImplementedError()

    def on_pick(self, *args, **kwargs):
        """Called when a mesh is picked and performs a certain action."""
        raise NotImplementedError()

    def get_mesh_data_from_face_index(self, face_index, buffer_id, glb_file_name):
        """Returns the mesh id from a face index."""
        raise NotImplementedError()


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

        self.path = db_path
        self.conn = sqlite3.connect(db_path)
        self.c = self.conn.cursor()
        self._init_db()

    def _init_db(self):
        self.c.execute(
            """CREATE TABLE mesh 
            (mesh_id text, parent_id test, full_name text, start int, end int, buffer_id int, glb_file_name text)"""
        )
        self.commit()

    def commit(self):
        self.conn.commit()

    def add_glb(self, glb_file, commit=True):
        if is_gzip_file(glb_file):
            with gzip.open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")
        else:
            with open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")

        self._insert_meta_id_sequence_from_glb(scene, glb_file)

        if commit:
            self.commit()

        return scene

    def add_from_glb_iterable(self, glb_files: Iterable[pathlib.Path | str]) -> Iterable[trimesh.Scene]:
        for glb_file in glb_files:
            yield self.add_glb(glb_file, commit=False)
        self.commit()

    def _insert_meta_id_sequence_from_glb(self, scene: trimesh.Scene, glb_file: pathlib.Path) -> None:
        id_sequence_data = {}
        for key, value in scene.metadata.items():
            if "id_sequence" not in key:
                continue
            buffer_id = int(float(key.replace("id_sequence", "")))
            for mesh_id, values in value.items():
                id_sequence_data[mesh_id] = values + [buffer_id]

        for mesh_id, values in scene.metadata.get("meta").items():
            full_name, parent_id = values
            start, end, buffer_id = id_sequence_data.get(mesh_id, [None, None, None])  # Get start, end values
            row = (mesh_id, parent_id, full_name, start, end, buffer_id, glb_file.stem)
            self.c.execute("INSERT INTO mesh VALUES (?,?,?,?,?,?,?)", row)

    def on_pick(self, event):
        """Called when a mesh is picked and returns the mesh id."""
        print(event)

    def get_mesh_data_from_face_index(self, face_index, buffer_id, glb_file_name):
        """Returns the mesh id from a face index."""
        self.c.execute('SELECT * FROM mesh WHERE buffer_id=? AND glb_file_name=? AND start<=? AND end >=?',
                       (buffer_id, glb_file_name, face_index, face_index))
        return self.c.fetchone()

    def close(self):
        self.commit()
        self.conn.close()

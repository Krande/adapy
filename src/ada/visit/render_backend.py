import gzip
import pathlib
import sqlite3
from typing import Iterable

import trimesh


class RenderBackend:
    def on_pick(self, *args, **kwargs):
        raise NotImplementedError()


def is_gzip_file(file_path):
    with open(file_path, "rb") as file:
        header = file.read(2)
        return header == b"\x1F\x8B"


class SqLiteBackend(RenderBackend):
    """A backend that uses a SQLite database to store mesh reference data."""

    def __init__(self, db_path: str | pathlib.Path, overwrite=True):
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
            (glb_file text, mesh_id text, parent_id test, full_name text, start int, end int, buffer_id int)"""
        )
        self.conn.commit()

    def _load_from_glb(self, glb_file):
        if is_gzip_file(glb_file):
            with gzip.open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")
        else:
            with open(glb_file, "rb") as f:
                scene = trimesh.load(f, file_type="glb")

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
            row = (glb_file.stem, mesh_id, parent_id, full_name, start, end, buffer_id)
            self.c.execute("INSERT INTO mesh VALUES (?,?,?,?,?,?,?)", row)

        return scene

    def load_from_glb_iterable(self, glb_files: Iterable[pathlib.Path | str]) -> Iterable[trimesh.Scene]:
        for glb_file in glb_files:
            yield self._load_from_glb(glb_file)
        self.conn.commit()

    def on_pick(self, event):
        """Called when a mesh is picked and returns the mesh id."""
        print(event)

    def close(self):
        self.conn.commit()
        self.conn.close()

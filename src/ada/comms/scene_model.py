from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from ada.cadit.ifc.sql_model import IfcSqlModel
from ada.comms.fb_model_gen import FileObjectDC, FilePurposeDC, FileTypeDC
from ada.config import Config


@dataclass
class Scene:
    file_objects: list[FileObjectDC] = field(default_factory=list)
    ifc_sql_store: IfcSqlModel = None
    mesh_meta: dict = None

    def get_file_object(self, name: str) -> FileObjectDC | None:
        for fo in self.file_objects:
            if fo.name == name:
                return fo
        return None

    def add_file_object(self, file_object: FileObjectDC):
        remove_existing_idx = None
        for i, fo in enumerate(self.file_objects):
            if fo.name == file_object.name:
                remove_existing_idx = i

        if remove_existing_idx is not None:
            self.file_objects.pop(remove_existing_idx)

        self.file_objects.append(file_object)

    def load_files_from_server_temp_dir(self):
        # check temp directory for any file objects
        if Config().websockets_server_temp_dir is None:
            return None

        temp_dir = Config().websockets_server_temp_dir
        if not temp_dir.exists():
            return

        for fp in temp_dir.iterdir():
            if not fp.is_file():
                continue
            if fp.suffix == ".ifc":
                glb_fp = fp.with_suffix(".glb")
                ifc_sqlite_fp = fp.with_suffix(".sqlite")
                ifc_sqlite_file = None
                if ifc_sqlite_fp.exists():
                    ifc_sqlite_file = FileObjectDC(
                        name=fp.stem,
                        filepath=ifc_sqlite_fp,
                        file_type=FileTypeDC.IFC,
                        purpose=FilePurposeDC.DESIGN,
                    )
                glb_file_object = None
                if glb_fp.exists():
                    glb_file_object = FileObjectDC(
                        name=fp.stem,
                        filepath=glb_fp,
                        file_type=FileTypeDC.GLB,
                        purpose=FilePurposeDC.DESIGN,
                    )
                file_object = FileObjectDC(
                    name=fp.stem,
                    filepath=fp,
                    file_type=FileTypeDC.IFC,
                    purpose=FilePurposeDC.DESIGN,
                    glb_file=glb_file_object,
                    ifcsqlite_file=ifc_sqlite_file,
                )
                self.add_file_object(file_object)

    def __post_init__(self):
        if Config().websockets_auto_load_temp_files is True:
            self.load_files_from_server_temp_dir()

    @staticmethod
    def get_temp_dir() -> pathlib.Path:
        if Config().websockets_server_temp_dir is not None:
            return Config().websockets_server_temp_dir

        return pathlib.Path("temp")

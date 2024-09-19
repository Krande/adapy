import pathlib
import shutil
from dataclasses import dataclass

import trimesh

import ada
from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
from ada.config import logger

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent

@dataclass
class PostProcessor:
    filepath: str | pathlib.Path

    def __post_init__(self):
        self.filepath = pathlib.Path(self.filepath)

    def __call__(self, scene: trimesh.Scene) -> trimesh.Scene:
        self.filepath.parent.mkdir(exist_ok=True, parents=True)
        scene.export("temp/curved_plates.glb")
        return scene

def run_this():
    a = ada.from_genie_xml(ROOT_DIR / "files/fem_files/sesam/curved_plates.xml")
    a.ifc_store.sync()

    tmp_ifc_fp = pathlib.Path("temp/curved_plates.ifc")
    tmp_glb_fp = tmp_ifc_fp.with_suffix(".glb")
    tmp_sql_fp = tmp_ifc_fp.with_suffix(".sqlite")

    postpro = PostProcessor(tmp_glb_fp)

    a.show(stream_from_ifc_store=True, scene_post_processor=postpro)
    a.to_ifc(tmp_ifc_fp)

    Ifc2SqlPatcher(tmp_ifc_fp, logger, dest_sql_file=tmp_sql_fp).patch()
    print(f"IFC file: {tmp_ifc_fp}")


if __name__ == '__main__':
    run_this()

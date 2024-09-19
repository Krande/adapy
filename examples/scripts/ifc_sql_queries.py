import pathlib

import trimesh

from ada.cadit.ifc.sql_model import sqlite as ifc_sqlite

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


def run_this(node_name: str):
    glb_file = THIS_DIR / 'temp' / 'curved_plates.glb'
    tri_scene = trimesh.load(glb_file)
    num = node_name.replace('node', '')

    meta = tri_scene.metadata.get(f'id_sequence{num}')
    guid = list(meta.keys())[0]
    sql_store = ifc_sqlite(glb_file.with_suffix('.sqlite'))
    entity = sql_store.by_guid(guid)
    print(entity)


if __name__ == '__main__':
    run_this('node1')

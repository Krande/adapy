import json
import os
import pathlib


def to_threejs_json(assembly, output_file_path):
    output = {
        "metadata": {"version": 4.3, "type": "Object", "generator": "ObjectExporter"},
        "textures": [],
        "images": [],
        "geometries": [
            {
                "uuid": "0A8F2988-626F-411C-BD6A-AC656C4E6878",
                "type": "BufferGeometry",
                "data": {
                    "attributes": {
                        "position": {
                            "itemSize": 3,
                            "type": "Float32Array",
                            "array": [1, 1, 0, 1, -1, 0, -1, -1, 0, -1, 1, 0],
                            "normalized": False,
                        },
                        "normal": {
                            "itemSize": 3,
                            "type": "Float32Array",
                            "array": [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1],
                            "normalized": False,
                        },
                        "uv": {
                            "itemSize": 2,
                            "type": "Float32Array",
                            "array": [1, 1, 1, 0, 0, 0, 0, 1],
                            "normalized": False,
                        },
                    },
                    # // type of index must be Uint8Array or Uint16Array.
                    # // # vertices thus cannot exceed 255 or 65535 respectively.
                    # // The current parser is able to read the index array
                    # // if it is nested in the attributes object, but such
                    # // syntax is no longer encouraged.
                    "index": {"type": "Uint16Array", "array": [0, 1, 2, 0, 2, 3]},
                    "boundingSphere": {"center": [0, 0, 0], "radius": 1},
                },
            }
        ],
        "materials": [],
        "object": {
            "uuid": "378FAA8D-0888-4249-8701-92D1C1F37C51",
            "type": "Scene",
            "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "children": [
                {
                    "uuid": "E7B44C44-DD75-4C29-B571-21AD6AEF0CA9",
                    "name": "SharedVertexTest",
                    "type": "Mesh",
                    "geometry": "0A8F2988-626F-411C-BD6A-AC656C4E6878",
                    "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                }
            ],
        },
    }

    output_file_path = pathlib.Path(output_file_path)
    os.makedirs(output_file_path.parent, exist_ok=True)
    with open(output_file_path, "w") as f:
        json.dump(output, f, indent=4)

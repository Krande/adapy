# ABAQUS/PYTHON POST PROCESSING SCRIPT
# Run using abaqus python / abaqus viewer -noGUI / abaqus cae -noGUI
# Tip! Import this into Abaqus PDE directly to further develop this script.
import logging
import os
import sys
import traceback

import cPickle as pickle
import numpy as np
from odbAccess import (
    ELEMENT_NODAL,
    INTEGRATION_POINT,
    NODAL,
    OdbInstanceType,
    OdbStepType,
    openOdb,
)


def filter1(obj, attr):
    if attr[:1] == "_":
        return False
    if hasattr(obj, attr) is False:
        return False
    r = getattr(obj, attr)
    if callable(r):
        return False
    return True


def get_data_from_attr(obj, attributes, serializer):
    return {attr: serializer(getattr(obj, attr)) for attr in filter(lambda attr: filter1(obj, attr), attributes)}


instance_names = []


def serialize(obj):
    """Recursively walk object's hierarchy."""
    if isinstance(obj, (int, float)):
        return obj
    elif isinstance(obj, np.float32):
        return float(obj)
    elif isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]
    elif isinstance(obj, (np.ndarray,)):
        return [serialize(v) for v in obj]
    elif isinstance(obj, str):
        return obj
    elif isinstance(obj, dict):
        return {key: serialize(val) for key, val in obj.items()}
    elif isinstance(obj, object):
        attributes = dir(obj)
        if "values" in attributes:
            return serialize(list(obj.values()))
        else:
            if isinstance(obj, OdbInstanceType):
                if obj.name not in instance_names:
                    instance_names.append(obj.name)
                    # Elements and nodes are enough for now
                    return instance_data(obj)
                else:
                    return obj.name
            if isinstance(obj, OdbStepType):
                data = get_data_from_attr(obj, attributes, serialize)
                # frames does not get exported automatically
                data["frames"] = [get_frame_data(frame) for frame in obj.frames]
                return data
            return get_data_from_attr(obj, attributes, serialize)
    else:
        raise ValueError('Unknown entity "{}", "{}"'.format(type(obj), obj))


def instance_data(obj):
    data = dict(name=obj.name)

    data["nodes"] = [(n.label, [float(x) for x in n.coordinates]) for n in obj.nodes]
    data["elements"] = [(e.label, e.connectivity) for e in obj.elements]
    return data


def get_field_data(field):
    # TODO: this does not export displacements. Should perhaps add element nodal as curr pos also.
    curr_pos = field.locations[0].position
    if curr_pos == INTEGRATION_POINT:
        nodal_data = field.getSubset(position=ELEMENT_NODAL)
        return [(int(n.nodeLabel), serialize(n.data)) for n in nodal_data.values]
    if curr_pos == NODAL:
        nodal_data = field.getSubset(position=NODAL)
        return [(int(n.nodeLabel), serialize(n.data)) for n in nodal_data.values]

    return None


def get_frame_data(frame):
    data = dict()
    for key, field in frame.fieldOutputs.items():
        field_data = dict()
        field_values = get_field_data(field)
        if field_values is None:
            continue
        field_data["values"] = field_values
        data[key] = field_data

    return data


analysis_path = sys.argv[1]
parent_dir = os.path.dirname(analysis_path)
logging.basicConfig(
    filename=os.path.join(parent_dir, "aba_io.log"),
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logging.info('Logging to "{}"'.format(parent_dir))
logging.info('Opening ODB "{}"'.format(analysis_path))

odb = openOdb(analysis_path, readOnly=True)

try:
    fname = os.path.basename(analysis_path)
    logging.info('serializing "{}" ODB data'.format(fname))
    res = serialize(
        dict(
            rootAssembly=odb.rootAssembly,
            userdata=odb.userData,
            steps=odb.steps,
            last_updated=os.path.getmtime(analysis_path),
        )
    )
    logging.info("serialization complete")

    logging.info("Starting export to pickle")
    with open(os.path.join(parent_dir, fname.replace(".odb", ".pckle")), "wb") as f:
        pickle.dump(res, f, 2)

except (BaseException, RuntimeError) as e:
    trace_str = traceback.format_exc()
    logging.error("{}, {}".format(e, trace_str))
finally:
    odb.close()

logging.info("Export to pickle complete")

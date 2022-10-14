# ABAQUS/PYTHON POST PROCESSING SCRIPT
# Run using abaqus python / abaqus viewer -noGUI / abaqus cae -noGUI
import logging
import os
import pickle
import sys

import numpy as np
from odbAccess import OdbInstanceType, openOdb


def filter1(obj, attr):
    if attr[:1] == "_":
        return False
    if hasattr(obj, attr) is False:
        return False
    r = getattr(obj, attr)
    if callable(r):
        return False
    return True


def odb_serializer(obj_):
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
                    return obj.name
                return {
                    attr: serialize(getattr(obj, attr)) for attr in filter(lambda attr: filter1(obj, attr), attributes)
                }
        else:
            raise ValueError('Unknown entity "{}", "{}"'.format(type(obj), obj))

    return serialize(obj_)


analysis_path = sys.argv[1]
parent_dir = os.path.dirname(analysis_path)
logging.basicConfig(filename=os.path.join(parent_dir, "aba_io.log"), filemode="w", level=logging.DEBUG)
logging.info('Logging to "{}"'.format(parent_dir))
logging.info('Opening ODB "{}"'.format(analysis_path))

odb = openOdb(analysis_path, readOnly=True)

try:
    fname = os.path.basename(analysis_path)
    logging.info('serializing "{}" ODB data'.format(fname))
    res = odb_serializer(
        dict(
            rootAssembly=odb.rootAssembly,
            userdata=odb.userData,
            steps=odb.steps,
            last_updated=os.path.getmtime(os.path.join(parent_dir, fname)),
        )
    )
    logging.info("serialization complete")

    logging.info("Starting export to pickle")
    with open(os.path.join(parent_dir, "aba_data.pckle"), "wb") as f:
        pickle.dump(res, f, 2)

except (BaseException, RuntimeError) as e:
    logging.error(e)
finally:
    odb.close()
logging.info("Export to pickle complete")

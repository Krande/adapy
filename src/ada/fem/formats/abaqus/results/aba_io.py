# ABAQUS/PYTHON POST PROCESSING SCRIPT
# Run using abaqus python / abaqus viewer -noGUI / abaqus cae -noGUI
# Tip! Import this into Abaqus PDE directly to further develop this script.
import inspect
import logging
import os
import sys
import traceback

import cPickle as pickle
import numpy as np
import symbolicConstants
from odbAccess import (
    ELEMENT_NODAL,
    INTEGRATION_POINT,
    NODAL,
    OdbAssemblyType,
    OdbInstanceType,
    OdbMeshElementArrayType,
    OdbMeshNodeArrayType,
    OdbStepType,
    openOdb,
)
from symbolicConstants import SymbolicConstant

_constants = [x[1] for x in inspect.getmembers(symbolicConstants, inspect.isclass)]
logger = logging.getLogger("abaqus")


def filter1(obj, attr):
    if attr[:1] == "_":
        return False
    if hasattr(obj, attr) is False:
        return False
    r = getattr(obj, attr)
    if callable(r):
        return False
    return True


def is_constant(value):
    if value in _constants:
        return True
    if isinstance(value, SymbolicConstant):
        return True
    return False


def get_data_from_attr(obj, attributes):
    att_filter = filter(lambda attr: filter1(obj, attr), attributes)
    return {attr: serialize(getattr(obj, attr)) for attr in att_filter}


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
            return serialize({key: serialize(value) for key, value in obj.items()})
        else:
            if isinstance(obj, OdbAssemblyType):
                data = get_data_from_attr(obj, attributes)
                data["instances"] = [instance_data(x) for x in obj.instances.values()]
                return data
            if isinstance(obj, OdbInstanceType):
                return obj.name
            if isinstance(obj, OdbMeshElementArrayType):
                return [x.label for x in obj]
            if isinstance(obj, OdbMeshNodeArrayType):
                return [x.label for x in obj]
            if isinstance(obj, OdbStepType):
                data = get_data_from_attr(obj, attributes)
                # frames does not get exported automatically
                data["frames"] = [get_frame_data(frame) for frame in obj.frames]
                return data
            if is_constant(obj):
                return str(obj)

            return get_data_from_attr(obj, attributes)
    else:
        raise ValueError('Unknown entity "{}", "{}"'.format(type(obj), obj))


def instance_data(obj):
    data = dict(name=obj.name)

    sc_ = {el.sectionCategory.name: el.sectionCategory for el in obj.elements}
    sc = {key: (i, [serialize(x) for x in value.sectionPoints]) for i, (key, value) in enumerate(sc_.items())}
    data["section_cats"] = sc

    data["nodeSets"] = serialize(obj.nodeSets)
    data["elementSets"] = serialize(obj.elementSets)
    data["beamOrientations"] = [x.vector for x in obj.beamOrientations] if obj.beamOrientations is not None else None
    data["nodes"] = [(n.label, [float(x) for x in n.coordinates]) for n in obj.nodes]
    data["elements"] = [(el.label, el.type, el.connectivity, sc.get(el.sectionCategory.name)[0]) for el in obj.elements]

    return data


def get_nodal_value(n):
    node_label = int(n.nodeLabel)
    element_label = int(n.elementLabel)
    sec_p = int(n.sectionPoint.number) if n.sectionPoint is not None else None
    return dict(nodeLabel=node_label, elementLabel=element_label, sec_p_num=sec_p, data=serialize(n.data))


def get_field_data(field):
    num_locs = len(field.locations)
    if num_locs > 1:
        logger.info("FieldOutput {} contains multiple section locations".format(field.name))
    curr_pos = field.locations[0].position
    if curr_pos == INTEGRATION_POINT:  # values obtained by extrapolating results calculated at the integration points.
        nodal_data = field.getSubset(position=ELEMENT_NODAL)
        components = nodal_data.componentLabels
        return "ELEMENT_NODAL", components, [get_nodal_value(n) for n in nodal_data.values]
    if curr_pos == NODAL:  # specifying the values calculated at the nodes.
        nodal_data = field.getSubset(position=NODAL)
        components = nodal_data.componentLabels
        return "NODAL", components, [(int(n.nodeLabel), serialize(n.data)) for n in nodal_data.values]

    logger.info("Skipping unsupported field position {}".format(curr_pos))
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


def get_section_data(section_obj):
    data = dict(name=section_obj.name)
    if hasattr(section_obj, "profile"):
        data["profile"] = serialize(getattr(section_obj, "profile"))
    elif hasattr(section_obj, "thickness"):
        data["thickness"] = serialize(getattr(section_obj, "thickness"))
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

logger.info('Logging to "{}"'.format(parent_dir))
logger.info('Opening ODB "{}"'.format(analysis_path))

odb = openOdb(analysis_path, readOnly=True)

try:
    fname = os.path.basename(analysis_path)
    logger.info('serializing "{}" ODB data'.format(fname))
    res = serialize(
        dict(
            rootAssembly=odb.rootAssembly,
            sections={key: get_section_data(value) for key, value in odb.sections.items()},
            profiles={key: dict(name=value.__class__.__name__, value=value) for key, value in odb.profiles.items()},
            userdata=odb.userData,
            steps=odb.steps,
            last_updated=os.path.getmtime(analysis_path),
        )
    )
    logger.info("serialization complete")

    logger.info("Starting export to pickle")
    pckle_name = fname.replace(".odb", ".pckle")
    with open(os.path.join(parent_dir, pckle_name), "wb") as f:
        pickle.dump(res, f, 2)

except (BaseException, RuntimeError) as e:
    trace_str = traceback.format_exc()
    logger.error("{}, {}".format(e, trace_str))
finally:
    odb.close()

logger.info("Export to pickle complete")

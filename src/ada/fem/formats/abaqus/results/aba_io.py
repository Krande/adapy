# ABAQUS/PYTHON: export an ODB to adapy's FEA results SQLite (ada/fem/results/resources/results.sql).
#
#     abaqus python aba_io.py <odb> <sqlite> <schema.sql>
#
# Needs Abaqus 2024+ (Python 3, with sqlite3 and numpy bundled). Not imported by adapy itself:
# read_odb.convert_odb_to_sqlite runs it.
#
# Field values are read through the ODB API's bulk data blocks and written one float32 blob per
# (instance, step, component, frame[, location, element type]). Where a field has several
# section points, the last block written fills the slot.
#
# Not exported (the tables stay empty): Materials, Sections, SectionAssignments,
# MaterialOrientations, Surfaces, BoundaryConditions, Loads. ElementInfo.IntPoints is taken from
# the integration-point field output (the Python ODB API has no numberOfIntegrationPoints); 1
# where there is none.
import getpass
import logging
import os
import sqlite3
import sys
import traceback

import numpy as np
from odbAccess import INTEGRATION_POINT, openOdb

logger = logging.getLogger("abaqus")

ASSEMBLY = "ASSEMBLY"


class OdbExporter(object):
    def __init__(self, odb, conn):
        self.odb = odb
        self.conn = conn
        self.instance_map = {}  # instance name -> InstanceID
        self.point_index = {}  # inst -> {node label: row in the instance's sorted Points}
        self.n_points = {}  # inst -> number of points
        self.elem_index = {}  # inst -> {type: {label: row within the type}}
        self.elem_count = {}  # inst -> {type: count}
        self.elem_ips = {}  # inst -> {type: integration points per element}
        self.label_to_type = {}  # inst -> {label: type}
        self.elem_conn = {}  # inst -> {label: node labels}
        self.elem_n_nodes = {}  # inst -> {type: nodes per element}
        self.field_var_map = {}  # component / history output name -> FieldID
        self.field_var_id = 0
        self.set_id = 0
        self.step_id = 0
        self.section_points_seen = set()

    def insert(self, table, row):
        self.conn.execute('INSERT INTO "%s" VALUES (%s)' % (table, ", ".join("?" * len(row))), row)

    def field_var(self, name, description):
        if name not in self.field_var_map:
            self.field_var_map[name] = self.field_var_id
            self.insert("FieldVars", (self.field_var_id, name, description))
            self.field_var_id += 1
        return self.field_var_map[name]

    # ----- topology -------------------------------------------------------

    def integration_points(self):
        """(instance name, element type) -> integration points per element, from the field output."""
        ips = {}
        for step in self.odb.steps.values():
            if len(step.frames) == 0:
                continue
            for field in step.frames[-1].fieldOutputs.values():
                for loc in field.locations:
                    if loc.position != INTEGRATION_POINT or not field.componentLabels:
                        continue
                    scalar = field.getSubset(location=loc).getScalarField(componentLabel=field.componentLabels[0])
                    for block in scalar.bulkDataBlocks:
                        if block.integrationPoints is None or len(block.integrationPoints) == 0:
                            continue
                        key = (block.instance.name if block.instance is not None else ASSEMBLY, block.baseElementType)
                        ips[key] = max(ips.get(key, 1), int(np.max(block.integrationPoints)))
        return ips

    def write_instance(self, inst_id, name, obj, ips):
        nodes = sorted(((int(n.label), [float(c) for c in n.coordinates]) for n in obj.nodes), key=lambda n: n[0])
        index = {}
        for row, (label, xyz) in enumerate(nodes):
            xyz = (list(xyz) + [0.0, 0.0, 0.0])[:3]
            self.insert("Points", (inst_id, label, xyz[0], xyz[1], xyz[2]))
            index[label] = row
        self.point_index[inst_id] = index
        self.n_points[inst_id] = len(nodes)

        elems = sorted(
            ((str(e.type), int(e.label), [int(c) for c in e.connectivity]) for e in obj.elements),
            key=lambda e: (e[0], e[1]),
        )
        self.elem_index[inst_id] = {}
        self.elem_count[inst_id] = {}
        self.elem_ips[inst_id] = {}
        self.label_to_type[inst_id] = {}
        self.elem_conn[inst_id] = {}
        self.elem_n_nodes[inst_id] = {}
        for el_type, label, conn in elems:
            self.elem_conn[inst_id][label] = conn
            self.elem_n_nodes[inst_id][el_type] = max(self.elem_n_nodes[inst_id].get(el_type, 0), len(conn))
            n_ips = ips.get((name, el_type), 1)
            self.insert("ElementInfo", (inst_id, label, el_type, n_ips))
            for seq, node in enumerate(conn):
                self.insert("ElementConnectivity", (inst_id, label, node, seq))
            count = self.elem_count[inst_id].get(el_type, 0)
            self.elem_index[inst_id].setdefault(el_type, {})[label] = count
            self.elem_count[inst_id][el_type] = count + 1
            self.elem_ips[inst_id][el_type] = n_ips
            self.label_to_type[inst_id][label] = el_type

        for set_name, elem_set in obj.elementSets.items():
            for label in _labels(elem_set.elements):
                self.insert("ElementSets", (self.set_id, set_name, inst_id, label))
                self.set_id += 1
        for set_name, node_set in obj.nodeSets.items():
            for label in _labels(node_set.nodes):
                self.insert("PointSets", (self.set_id, set_name, inst_id, label))
                self.set_id += 1

    # ----- results --------------------------------------------------------

    def write_history(self, step):
        for region_name, region in step.historyRegions.items():
            point = region.point
            node_label = int(point.node.label) if point.node is not None else -1
            elem_label = int(point.element.label) if point.element is not None else -1
            if node_label == -1 and elem_label == -1:
                inst_name = ASSEMBLY
            elif point.node is not None:
                inst_name = point.node.instanceName
            else:
                inst_name = point.element.instanceName
            inst_id = self.instance_map.get(inst_name, 0)
            for output_name, output in region.historyOutputs.items():
                var_id = self.field_var(output_name, output.description)
                for sample in output.data:
                    if len(sample) != 2:
                        raise RuntimeError("history sample of size %d, expected (time, value)" % len(sample))
                    self.insert(
                        "HistOutput",
                        (
                            region_name,
                            str(point.position),
                            inst_id,
                            elem_label,
                            node_label,
                            self.step_id,
                            var_id,
                            float(sample[0]),
                            float(sample[1]),
                        ),
                    )

    def write_fields(self, step):
        for frame_index, frame in enumerate(step.frames):
            frame_value = float(frame.frameValue)
            self.insert("Frames", (self.step_id, frame_index, int(frame.frameId), frame_value, frame.description))
            nodal, nodal_imag, elem, elem_imag = {}, {}, {}, {}
            for field in frame.fieldOutputs.values():
                for comp in field.componentLabels:
                    var_id = self.field_var(comp, field.description)
                    for loc in field.locations:
                        res_pos = str(loc.position)
                        blocks = field.getSubset(location=loc).getScalarField(componentLabel=comp).bulkDataBlocks
                        for block in blocks:
                            self.scatter(block, var_id, res_pos, nodal, nodal_imag, elem, elem_imag)
                        self.section_points(loc, blocks, res_pos)
            for (inst_id, var_id), vec in nodal.items():
                self.insert("FieldNodes", (inst_id, self.step_id, var_id, frame_value, 0, vec.tobytes()))
            for (inst_id, var_id), vec in nodal_imag.items():
                self.insert("FieldNodes", (inst_id, self.step_id, var_id, frame_value, 1, vec.tobytes()))
            for imag, buffers in ((0, elem), (1, elem_imag)):
                for (inst_id, var_id, res_pos, el_type), vec in buffers.items():
                    n_ips = self.slots_per_element(inst_id, el_type, res_pos)
                    self.insert(
                        "FieldElem",
                        (inst_id, self.step_id, var_id, res_pos, el_type, n_ips, frame_value, imag, vec.tobytes()),
                    )

    def scatter(self, block, var_id, res_pos, nodal, nodal_imag, elem, elem_imag):
        inst_id = self.instance_map.get(block.instance.name if block.instance is not None else "", 0)
        real = np.asarray(block.data, dtype=np.float32).reshape(len(block.data), -1)[:, 0]
        imag = None
        if block.conjugateData is not None:
            imag = np.asarray(block.conjugateData, dtype=np.float32).reshape(len(block.conjugateData), -1)[:, 0]

        # Nodal blocks carry node labels only; element-nodal ones carry both (each value's element
        # and the element node it sits at), and no integration points.
        if block.nodeLabels is not None and block.elementLabels is None:
            index = self.point_index.get(inst_id, {})
            rows = np.array([index.get(int(label), -1) for label in block.nodeLabels], dtype=np.int64)
            keep = rows >= 0  # values for nodes outside the instance's mesh are dropped
            key = (inst_id, var_id)
            n = self.n_points.get(inst_id, 0)
            for buffers, values in ((nodal, real), (nodal_imag, imag)):
                if values is None:
                    continue
                vec = buffers.setdefault(key, np.zeros(n, dtype="<f4"))
                vec[rows[keep]] = values[keep]
            return

        if block.elementLabels is None or len(block.elementLabels) == 0:
            return
        el_type = self.label_to_type.get(inst_id, {}).get(int(block.elementLabels[0]))
        if el_type is None:
            return
        index = self.elem_index[inst_id][el_type]
        n_elem = self.elem_count[inst_id][el_type]
        n_ips = self.slots_per_element(inst_id, el_type, res_pos)
        rows = np.array([index.get(int(label), -1) for label in block.elementLabels], dtype=np.int64)
        if res_pos == "ELEMENT_NODAL" and block.nodeLabels is not None:
            # slot = the node's place in the element's connectivity
            conn = self.elem_conn[inst_id]
            ip = np.array(
                [_position(conn.get(int(e), ()), int(n)) for e, n in zip(block.elementLabels, block.nodeLabels)],
                dtype=np.int64,
            )
            rows[ip < 0] = -1
            ip = np.maximum(ip, 0)
        elif block.integrationPoints is not None and len(block.integrationPoints) == len(rows):
            ip = np.maximum(np.asarray(block.integrationPoints, dtype=np.int64) - 1, 0)
        else:
            ip = np.zeros(len(rows), dtype=np.int64)
        keep = (rows >= 0) & (ip < n_ips)
        slots = rows[keep] * n_ips + ip[keep]
        key = (inst_id, var_id, res_pos, el_type)
        for buffers, values in ((elem, real), (elem_imag, imag)):
            if values is None:
                continue
            vec = buffers.setdefault(key, np.zeros(n_elem * n_ips, dtype="<f4"))
            vec[slots] = values[keep]

    def slots_per_element(self, inst_id, el_type, res_pos):
        """An element blob's values per element: its nodes (ELEMENT_NODAL), else its integration points."""
        if res_pos == "ELEMENT_NODAL":
            return self.elem_n_nodes[inst_id].get(el_type) or 1
        return self.elem_ips[inst_id].get(el_type) or 1

    def section_points(self, loc, blocks, res_pos):
        points = list(loc.sectionPoints)
        if not points:
            return
        for block in blocks:
            if block.elementLabels is None or len(block.elementLabels) == 0:
                continue
            name = block.instance.name if block.instance is not None else ""
            if name not in self.instance_map:
                continue
            inst_id = self.instance_map[name]
            el_type = self.label_to_type.get(inst_id, {}).get(int(block.elementLabels[0]))
            if el_type is None:
                continue
            for sp in points:
                key = (inst_id, el_type, res_pos, int(sp.number))
                if key in self.section_points_seen:
                    continue
                self.section_points_seen.add(key)
                self.insert("SectionPoints", (inst_id, el_type, res_pos, int(sp.number), sp.description))

    # ----- driver ---------------------------------------------------------

    def export(self, odb_path):
        self.insert("metadata", (os.path.splitext(os.path.basename(odb_path))[0], getpass.getuser(), odb_path))
        ips = self.integration_points()
        assembly = self.odb.rootAssembly
        self.insert("ModelInstances", (0, ASSEMBLY))
        self.write_instance(0, ASSEMBLY, assembly, ips)
        inst_id = 1
        for name, inst in assembly.instances.items():
            self.instance_map[inst.name] = inst_id
            self.insert("ModelInstances", (inst_id, name))
            self.write_instance(inst_id, inst.name, inst, ips)
            inst_id += 1
        self.conn.commit()
        logger.info("exported the mesh of %d instance(s)", inst_id - 1)

        for step in self.odb.steps.values():
            self.insert("Steps", (self.step_id, step.name, step.description, str(step.domain), step.procedure))
            self.write_history(step)
            self.write_fields(step)
            self.conn.commit()  # one transaction per step bounds the WAL on large ODBs
            logger.info('exported step "%s"', step.name)
            self.step_id += 1


def _position(conn, node):
    try:
        return list(conn).index(node)
    except ValueError:
        return -1


def _labels(members):
    """Labels of a set's nodes / elements: an array of mesh entities, or (assembly sets) a sequence of them."""
    labels = []
    for m in members:
        if hasattr(m, "label"):
            labels.append(int(m.label))
        else:
            labels.extend(int(x.label) for x in m)
    return labels


def main(odb_path, sqlite_path, schema_path):
    with open(schema_path) as f:
        schema = f.read()
    if os.path.exists(sqlite_path):
        os.remove(sqlite_path)
    conn = sqlite3.connect(sqlite_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    odb = openOdb(odb_path, readOnly=True)
    try:
        OdbExporter(odb, conn).export(odb_path)
    finally:
        odb.close()
        conn.close()


if __name__ == "__main__":
    odb_file, sqlite_file, schema_file = sys.argv[1:4]
    logging.basicConfig(
        filename=os.path.join(os.path.dirname(os.path.abspath(sqlite_file)), "aba_io.log"),
        filemode="w",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        main(odb_file, sqlite_file, schema_file)
    except BaseException:
        logger.error(traceback.format_exc())
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)
    logger.info("export complete")

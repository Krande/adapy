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
        # Label -> row lookups are numpy searchsorted over sorted label arrays, done per bulk-data
        # block: a per-value Python loop was most of an export's time on a large mesh.
        self.node_labels = {}  # inst -> sorted node labels (row = position, as in Points)
        self.elem_labels = {}  # inst -> {type: sorted element labels (row within the type)}
        self.elem_conn = {}  # inst -> {type: (n_elements, nodes per element) node labels, by row}
        self.elem_ips = {}  # inst -> {type: integration points per element}
        self.label_to_type = {}  # inst -> {label: type}
        self.field_var_map = {}  # component / history output name -> FieldID
        self.field_var_id = 0
        self.set_id = 0
        self.step_id = 0
        self.section_points_seen = set()
        self._sql = {}  # (table, columns) -> INSERT statement

    def _insert_sql(self, table, columns):
        # By column name: the schema fixes the columns, not their order.
        sql = self._sql.get((table, columns))
        if sql is None:
            sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (table, ", ".join(columns), ", ".join("?" * len(columns)))
            self._sql[(table, columns)] = sql
        return sql

    def insert(self, table, **row):
        self.conn.execute(self._insert_sql(table, tuple(row)), tuple(row.values()))

    def insert_many(self, table, columns, rows):
        """Insert an iterable of row tuples (values in the order of ``columns``)."""
        self.conn.executemany(self._insert_sql(table, tuple(columns)), rows)

    def field_var(self, name, description):
        if name not in self.field_var_map:
            self.field_var_map[name] = self.field_var_id
            self.insert("FieldVars", FieldID=self.field_var_id, Name=name, Description=description)
            self.field_var_id += 1
        return self.field_var_map[name]

    # ----- topology -------------------------------------------------------

    def integration_points(self):
        """(instance name, element type) -> integration points per element, from the field output."""
        ips = {}
        for step in _values(self.odb.steps):
            if len(step.frames) == 0:
                continue
            for field in _values(step.frames[-1].fieldOutputs):
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
        self.insert_many(
            "Points",
            ("InstanceID", "ID", "X", "Y", "Z"),
            ((inst_id, label, *(list(xyz) + [0.0, 0.0, 0.0])[:3]) for label, xyz in nodes),
        )
        self.node_labels[inst_id] = np.array([label for label, _ in nodes], dtype=np.int64)
        del nodes

        by_type = {}
        for e in obj.elements:
            by_type.setdefault(str(e.type), []).append((int(e.label), [int(c) for c in e.connectivity]))
        self.elem_labels[inst_id] = {}
        self.elem_conn[inst_id] = {}
        self.elem_ips[inst_id] = {}
        self.label_to_type[inst_id] = {}
        for el_type in sorted(by_type):
            elems = sorted(by_type.pop(el_type))
            n_ips = ips.get((name, el_type), 1)
            n_nodes = max(len(conn) for _, conn in elems)
            self.insert_many(
                "ElementInfo",
                ("InstanceID", "ElemID", "Type", "IntPoints"),
                ((inst_id, label, el_type, n_ips) for label, _ in elems),
            )
            self.insert_many(
                "ElementConnectivity",
                ("InstanceID", "ElemID", "PointID", "Seq"),
                ((inst_id, label, node, seq) for label, conn in elems for seq, node in enumerate(conn)),
            )
            self.elem_labels[inst_id][el_type] = np.array([label for label, _ in elems], dtype=np.int64)
            self.elem_conn[inst_id][el_type] = np.array(
                [conn + [-1] * (n_nodes - len(conn)) for _, conn in elems], dtype=np.int64
            ).reshape(len(elems), n_nodes)
            self.elem_ips[inst_id][el_type] = n_ips
            self.label_to_type[inst_id].update((label, el_type) for label, _ in elems)

        for set_name, elem_set in _items(obj.elementSets):
            labels = _labels(elem_set.elements)
            self.insert_many(
                "ElementSets",
                ("SetID", "Name", "InstanceID", "ElemID"),
                ((self.set_id + i, set_name, inst_id, label) for i, label in enumerate(labels)),
            )
            self.set_id += len(labels)
        for set_name, node_set in _items(obj.nodeSets):
            labels = _labels(node_set.nodes)
            self.insert_many(
                "PointSets",
                ("SetID", "Name", "InstanceID", "PointID"),
                ((self.set_id + i, set_name, inst_id, label) for i, label in enumerate(labels)),
            )
            self.set_id += len(labels)

    # ----- results --------------------------------------------------------

    def write_history(self, step):
        for region_name, region in _items(step.historyRegions):
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
            # Once per region, not once per sample: every ODB API attribute read is a call into
            # the API, and a region has a sample per increment.
            res_type = str(point.position)
            for output_name, output in _items(region.historyOutputs):
                var_id = self.field_var(output_name, output.description)
                data = output.data
                if any(len(sample) != 2 for sample in data):
                    raise RuntimeError("history output %s: samples are not (time, value) pairs" % output_name)
                self.insert_many(
                    "HistOutput",
                    ("Region", "ResType", "InstanceID", "ElemID", "PointID", "StepID", "FieldVarID", "Frame", "Value"),
                    (
                        (
                            region_name,
                            res_type,
                            inst_id,
                            elem_label,
                            node_label,
                            self.step_id,
                            var_id,
                            float(t),
                            float(v),
                        )
                        for t, v in data
                    ),
                )

    def write_fields(self, step):
        for frame_index, frame in enumerate(step.frames):
            frame_value = float(frame.frameValue)
            self.insert(
                "Frames",
                StepID=self.step_id,
                FrameID=frame_index,
                Increment=int(frame.frameId),
                FrameValue=frame_value,
                Description=frame.description,
            )
            nodal, nodal_imag, elem, elem_imag = {}, {}, {}, {}
            for field in _values(frame.fieldOutputs):
                for comp in field.componentLabels:
                    var_id = self.field_var(comp, field.description)
                    for loc in field.locations:
                        res_pos = str(loc.position)
                        blocks = field.getSubset(location=loc).getScalarField(componentLabel=comp).bulkDataBlocks
                        for block in blocks:
                            self.scatter(block, var_id, res_pos, nodal, nodal_imag, elem, elem_imag)
                        self.section_points(loc, blocks, res_pos)
            for (inst_id, var_id), vec in nodal.items():
                self.insert(
                    "FieldNodes",
                    InstanceID=inst_id,
                    StepID=self.step_id,
                    FieldVarID=var_id,
                    Frame=frame_value,
                    IsImaginary=0,
                    Data=vec.tobytes(),
                )
            for (inst_id, var_id), vec in nodal_imag.items():
                self.insert(
                    "FieldNodes",
                    InstanceID=inst_id,
                    StepID=self.step_id,
                    FieldVarID=var_id,
                    Frame=frame_value,
                    IsImaginary=1,
                    Data=vec.tobytes(),
                )
            for imag, buffers in ((0, elem), (1, elem_imag)):
                for (inst_id, var_id, res_pos, el_type), vec in buffers.items():
                    n_ips = self.slots_per_element(inst_id, el_type, res_pos)
                    self.insert(
                        "FieldElem",
                        InstanceID=inst_id,
                        StepID=self.step_id,
                        FieldVarID=var_id,
                        Location=res_pos,
                        ElemType=el_type,
                        NIPs=n_ips,
                        Frame=frame_value,
                        IsImaginary=imag,
                        Data=vec.tobytes(),
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
            labels = self.node_labels.get(inst_id, _NO_LABELS)
            rows = _rows(labels, block.nodeLabels)
            keep = rows >= 0  # values for nodes outside the instance's mesh are dropped
            key = (inst_id, var_id)
            for buffers, values in ((nodal, real), (nodal_imag, imag)):
                if values is None:
                    continue
                vec = buffers.setdefault(key, np.zeros(len(labels), dtype="<f4"))
                vec[rows[keep]] = values[keep]
            return

        if block.elementLabels is None or len(block.elementLabels) == 0:
            return
        el_type = self.label_to_type.get(inst_id, {}).get(int(block.elementLabels[0]))
        if el_type is None:
            return
        n_elem = len(self.elem_labels[inst_id][el_type])
        n_ips = self.slots_per_element(inst_id, el_type, res_pos)
        rows = _rows(self.elem_labels[inst_id][el_type], block.elementLabels)
        if res_pos == "ELEMENT_NODAL" and block.nodeLabels is not None:
            # slot = the node's place in the element's connectivity
            conn = self.elem_conn[inst_id][el_type][np.maximum(rows, 0)]
            match = conn == np.asarray(block.nodeLabels, dtype=np.int64)[:, None]
            ip = np.where(match.any(axis=1), match.argmax(axis=1), -1)
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
            conn = self.elem_conn[inst_id].get(el_type)
            return conn.shape[1] if conn is not None and conn.shape[1] else 1
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
                self.insert(
                    "SectionPoints",
                    InstanceID=inst_id,
                    ElemType=el_type,
                    Position=res_pos,
                    PointNumber=int(sp.number),
                    Description=sp.description,
                )

    # ----- driver ---------------------------------------------------------

    def export(self, odb_path):
        self.insert(
            "metadata",
            project=os.path.splitext(os.path.basename(odb_path))[0],
            user=getpass.getuser(),
            filename=odb_path,
        )
        ips = self.integration_points()
        assembly = self.odb.rootAssembly
        self.insert("ModelInstances", ID=0, Name=ASSEMBLY)
        self.write_instance(0, ASSEMBLY, assembly, ips)
        inst_id = 1
        for name, inst in _items(assembly.instances):
            self.instance_map[inst.name] = inst_id
            self.insert("ModelInstances", ID=inst_id, Name=name)
            self.write_instance(inst_id, inst.name, inst, ips)
            inst_id += 1
        self.conn.commit()
        logger.info("exported the mesh of %d instance(s)", inst_id - 1)

        for step in _values(self.odb.steps):
            self.insert(
                "Steps",
                ID=self.step_id,
                Name=step.name,
                Description=step.description,
                DomainType=str(step.domain),
                Procedure=step.procedure,
            )
            self.write_history(step)
            self.write_fields(step)
            self.conn.commit()  # one transaction per step bounds the WAL on large ODBs
            logger.info('exported step "%s"', step.name)
            self.step_id += 1


def _items(repository):
    """``(key, value)`` pairs of an ODB repository, one at a time.

    Lazily, so a large repository (one history region per output point) is never held whole:
    each value is fetched, used and released before the next.
    """
    for key in repository.keys():
        yield key, repository[key]


def _values(repository):
    for _, value in _items(repository):
        yield value


_NO_LABELS = np.zeros(0, dtype=np.int64)


_LOOKUP_TABLES = {}  # id(sorted labels) -> (sorted labels, label -> row table)


def _lookup_table(sorted_labels):
    """A label -> row array when labels are dense enough (at most 4 slots per label), else None.

    Abaqus labels are usually close to 1..N, where indexing a table beats a binary search.
    """
    key = id(sorted_labels)
    hit = _LOOKUP_TABLES.get(key)
    if hit is not None and hit[0] is sorted_labels:
        return hit[1]
    table = None
    if len(sorted_labels) and sorted_labels[0] >= 0 and sorted_labels[-1] < 4 * len(sorted_labels) + 1024:
        table = np.full(int(sorted_labels[-1]) + 1, -1, dtype=np.int64)
        table[sorted_labels] = np.arange(len(sorted_labels), dtype=np.int64)
    _LOOKUP_TABLES[key] = (sorted_labels, table)
    return table


def _rows(sorted_labels, labels):
    """Each label's row in ``sorted_labels`` (its position), -1 where it is not there."""
    labels = np.asarray(labels, dtype=np.int64)
    if len(sorted_labels) == 0:
        return np.full(len(labels), -1, dtype=np.int64)
    table = _lookup_table(sorted_labels)
    if table is not None:
        inside = (labels >= 0) & (labels < len(table))
        rows = np.full(len(labels), -1, dtype=np.int64)
        rows[inside] = table[labels[inside]]
        return rows
    rows = np.minimum(np.searchsorted(sorted_labels, labels), len(sorted_labels) - 1)
    rows[sorted_labels[rows] != labels] = -1
    return rows


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

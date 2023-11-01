import libaster
import numpy as np

from ada.fem.results.sqlite_store import SQLiteFEAStore


def export_mesh_data_to_sqlite(instance_id, mesh_name: str, mesh: libaster.Mesh, sql_store: SQLiteFEAStore):
    # Add ModelInstance to SQLite
    sql_store.insert_table("ModelInstances", [(instance_id, mesh_name)])

    # Get Point Data
    coords = mesh.getCoordinates()
    coord_values = np.asarray(coords.getValues()).reshape(-1, 3)
    point_data = [(instance_id, i, x, y, z) for i, (x, y, z) in enumerate(coord_values, start=1)]
    sql_store.insert_table("Points", point_data)

    # Get Element Data
    elem_conn_data = []
    elem_info = []
    for elem_index, nodal_conn in enumerate(mesh.getConnectivity(), start=0):
        cell_type = mesh.getCellTypeName(elem_index)
        elem_id = elem_index + 1
        int_points = -1
        elem_info.append((instance_id, elem_id, cell_type, int_points))
        for seq, node_index in enumerate(nodal_conn):
            node_id = node_index + 1
            elem_conn_data.append((instance_id, elem_id, node_id, seq))

    sql_store.insert_table("ElementConnectivity", elem_conn_data)
    sql_store.insert_table("ElementInfo", elem_info)

    # Insert Sets
    set_id = 0

    point_sets = []
    point_set_names = mesh.getGroupsOfNodes()
    point_set_nodes = mesh.getNodes(point_set_names)
    for point_set_name, point_set_nodes in zip(point_set_names, point_set_nodes):
        point_set = (set_id, point_set_name, instance_id, point_set_nodes)
        point_sets.append(point_set)
        set_id += 1
    sql_store.insert_table("PointSets", point_sets)

    groups_of_cells = mesh.getGroupsOfCells()
    cell_ids = mesh.getCells(groups_of_cells)
    cell_sets = []
    for cell_group_name, cell_id in zip(groups_of_cells, cell_ids):
        cell_sets.append((set_id, cell_group_name, instance_id, cell_id))
        set_id += 1
    sql_store.insert_table("ElementSets", cell_sets)

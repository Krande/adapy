from OCC.Core.TopoDS import TopoDS_Face
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCC.Core.BRep import BRep_Tool
from OCC.Extend.ShapeAnalysis import shape_topo_explorer
from OCC.Extend.TopologyUtils import TopologyExplorer

# Sample function to group connected faces
def group_connected_faces(shape):
    # Create a dictionary to store face connections
    face_connections = {}
    face_groups = []

    # Explore all faces in the shape
    topology = TopologyExplorer(shape)
    all_faces = list(topology.faces())

    # Initialize connections dictionary for each face
    for i, face in enumerate(all_faces):
        face_connections[face] = set()

        # Explore edges for each face to find shared edges
        edges = shape_topo_explorer(face, TopAbs_EDGE)

        for edge in edges:
            # For each edge, find which other faces contain this edge
            connected_faces = [
                f for f in all_faces if f != face and edge in shape_topo_explorer(f, TopAbs_EDGE)
            ]

            # Add these faces to the face's connection set
            face_connections[face].update(connected_faces)

    # Group faces based on connectivity
    visited_faces = set()
    for face in all_faces:
        if face in visited_faces:
            continue

        # Start a new group with the current face
        group = set([face])
        queue = [face]

        while queue:
            current_face = queue.pop()
            for connected_face in face_connections[current_face]:
                if connected_face not in visited_faces:
                    visited_faces.add(connected_face)
                    group.add(connected_face)
                    queue.append(connected_face)

        # Add this group to the list of face groups
        face_groups.append(group)

    return face_groups

# Example usage:
# Assuming 'my_shape' is your TopoDS_Shape containing the faces to group
face_groups = group_connected_faces(my_shape)

# Print the results
for i, group in enumerate(face_groups):
    print(f"Group {i + 1}:")
    for face in group:
        print(face)

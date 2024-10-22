import gmsh

# Initialize Gmsh
gmsh.initialize()
gmsh.model.add("OCC example")

# Enable the OpenCASCADE kernel
gmsh.option.setNumber("Geometry.OCCBooleanPreserveNumbering", 1)

# Create a basic geometry using the OCC kernel
# Define points
p1 = gmsh.model.occ.addPoint(0, 0, 0)
p2 = gmsh.model.occ.addPoint(1, 0, 0)
p3 = gmsh.model.occ.addPoint(1, 1, 0)
p4 = gmsh.model.occ.addPoint(0, 1, 0)

# Create lines
l1 = gmsh.model.occ.addLine(p1, p2)
l2 = gmsh.model.occ.addLine(p2, p3)
l3 = gmsh.model.occ.addLine(p3, p4)
l4 = gmsh.model.occ.addLine(p4, p1)

# Create a surface
cl = gmsh.model.occ.addCurveLoop([l1, l2, l3, l4])
surface = gmsh.model.occ.addPlaneSurface([cl])

# Synchronize to update the model
gmsh.model.occ.synchronize()

# Assign physical groups with names before fragmentation
gmsh.model.addPhysicalGroup(1, [l1, l2, l3, l4], tag=1)
gmsh.model.setPhysicalName(1, 1, "Boundary")
gmsh.model.addPhysicalGroup(2, [surface], tag=2)
gmsh.model.setPhysicalName(2, 2, "Original Surface")

# Create another shape to fragment the original surface with
# Example: A circle intersecting the original surface
circle_center = gmsh.model.occ.addPoint(0.5, 0.5, 0)
circle = gmsh.model.occ.addCircle(0.5, 0.5, 0, 0.3)
circle_loop = gmsh.model.occ.addCurveLoop([circle])
circle_surface = gmsh.model.occ.addPlaneSurface([circle_loop])

# Perform the fragmentation operation
# This will return the mapping of original to new entities
dim_tags_out, map_out = gmsh.model.occ.fragment([(2, surface)], [(2, circle_surface)])

# Synchronize the model after the operation
gmsh.model.occ.synchronize()

# Update physical groups based on the mapping from the fragmentation
for entity_map in map_out:
    original_entity = entity_map[0]
    new_entities = entity_map[1:]

    # Assign physical groups to new entities based on the original entity's dimension and tag
    if original_entity[0] == 1:  # Line (1D entity)
        for new_entity in new_entities:
            gmsh.model.addPhysicalGroup(1, [new_entity[1]], tag=original_entity[1])
            gmsh.model.setPhysicalName(1, original_entity[1], f"Boundary Fragment {new_entity[1]}")
    elif original_entity[0] == 2:  # Surface (2D entity)
        for new_entity in new_entities:
            gmsh.model.addPhysicalGroup(2, [new_entity[1]], tag=original_entity[1])
            gmsh.model.setPhysicalName(2, original_entity[1], f"Surface Fragment {new_entity[1]}")

gmsh.fltk.run()
# Optionally, you can mesh the geometry
gmsh.model.mesh.generate(2)

# Save the modified model
gmsh.write("occ_example_with_fragmentation.msh")

# Finalize Gmsh
gmsh.finalize()

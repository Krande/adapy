import gmsh

# Initialize Gmsh
gmsh.initialize()
gmsh.model.add("split_line_occ_example")

# Define the OCC geometry
# Add two points to define the original line
p1 = gmsh.model.occ.addPoint(0, 0, 0)
p2 = gmsh.model.occ.addPoint(1, 0, 0)
p3 = gmsh.model.occ.addPoint(0.5, -2, 0)
p4 = gmsh.model.occ.addPoint(0.5, 1, 0)

# Create a line segment between these points using OCC
line = gmsh.model.occ.addLine(p1, p2)
line2 = gmsh.model.occ.addLine(p3, p4)

# Add the splitting point (vertex) along the line
split_point = gmsh.model.occ.addPoint(0.5, 0, 0)
gmsh.model.occ.synchronize()
gmsh.fltk.run()

# Perform the fragmentation using occ.fragment
# The first argument is the list of objects to fragment (the line)
# The second argument is the list of objects that will be used to fragment the first list (the vertex)
result = gmsh.model.occ.fragment([(1, line2)], [(0, split_point)])

# The result of the fragment operation contains the new entities, which need to be extracted
fragmented_lines = [entity for entity in result[0] if entity[0] == 1]

# Synchronize to apply the changes
gmsh.model.occ.synchronize()

# Optionally, generate a mesh to see the result
gmsh.model.mesh.generate(1)

# Launch the Gmsh GUI to visualize the result
gmsh.fltk.run()

# Finalize Gmsh
gmsh.finalize()
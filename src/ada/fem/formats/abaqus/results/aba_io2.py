# Import the necessary modules
import csv
from odbAccess import *

# Open the .odb file
odb_file = openOdb(r'test-model.odb', readOnly=True)

# Open a CSV file for writing
with open('data.csv', 'wb') as f:
    # Create a CSV writer object
    writer = csv.writer(f)

    # Loop over all the steps in the .odb file
    for step in odb_file.steps.values():
        # Loop over all the frames in the step
        for frame in step.frames:
            # Loop over all the field outputs in the frame
            for field_output in frame.fieldOutputs.values():
                # Extract the data from the field output
                values = field_output.values
                # Write the field output name and data to the CSV file
                writer.writerow([field_output.name] + values)

# Close the .odb file
odb_file.close()

import pathlib
import re
from pathlib import Path


def find_schema_inclusions(schema_files: list[pathlib.Path], schema_directory: Path):
    """
    Finds the schema inclusion relationships.

    Parameters:
        schema_files (list[pathlib.Path]): List of schema file names to analyze (relative or absolute paths).
        schema_directory (Path): The directory where schemas are located.

    Returns:
        dict: A dictionary where keys are schema names and values are sets of schemas they include.
    """
    inclusion_pattern = re.compile(r'include "([^\"]+)"')

    # Initialize a dictionary to store schema inclusions
    inclusions = {}

    for schema_path in schema_files:
        schema_file = schema_path.name
        if not schema_path.exists():
            print(f"Warning: {schema_file} not found in {schema_directory}. Skipping.")
            continue

        # Read the schema file and search for includes
        with open(schema_path, "r") as f:
            content = f.read()

        included_schemas = set()
        for match in inclusion_pattern.findall(content):
            included_schemas.add(match)

        # Store the inclusions in the dictionary
        inclusions[schema_file] = included_schemas

    return inclusions

def main():
    # Example usage
    schema_directory = Path("schemas")
    schema_files = list(schema_directory.rglob("*.fbs"))
    inclusions = find_schema_inclusions(schema_files, schema_directory)

    # Print inclusion relationships
    for schema, included_schemas in inclusions.items():
        print(f"Schema '{schema}' includes the following schemas: {', '.join(included_schemas)}")

if __name__ == "__main__":
    main()
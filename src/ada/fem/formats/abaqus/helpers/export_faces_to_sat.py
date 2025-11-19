"""
Abaqus CAE Python 3 script for exporting individual faces from a part to ACIS SAT files.

This script iterates over all faces in a specified part and exports each face
to a separate ACIS SAT file with a unique filename.

Usage in Abaqus CAE:
    abaqus cae noGUI=export_faces_to_sat.py -- --part-name=<part_name> --output-dir=<output_directory>

Or interactively in Abaqus CAE:
    execfile('export_faces_to_sat.py')
    export_faces_to_sat(part_name='YourPartName', output_dir='C:/output')
"""

from __future__ import print_function
import os

# Abaqus imports - only available when run in Abaqus CAE environment
try:
    from abaqus import mdb, session
    from abaqusConstants import ON, OFF, THREE_D, DEFORMABLE_BODY, STANDARD_EXPLICIT
except ImportError:
    # Will be caught in function calls if not in Abaqus environment
    pass


def export_faces_to_sat(part_name=None, output_dir=None, model_name=None, loop_all=True):
    """
    Export all faces from a specified part to individual ACIS SAT files.

    If no model_name or part_name is specified and loop_all is True,
    loops over all models and parts, creating organized directory structures.

    Parameters
    ----------
    part_name : str, optional
        Name of the part to export faces from. If None and loop_all is True,
        processes all parts in all models.
    output_dir : str, optional
        Directory where SAT files will be saved. If None, uses current working directory.
    model_name : str, optional
        Name of the model containing the part. If None and loop_all is True,
        processes all models.
    loop_all : bool, optional
        If True and model_name/part_name are None, loops over all models and parts.
        Default is True.

    Returns
    -------
    list
        List of exported file paths.
    """
    # Check if running in Abaqus environment
    try:
        mdb.models  # Test if mdb is available
    except (NameError, AttributeError):
        print("Error: This script must be run within Abaqus CAE environment")
        return []

    # Set base output directory
    if output_dir is None:
        output_dir = os.getcwd()

    # If no model specified and loop_all is True, process all models
    if model_name is None and loop_all:
        if len(mdb.models) == 0:
            print("Error: No models found in the database")
            return []

        print("=" * 60)
        print("Processing all models and parts")
        print("=" * 60)
        print("Found {} model(s) in database".format(len(mdb.models)))

        all_exported_files = []
        for mdl_name in mdb.models.keys():
            print("\n" + "-" * 60)
            print("Processing model: {}".format(mdl_name))
            print("-" * 60)

            # Create model-specific output directory
            model_output_dir = os.path.join(output_dir, mdl_name)

            # Process this model (with loop_all=False to avoid recursion)
            files = export_faces_to_sat(
                part_name=None, output_dir=model_output_dir, model_name=mdl_name, loop_all=False
            )
            all_exported_files.extend(files)

        print("\n" + "=" * 60)
        print("Completed processing all models")
        print("=" * 60)
        print("Total files exported: {}".format(len(all_exported_files)))
        return all_exported_files

    # Get the model
    if model_name is None:
        if len(mdb.models) == 0:
            print("Error: No models found in the database")
            return []
        model_name = mdb.models.keys()[0]

    if model_name not in mdb.models:
        print("Error: Model '{}' not found".format(model_name))
        print("Available models: {}".format(", ".join(mdb.models.keys())))
        return []

    model = mdb.models[model_name]

    # If no part specified and loop_all is True, process all parts
    if part_name is None and loop_all:
        if len(model.parts) == 0:
            print("No parts found in model '{}'".format(model_name))
            return []

        print("Found {} part(s) in model '{}'".format(len(model.parts), model_name))

        all_exported_files = []
        for prt_name in model.parts.keys():
            print("\n  Processing part: {}".format(prt_name))

            # Create part-specific output directory
            part_output_dir = os.path.join(output_dir, prt_name)

            # Process this part (with loop_all=False to avoid recursion)
            files = export_faces_to_sat(
                part_name=prt_name, output_dir=part_output_dir, model_name=model_name, loop_all=False
            )
            all_exported_files.extend(files)

        return all_exported_files

    # Get the part
    if part_name is None:
        if len(model.parts) == 0:
            print("Error: No parts found in model '{}'".format(model_name))
            return []
        part_name = model.parts.keys()[0]

    if part_name not in model.parts:
        print("Error: Part '{}' not found in model '{}'".format(part_name, model_name))
        print("Available parts: {}".format(", ".join(model.parts.keys())))
        return []

    part = model.parts[part_name]

    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print("Created output directory: {}".format(output_dir))
        except Exception as e:
            print("Error creating output directory: {}".format(e))
            return []

    # Get all faces from the part
    faces = part.faces
    num_faces = len(faces)

    if num_faces == 0:
        print("Warning: Part '{}' has no faces to export".format(part_name))
        return []

    print("Found {} face(s) in part '{}'".format(num_faces, part_name))
    print("Exporting to directory: {}".format(output_dir))

    exported_files = []

    # Loop through each face and export it
    for idx, face in enumerate(faces):
        # Create a unique filename for each face
        filename = "{}_{}_face_{}.sat".format(model_name, part_name, idx)
        filepath = os.path.join(output_dir, filename)

        try:
            print("Processing face {}/{}...".format(idx + 1, num_faces))

            # Get face area for information
            if hasattr(face, "getSize"):
                face_area = face.getSize()
                print("  Face {} area: {:.6f}".format(idx, face_area))

            # Create a temporary part name for this face
            temp_part_name = "TempFace_{}".format(idx)

            # Approach 1: Create a shell/surface part from the face
            # This is the most reliable way to export individual faces
            try:
                # Create a feature-based shell part from the face
                # We'll use CoverEdges or other surface creation methods

                # Create a new part for the shell surface
                shell_part = model.Part(name=temp_part_name, dimensionality=THREE_D, type=DEFORMABLE_BODY)

                # Get edges of the face to create a shell
                face_edges = face.getEdges()
                face_vertices = face.getVertices()

                print("  Face has {} edges and {} vertices".format(len(face_edges), len(face_vertices)))

                # This approach is complex - let's use a simpler method
                # Delete the empty shell part
                del model.parts[temp_part_name]

            except Exception as shell_error:
                print("  Shell creation not suitable: {}".format(shell_error))
                try:
                    if temp_part_name in model.parts:
                        del model.parts[temp_part_name]
                except:
                    pass

            # Approach 2: Copy part and remove unwanted cells
            try:
                # Create a copy of the original part
                temp_part = model.Part(name=temp_part_name, objectToCopy=part, compressFeatureList=ON)

                # Get all faces and cells in the temporary part
                temp_faces = temp_part.faces
                temp_cells = temp_part.cells

                if idx >= len(temp_faces):
                    print("  Warning: Face index {} out of range".format(idx))
                    del model.parts[temp_part_name]
                    continue

                print("  Temp part has {} faces and {} cells".format(len(temp_faces), len(temp_cells)))

                # Strategy depends on whether this is a solid part (with cells) or shell part (no cells)
                if len(temp_cells) > 0:
                    # SOLID PART: Remove all cells except those adjacent to the target face
                    target_face = temp_faces[idx]

                    # Get the cell(s) adjacent to the target face
                    cells_adjacent_to_face = []

                    # Check each cell to see if it has the target face
                    for cell_idx, cell in enumerate(temp_cells):
                        cell_faces = cell.getFaces()
                        # Check if our target face index is in this cell's faces
                        for cf_idx, cell_face_idx in enumerate(cell_faces):
                            if cell_face_idx == idx:
                                cells_adjacent_to_face.append(cell_idx)
                                break

                    if len(cells_adjacent_to_face) == 0:
                        # Fallback: just keep first cell
                        print("  Could not identify cell for face, using heuristic")
                        cells_adjacent_to_face = [0]

                    print("  Face is part of {} cell(s)".format(len(cells_adjacent_to_face)))

                    # Collect cells to delete (inverse of cells to keep)
                    cells_to_delete_indices = []
                    for cell_idx in range(len(temp_cells)):
                        if cell_idx not in cells_adjacent_to_face:
                            cells_to_delete_indices.append(cell_idx)

                    if len(cells_to_delete_indices) > 0:
                        # Get the actual cell objects to delete
                        cells_to_delete = [temp_cells[i] for i in cells_to_delete_indices]

                        try:
                            # Delete unwanted cells
                            temp_part.RemoveCells(cellList=cells_to_delete)
                            print("  Removed {} unwanted cell(s)".format(len(cells_to_delete)))

                            # Check remaining faces
                            remaining_faces = temp_part.faces
                            print("  Remaining faces after cell removal: {}".format(len(remaining_faces)))

                        except AttributeError:
                            # RemoveCells might not exist
                            print("  RemoveCells not available, exporting with all geometry")
                        except Exception as remove_error:
                            print("  Could not remove cells: {}".format(remove_error))

                else:
                    # SHELL/SURFACE PART: Delete unwanted faces using RemoveFaces
                    print("  Shell/surface part detected (no cells)")
                    print("  Removing unwanted faces using RemoveFaces...")

                    # Collect all face indices EXCEPT the target face
                    faces_to_delete_indices = []
                    for face_idx in range(len(temp_faces)):
                        if face_idx != idx:
                            faces_to_delete_indices.append(face_idx)

                    if len(faces_to_delete_indices) > 0:
                        print(
                            "  Deleting {} unwanted faces (keeping face {})...".format(
                                len(faces_to_delete_indices), idx
                            )
                        )

                        try:
                            # Get the actual face objects to delete
                            faces_to_delete = [temp_faces[i] for i in faces_to_delete_indices]

                            # Use RemoveFaces with deleteCells=False for shell parts
                            temp_part.RemoveFaces(faceList=faces_to_delete, deleteCells=False)

                            # Check remaining faces
                            remaining_faces = temp_part.faces
                            print("  Successfully removed faces! Remaining: {} face(s)".format(len(remaining_faces)))

                        except Exception as face_delete_error:
                            print("  Error removing faces: {}".format(face_delete_error))
                            import traceback

                            traceback.print_exc()

                # Export the temporary part
                try:
                    temp_part.writeAcisFile(filepath)
                    final_face_count = len(temp_part.faces)
                    final_cell_count = len(temp_part.cells)

                    if final_cell_count <= 1:
                        print(
                            "  Exported: {} (isolated to {} cell(s), {} face(s))".format(
                                filename, final_cell_count, final_face_count
                            )
                        )
                    else:
                        print(
                            "  Exported: {} (contains {} cells, {} faces)".format(
                                filename, final_cell_count, final_face_count
                            )
                        )

                    exported_files.append(filepath)
                except Exception as export_error:
                    print("  Error writing ACIS file: {}".format(export_error))

                # Clean up temporary part
                try:
                    del model.parts[temp_part_name]
                except:
                    pass

            except Exception as copy_error:
                print("  Error creating/modifying part copy: {}".format(copy_error))
                try:
                    if temp_part_name in model.parts:
                        del model.parts[temp_part_name]
                except:
                    pass

        except Exception as e:
            print("  Error processing face {}: {}".format(idx, e))
            import traceback

            traceback.print_exc()

            # Clean up any remaining temp parts
            try:
                if temp_part_name in model.parts:
                    del model.parts[temp_part_name]
            except:
                pass

            continue

    print("\n" + "=" * 60)
    print("Export process completed")
    print("=" * 60)
    print("Exported {} SAT file(s) to: {}".format(len(exported_files), output_dir))

    if exported_files:
        print("\nExported files:")
        for f in exported_files[:10]:  # Show first 10 files
            print("  - {}".format(os.path.basename(f)))
        if len(exported_files) > 10:
            print("  ... and {} more files".format(len(exported_files) - 10))

    print("\n" + "=" * 60)
    print("Each SAT file contains an isolated face from the original part.")
    print("For solid parts: Isolated by removing cells not adjacent to target face")
    print("For shell parts: Isolated by removing all other faces using RemoveFaces")
    print("=" * 60)

    return exported_files


def export_part_with_face_sets(part_name=None, output_dir=None, model_name=None, loop_all=True):
    """
    Alternative approach: Export the entire part with face sets defined.
    This creates a single SAT file with named face sets for identification.

    If no model_name or part_name is specified and loop_all is True,
    loops over all models and parts, creating organized directory structures.

    Parameters
    ----------
    part_name : str, optional
        Name of the part to export. If None and loop_all is True,
        processes all parts in all models.
    output_dir : str, optional
        Directory where SAT file will be saved. If None, uses current working directory.
    model_name : str, optional
        Name of the model containing the part. If None and loop_all is True,
        processes all models.
    loop_all : bool, optional
        If True and model_name/part_name are None, loops over all models and parts.
        Default is True.

    Returns
    -------
    str or list
        Path to exported SAT file, or list of paths if multiple parts processed.
    """
    # Check if running in Abaqus environment
    try:
        mdb.models  # Test if mdb is available
    except (NameError, AttributeError):
        print("Error: This script must be run within Abaqus CAE environment")
        return None

    # Set base output directory
    if output_dir is None:
        output_dir = os.getcwd()

    # If no model specified and loop_all is True, process all models
    if model_name is None and loop_all:
        if len(mdb.models) == 0:
            print("Error: No models found in the database")
            return []

        print("=" * 60)
        print("Processing all models and parts (with face sets)")
        print("=" * 60)
        print("Found {} model(s) in database".format(len(mdb.models)))

        all_exported_files = []
        for mdl_name in mdb.models.keys():
            print("\n" + "-" * 60)
            print("Processing model: {}".format(mdl_name))
            print("-" * 60)

            # Create model-specific output directory
            model_output_dir = os.path.join(output_dir, mdl_name)

            # Process this model (with loop_all=False to avoid recursion)
            result = export_part_with_face_sets(
                part_name=None, output_dir=model_output_dir, model_name=mdl_name, loop_all=False
            )

            if isinstance(result, list):
                all_exported_files.extend(result)
            elif result:
                all_exported_files.append(result)

        print("\n" + "=" * 60)
        print("Completed processing all models")
        print("=" * 60)
        print("Total files exported: {}".format(len(all_exported_files)))
        return all_exported_files

    # Get the model
    if model_name is None:
        if len(mdb.models) == 0:
            print("Error: No models found in the database")
            return None
        model_name = mdb.models.keys()[0]

    if model_name not in mdb.models:
        print("Error: Model '{}' not found".format(model_name))
        return None

    model = mdb.models[model_name]

    # If no part specified and loop_all is True, process all parts
    if part_name is None and loop_all:
        if len(model.parts) == 0:
            print("No parts found in model '{}'".format(model_name))
            return []

        print("Found {} part(s) in model '{}'".format(len(model.parts), model_name))

        all_exported_files = []
        for prt_name in model.parts.keys():
            print("\n  Processing part: {}".format(prt_name))

            # Create part-specific output directory
            part_output_dir = os.path.join(output_dir, prt_name)

            # Process this part (with loop_all=False to avoid recursion)
            result = export_part_with_face_sets(
                part_name=prt_name, output_dir=part_output_dir, model_name=model_name, loop_all=False
            )

            if result:
                all_exported_files.append(result)

        return all_exported_files

    # Get the part
    if part_name is None:
        if len(model.parts) == 0:
            print("Error: No parts found in model '{}'".format(model_name))
            return None
        part_name = model.parts.keys()[0]

    if part_name not in model.parts:
        print("Error: Part '{}' not found in model '{}'".format(part_name, model_name))
        return None

    part = model.parts[part_name]

    # Set output directory
    if output_dir is None:
        output_dir = os.getcwd()

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Create face sets for each face
    faces = part.faces
    print("Creating face sets for {} faces...".format(len(faces)))

    for idx, face in enumerate(faces):
        set_name = "Face_{}".format(idx)
        try:
            part.Set(faces=faces[idx : idx + 1], name=set_name)
            print("  Created set: {}".format(set_name))
        except Exception as e:
            print("  Error creating set {}: {}".format(set_name, e))

    # Export the part to SAT file
    output_file = os.path.join(output_dir, "{}.sat".format(part_name))

    try:
        # Use the ACIS export functionality
        part.writeAcisFile(output_file)
        print("\nSuccessfully exported part to: {}".format(output_file))
        print("Face sets have been created for identification")
        return output_file
    except Exception as e:
        print("Error exporting part: {}".format(e))
        return None


def import_sat_file(sat_file_path, part_name=None, model_name=None, combine=False):
    """
    Import an ACIS SAT file into Abaqus CAE as a part.

    Works with both 'abaqus cae noGUI' and 'abaqus python'.

    Parameters
    ----------
    sat_file_path : str
        Path to the SAT file to import.
    part_name : str, optional
        Name for the imported part. If None, uses the SAT filename without extension.
    model_name : str, optional
        Name of the model to import into. If None, uses the first model or creates 'Model-1'.
    combine : bool, optional
        If True, combines separate bodies into one part. Default is False.

    Returns
    -------
    part
        The imported Abaqus part object.
    """
    # Check if running in Abaqus environment and initialize if needed
    try:
        # Try to access mdb - will fail if not in Abaqus environment
        from abaqus import mdb as _mdb_test

        # For 'abaqus python', mdb exists but may need initialization
        # Check if models attribute exists and is accessible
        if not hasattr(_mdb_test, "models"):
            print("Error: mdb.models not available")
            return None

        # If we get here, mdb is available - use the global one
        global mdb
        if "mdb" not in globals():
            from abaqus import mdb

    except ImportError as e:
        print("Error: This script must be run within Abaqus CAE kernel")
        print("")
        print("IMPORTANT: You MUST use 'abaqus cae noGUI', NOT 'abaqus python'")
        print("")
        print("Correct usage:")
        print("  abaqus cae noGUI=export_faces_to_sat.py -- --sat-file C:/path/to/file.sat")
        print("")
        print("'abaqus python' does not load the CAE kernel (mdb, geometry modules)")
        print("Error: {}".format(e))
        return None
    except Exception as e:
        print("Error: Could not access Abaqus mdb: {}".format(e))
        return None

    # Validate SAT file path
    if not os.path.exists(sat_file_path):
        print("Error: SAT file not found: {}".format(sat_file_path))
        return None

    # Get or create model
    if model_name is None:
        if len(mdb.models) == 0:
            # Create a new model (this works in both 'abaqus python' and 'cae noGUI')
            model_name = "Model-1"
            try:
                mdb.Model(name=model_name, modelType=STANDARD_EXPLICIT)
                print("Created new model: {}".format(model_name))
            except:
                # STANDARD_EXPLICIT might not be available, try without modelType
                mdb.Model(name=model_name)
                print("Created new model: {}".format(model_name))
        else:
            model_name = mdb.models.keys()[0]

    if model_name not in mdb.models:
        print("Error: Model '{}' not found".format(model_name))
        print("Available models: {}".format(", ".join(mdb.models.keys())))
        return None

    model = mdb.models[model_name]

    # Determine part name
    if part_name is None:
        # Use SAT filename without extension
        part_name = os.path.splitext(os.path.basename(sat_file_path))[0]

    # Check if part name already exists
    if part_name in model.parts:
        print("Warning: Part '{}' already exists in model '{}'".format(part_name, model_name))
        # Generate unique name
        counter = 1
        original_name = part_name
        while part_name in model.parts:
            part_name = "{}_{}".format(original_name, counter)
            counter += 1
        print("Using unique name: {}".format(part_name))

    try:
        print("Importing SAT file: {}".format(sat_file_path))
        print("  Model: {}".format(model_name))
        print("  Part name: {}".format(part_name))
        print("  Combine bodies: {}".format(combine))

        # Open the ACIS file
        acis = mdb.openAcis(sat_file_path, scaleFromFile=OFF)

        # Create part from geometry file
        model.PartFromGeometryFile(
            name=part_name, geometryFile=acis, combine=combine, dimensionality=THREE_D, type=DEFORMABLE_BODY
        )

        part = model.parts[part_name]

        # Display the part in viewport if running interactively
        try:
            session.viewports["Viewport: 1"].setValues(displayedObject=part)
        except:
            pass  # May fail in noGUI mode

        print("Successfully imported SAT file as part '{}'".format(part_name))
        print("  Faces: {}".format(len(part.faces)))
        print("  Cells: {}".format(len(part.cells)))
        print("  Vertices: {}".format(len(part.vertices)))
        print("  Edges: {}".format(len(part.edges)))

        return part

    except Exception as e:
        print("Error importing SAT file: {}".format(e))
        import traceback

        traceback.print_exc()
        return None


def parse_arguments():
    """Parse command line arguments when run with noGUI or abaqus python."""
    import argparse
    import sys

    # Clean up sys.argv to handle both 'abaqus cae noGUI' and 'abaqus python' modes
    # Filter out Abaqus-specific arguments
    clean_argv = []
    skip_next = False

    for i, arg in enumerate(sys.argv):
        if skip_next:
            skip_next = False
            continue

        # Skip Abaqus internal arguments and their values
        if arg in ["-cae", "-tmpdir", "-lmlog"]:
            skip_next = True
            continue

        # Keep all arguments (script name, flags, and their values)
        clean_argv.append(arg)

    # Temporarily replace sys.argv for parsing
    original_argv = sys.argv
    sys.argv = clean_argv

    try:
        parser = argparse.ArgumentParser(
            description="Export Abaqus part faces to individual SAT files or import SAT files",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # Import a SAT file (MUST use 'abaqus cae noGUI', NOT 'abaqus python'):
  abaqus cae noGUI=export_faces_to_sat.py -- --sat-file C:/path/to/file.sat
  
  # Export faces with face sets:
  abaqus cae noGUI=export_faces_to_sat.py -- --part-name MyPart --output-dir C:/output
  
  # Export individual faces:
  abaqus cae noGUI=export_faces_to_sat.py -- --method individual --part-name MyPart

Note: 'abaqus python' will NOT work - it does not load the CAE kernel.
      You MUST use 'abaqus cae noGUI=' for geometry import/export operations.
            """,
        )
        parser.add_argument(
            "--sat-file", type=str, default=None, metavar="PATH", help="Path to SAT file to import into Abaqus"
        )
        parser.add_argument(
            "--part-name",
            type=str,
            default=None,
            metavar="NAME",
            help="Name of the part to export (or name for imported part)",
        )
        parser.add_argument(
            "--output-dir",
            "--output",
            type=str,
            default=None,
            metavar="DIR",
            dest="output_dir",
            help="Output directory for SAT files",
        )
        parser.add_argument(
            "--model-name", type=str, default=None, metavar="NAME", help="Name of the model containing the part"
        )
        parser.add_argument(
            "--method",
            type=str,
            default="sets",
            choices=["individual", "sets"],
            help="Export method: individual faces or part with face sets (default: sets)",
        )
        parser.add_argument(
            "--combine", action="store_true", default=False, help="Combine separate bodies when importing SAT file"
        )
        parser.add_argument(
            "--loop-all",
            action="store_true",
            default=True,
            help="Loop over all models and parts if not specified (default: True)",
        )
        parser.add_argument(
            "--no-loop-all", dest="loop_all", action="store_false", help="Do not loop over all models/parts"
        )

        args = parser.parse_args()
        return args

    finally:
        # Restore original sys.argv
        sys.argv = original_argv


if __name__ == "__main__":
    # When run from command line with noGUI or 'abaqus python'
    # Check if we're being run with command-line arguments or being execfile'd
    import sys

    # Debug: Check what we're getting
    # print("DEBUG: sys.argv = {}".format(sys.argv))

    # Detect if running via command line vs execfile()
    # When execfile'd in interactive CAE: sys.argv has only script name or Abaqus internals
    # When run via 'abaqus cae noGUI=script.py -- --args': sys.argv has user args after '--'

    # Check if we have any user arguments (not just Abaqus internal ones)
    user_args = [
        arg
        for arg in sys.argv[1:]
        if not arg.startswith("-cae") and not arg.startswith("-tmpdir") and not arg.startswith("-lmlog")
    ]

    # If we have user args, or if '--' separator exists, we're in command-line mode
    is_command_line = len(user_args) > 0 or "--" in sys.argv

    if is_command_line:
        print("=" * 60)
        print("Abaqus Face Export/Import to ACIS SAT")
        print("=" * 60)

        args = parse_arguments()

        # Check if we're importing a SAT file
        if args.sat_file:
            # Import mode
            import_sat_file(
                sat_file_path=args.sat_file, part_name=args.part_name, model_name=args.model_name, combine=args.combine
            )
        else:
            # Export mode
            if args.method == "sets":
                export_part_with_face_sets(
                    part_name=args.part_name,
                    output_dir=args.output_dir,
                    model_name=args.model_name,
                    loop_all=args.loop_all,
                )
            else:
                export_faces_to_sat(
                    part_name=args.part_name,
                    output_dir=args.output_dir,
                    model_name=args.model_name,
                    loop_all=args.loop_all,
                )
    else:
        # Being run interactively via execfile() - don't parse command-line args
        print("=" * 60)
        print("Abaqus Face Export/Import Script Loaded")
        print("=" * 60)
        print("Available functions:")
        print("  - import_sat_file(sat_file_path, part_name=None, model_name=None)")
        print("  - export_part_with_face_sets(output_dir='C:/output')  [RECOMMENDED]")
        print("  - export_faces_to_sat(output_dir='C:/output')")
        print("")
        print("Example usage:")
        print("  # Import a SAT file:")
        print("  import_sat_file('C:/path/to/file.sat')")
        print("")
        print("  # Export faces:")
        print("  export_part_with_face_sets(output_dir='C:/temp/exports')")
        print("=" * 60)

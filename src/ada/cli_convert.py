import argparse
import pathlib


def app():
    import ada


    parser = argparse.ArgumentParser(description="ADA CLI - A tool for converting CAD/FEA models and visualizations.")
    parser.add_argument(
        "input",
        type=str,
        help="Input file path (supports IFC, STEP/STP, XML (genie xml), .INP (abaqus), .FEM (sesam)).",
    )
    parser.add_argument(
        "output",
        type=str,
        help="Output file path (supports IFC, STEP/STP, GLTF/GLB, XML (genie xml)).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (e.g., DEBUG, INFO, WARNING, ERROR).",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="When reading ACIS/SAT, split bodies into individual AdvancedFace objects (one shape per face).",
    )

    args = parser.parse_args()

    ada.logger.setLevel(args.log_level)
    ada.logger.propagate = False

    input_file = args.input
    in_suffix = input_file.split(".")[-1].lower()

    if in_suffix.lower() == "ifc":
        model = ada.from_ifc(input_file)
    elif in_suffix.lower() in ["step", "stp"]:
        model = ada.from_step(input_file)
    elif in_suffix.lower() in "xml":
        model = ada.from_genie_xml(input_file)
    elif in_suffix.lower() in ("inp", "fem"):
        model = ada.from_fem(input_file)
    elif in_suffix.lower() in ("sat", "acis"):
        model = ada.from_acis(input_file, split=args.split)
    else:
        raise ValueError(f"Unsupported input file format: {in_suffix}")

    # Outputs
    output_file = args.output
    out_suffix = output_file.split(".")[-1].lower()
    output_file = pathlib.Path(output_file)
    if out_suffix.lower() == "ifc":
        model.to_ifc(output_file)
    elif out_suffix.lower() in ["step", "stp"]:
        model.to_stp(output_file)
    elif out_suffix.lower() == ("gltf", "glb"):
        model.to_gltf(output_file)
    elif out_suffix.lower() == "xml":
        model.to_genie_xml(output_file)
    elif out_suffix.lower() == "inp" and in_suffix in ("fem",):

        model.to_fem(model.name, fem_format="abaqus", scratch_dir=output_file)
    else:
        raise ValueError(f"Unsupported output file format: {out_suffix}")


if __name__ == '__main__':
    try:
        app()
    except Exception as e:
        import traceback

        traceback.print_exc()
        exit(1)
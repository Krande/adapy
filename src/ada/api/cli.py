import argparse
import pathlib
import sys

READ_FORMATS = ("ifc", "step", "stp", "xml", "inp", "fem", "sat", "acis")
WRITE_FORMATS = ("ifc", "step", "stp", "gltf", "glb", "xml", "inp")
VIEW_FORMATS = READ_FORMATS


def _suffix(path: str) -> str:
    return pathlib.Path(path).suffix.lstrip(".").lower()


def _load(input_file: str, split: bool = False, limit: int | None = None):
    import ada

    suffix = _suffix(input_file)
    if suffix == "ifc":
        return ada.from_ifc(input_file)
    if suffix in ("step", "stp"):
        return ada.from_step(input_file)
    if suffix == "xml":
        return ada.from_genie_xml(input_file)
    if suffix in ("inp", "fem"):
        return ada.from_fem(input_file)
    if suffix in ("sat", "acis"):
        return ada.from_acis(input_file, split=split, limit=limit)
    raise ValueError(f"Unsupported input file format: {suffix!r}. Supported: {READ_FORMATS}")


def _write(model, output_file: str) -> None:
    suffix = _suffix(output_file)
    out_path = pathlib.Path(output_file)
    if suffix == "ifc":
        model.to_ifc(out_path)
    elif suffix in ("step", "stp"):
        model.to_stp(out_path)
    elif suffix in ("gltf", "glb"):
        model.to_gltf(out_path)
    elif suffix == "xml":
        model.to_genie_xml(out_path)
    elif suffix == "inp":
        model.to_fem(model.name, fem_format="abaqus", scratch_dir=out_path)
    else:
        raise ValueError(f"Unsupported output file format: {suffix!r}. Supported: {WRITE_FORMATS}")


def _cmd_convert(args: argparse.Namespace) -> None:
    model = _load(args.input, split=args.split, limit=args.limit)
    _write(model, args.output)


def _cmd_view(args: argparse.Namespace) -> None:
    model = _load(args.input, split=args.split, limit=args.limit)
    model.show(renderer=args.renderer, host=args.host, ws_port=args.ws_port)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ada",
        description="ADA CLI - convert and view CAD/FEM models (Genie XML, IFC, STEP, FEM, ACIS, glTF).",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR).")

    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser(
        "convert",
        help="Convert between supported CAD/FEM formats.",
        description=(
            f"Convert an input model to another format. "
            f"Input formats: {', '.join(READ_FORMATS)}. "
            f"Output formats: {', '.join(WRITE_FORMATS)}."
        ),
    )
    convert.add_argument("input", help="Input file path.")
    convert.add_argument("output", help="Output file path (format is inferred from extension).")
    convert.add_argument("--split", action="store_true", help="Split ACIS/SAT bodies into individual faces.")
    convert.add_argument("--limit", type=int, default=None, help="Limit the number of geometries (debugging).")
    convert.set_defaults(func=_cmd_convert)

    view = subparsers.add_parser(
        "view",
        help="Open the built-in web viewer on the given file.",
        description=f"Open the adapy web viewer for a supported file. Supported: {', '.join(VIEW_FORMATS)}.",
    )
    view.add_argument("input", help="Input file path.")
    view.add_argument("--renderer", default="react", choices=["react", "pygfx", "trimesh"], help="Viewer renderer.")
    view.add_argument("--host", default="localhost", help="Web viewer host.")
    view.add_argument("--ws-port", type=int, default=8765, help="WebSocket port.")
    view.add_argument("--split", action="store_true", help="Split ACIS/SAT bodies into individual faces.")
    view.add_argument("--limit", type=int, default=None, help="Limit the number of geometries (debugging).")
    view.set_defaults(func=_cmd_view)

    return parser


def app(argv: list[str] | None = None) -> None:
    import ada

    parser = _build_parser()
    args = parser.parse_args(argv)

    ada.logger.setLevel(args.log_level)
    ada.logger.propagate = False

    args.func(args)


if __name__ == "__main__":
    try:
        app()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)

import pathlib
import subprocess

from ada.fem.formats.sesam.sesam_exe_locator import get_genie_default_exe_path


def export_fem_js_str(tfile_path: pathlib.Path, superid=1):
    if isinstance(tfile_path, str):
        tfile_path = pathlib.Path(tfile_path)

    tfile_path = tfile_path.resolve().absolute()
    tfile_path.parent.mkdir(parents=True, exist_ok=True)

    return f"""
GenieRules.Meshing.superElementType = {superid};
ExportMeshFem().DoExport("{tfile_path.as_posix()}");
"""


def start_genie(
    genie_xml: pathlib.Path,
    workspace_name="ada_dev",
    run_externally=False,
    extra_js="",
    use_dual_assembly=False,
    export_fem_path=None,
    exit_on_finish=False,
    workspace_path=None,
):

    if isinstance(genie_xml, str):
        genie_xml = pathlib.Path(genie_xml)

    genie_xml = genie_xml.resolve().absolute()
    dual_ass = ""
    if use_dual_assembly:
        dual_ass = "\nGenieRules.Geometry.AssemblyType = DualAssembly;"

    start_str = f"""GenieRules.Compatibility.version = "V8.11-02";
GenieRules.Tolerances.useTolerantModelling = true;
GenieRules.Tolerances.angleTolerance = 2 deg;

GenieRules.Meshing.autoSimplifyTopology = true;
GenieRules.Meshing.eliminateInternalEdges = true;
GenieRules.BeamCreation.DefaultCurveOffset = ReparameterizedBeamCurveOffset();{dual_ass}
GenieRules.Transformation.DefaultConnectedCopy = false;
"""
    genie_exe = get_genie_default_exe_path()
    if genie_exe is None:
        raise ValueError("Please set the environment variable ADA_GENIE_EXE to the path of the Genie executable")

    startup_js_file = genie_xml.parent / "startup.js"
    if workspace_path is None:
        workspace = genie_xml.parent / f"workspace/{workspace_name}/{workspace_name}"
    else:
        workspace = pathlib.Path(workspace_path)

    xml_import = f'XmlImporter = ImportConceptXml();\nXmlImporter.DoImport("{genie_xml.as_posix()}");'

    with open(startup_js_file, "w") as f:
        f.write(start_str + "\n")
        f.write(xml_import + "\n")
        f.write(extra_js)
        if export_fem_path is not None:
            js_str = export_fem_js_str(export_fem_path)
            f.write(js_str)

    args = f'"{genie_exe}" {workspace.absolute().as_posix()} /new /javascript_execution_policy=unsafe /com={startup_js_file.resolve().absolute().as_posix()}'
    if exit_on_finish:
        args += " /exit"

    if run_externally:  # without blocking
        subprocess.Popen(args, shell=True)
    else:
        subprocess.run(args, shell=True)

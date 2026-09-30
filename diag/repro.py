"""ifcopenshell 0.9 osx-arm64 segfault repro: ifcopenshell.geom.iterator on an IFC4X3_ADD2 file.

Each case runs in a child process so a segfault is reported as an exit code rather than killing the
job. Usage: python diag/repro.py <label> [--preimport mod1,mod2]
"""

import argparse
import json
import pathlib
import subprocess
import sys
import textwrap

HERE = pathlib.Path(__file__).parent

CHILD = textwrap.dedent(
    """
    import faulthandler, importlib, sys
    faulthandler.enable()
    for mod in {preimport!r}:
        importlib.import_module(mod)
        print("preimported", mod, flush=True)
    import ifcopenshell, ifcopenshell.geom
    print("ifcopenshell", ifcopenshell.version, ifcopenshell.__file__, flush=True)
    path = {path!r}
    if path == "MINIMAL":
        import ifcopenshell.api
        f = ifcopenshell.api.project.create_file(version="IFC4X3_ADD2")
        project = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="p")
        ifcopenshell.api.unit.assign_unit(f)
        ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
        body = ifcopenshell.api.context.add_context(
            f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx
        )
        el = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingElementProxy", name="box")
        rep = ifcopenshell.api.geometry.add_wall_representation(f, context=body, length=1.0, height=1.0, thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(f, product=el, representation=rep)
        ifcopenshell.api.geometry.edit_object_placement(f, product=el)
    else:
        f = ifcopenshell.open(path)
    print("schema", f.schema_identifier, "entities", len(list(f)), flush=True)
    print("constructing iterator", flush=True)
    it = ifcopenshell.geom.iterator(ifcopenshell.geom.settings(), f)
    print("iterator constructed; initialize ->", it.initialize(), flush=True)
    n = 0
    while True:
        n += 1
        if not it.next():
            break
    print("shapes iterated:", n, flush=True)
    s = ifcopenshell.geom.create_shape(ifcopenshell.geom.settings(), f.by_type("IfcProduct")[0])
    print("create_shape ok:", type(s).__name__, flush=True)
    """
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--preimport", default="")
    args = ap.parse_args()
    preimport = [m for m in args.preimport.split(",") if m]

    results = {}
    for name, path in (("minimal_ifc4x3_add2", "MINIMAL"), ("curved_thick_plate", str(HERE / "curved_thick_plate.ifc"))):
        code = CHILD.format(preimport=preimport, path=path)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        status = "OK" if p.returncode == 0 else f"FAILED rc={p.returncode}" + (" (SIGSEGV)" if p.returncode in (-11, 139) else "")
        results[name] = status
        print(f"\n===== [{args.label}] {name}: {status}")
        print(p.stdout[-4000:])
        if p.returncode:
            print("--- stderr (tail) ---")
            print(p.stderr[-6000:])
    print(f"\n##### SUMMARY [{args.label}] {json.dumps(results)}")
    (HERE / f"summary-{args.label}.json").write_text(json.dumps(results))


if __name__ == "__main__":
    main()

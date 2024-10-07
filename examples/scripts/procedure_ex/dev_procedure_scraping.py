import pathlib

from ada.config import Config
from ada.procedural_modelling.procedure_store import ProcedureStore

PROCEDURE_EXAMPLE_DIR = (pathlib.Path(__file__).parent / "../../procedure_example").resolve().absolute()


def main():
    Config().update_config_globally("procedures_script_dir", PROCEDURE_EXAMPLE_DIR / "procedures")
    Config().update_config_globally("procedures_components_dir", PROCEDURE_EXAMPLE_DIR / "components")

    ps = ProcedureStore()
    ps.update_procedures()
    print(ps.procedures)

    procedure = ps.get("add_stiffeners")
    print(procedure.name)  # Outputs: 'example'
    print(procedure.description)  # Outputs the docstring of 'main'
    print(procedure.params)  # Outputs: {'name': 'str', 'age': 'int'}

    dc_procedure = procedure.to_procedure_dc()
    print(dc_procedure)
    input_file = PROCEDURE_EXAMPLE_DIR / "server_temp/components/create_floor/create_floor-11271.ifc"
    if not input_file.exists():
        raise FileNotFoundError(f"{input_file} does not exist")

    output_file = input_file.with_name("MyBaseStructureWithStiffeners.ifc")
    procedure(
        input_file=input_file,
        output_file=output_file,
        hp_section="HP180x8",
        stiff_spacing=1.0
    )


if __name__ == "__main__":
    main()

import pathlib

from ada.procedural_modelling.procedure_store import ProcedureStore


def main():
    ps = ProcedureStore()
    ps.update_procedures()
    print(ps.procedures)

    procedure = ps.get("add_stiffeners")
    print(procedure.name)  # Outputs: 'example'
    print(procedure.description)  # Outputs the docstring of 'main'
    print(procedure.params)  # Outputs: {'name': 'str', 'age': 'int'}

    dc_procedure = procedure.to_procedure_dc()
    print(dc_procedure)
    input_file = pathlib.Path("server_temp/components/create_floor/create_floor-11271.ifc").resolve().absolute()
    output_file = input_file.with_name("MyBaseStructureWithStiffeners.ifc")
    procedure(
        input_file=input_file,
        output_file=output_file,
        hp_section="HP180x8",
        stiff_spacing=1.0
    )


if __name__ == "__main__":
    main()

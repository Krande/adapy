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

    procedure(ifc_file=pathlib.Path("temp/MyBaseStructure.ifc").resolve().absolute().as_posix())


if __name__ == "__main__":
    main()

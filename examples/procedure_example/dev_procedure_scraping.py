import pathlib

from ada.comms.procedures import ProcedureStore


def main():
    ps = ProcedureStore()
    print(ps.procedures)

    procedure = ps.get("add_stiffeners")
    print(procedure.name)  # Outputs: 'example'
    print(procedure.description)  # Outputs the docstring of 'main'
    print(procedure.params)  # Outputs: {'name': 'str', 'age': 'int'}
    print(procedure.return_type)  # Outputs: 'str'

    procedure(pathlib.Path("temp/MyBaseStructure.ifc").resolve().absolute().as_posix())


if __name__ == "__main__":
    main()

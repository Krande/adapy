from functools import wraps

from . import abaqus, calculix, code_aster, sesam, usfos

fem_exports = dict(
    abaqus=abaqus.to_fem, calculix=calculix.to_fem, code_aster=code_aster.to_fem, sesam=sesam.to_fem, usfos=usfos.to_fem
)
fem_imports = dict(
    abaqus=abaqus.read_fem,
    sesam=sesam.read_fem,
)
fem_executables = dict(abaqus=abaqus.run_abaqus, calculix=calculix.run_calculix, code_aster=code_aster.run_code_aster)


def femio(f):
    """

    TODO: Make this into a file-identifcation utility and allow for storing cached FEM representations in a HDF5 file.

    :param f:
    :return:
    """

    def interpret_fem(fem_ref):
        fem_type = None
        if ".fem" in str(fem_ref).lower():
            fem_type = "sesam"
        elif ".inp" in str(fem_ref).lower():
            fem_type = "abaqus"
        return fem_type

    @wraps(f)
    def convert_fem_wrapper(*args, **kwargs):
        from .io_meshio import meshio_read_fem, meshio_to_fem

        f_name = f.__name__
        if f_name == "read_fem":
            fem_map = fem_imports
        else:
            fem_map = fem_exports

        fem_format = args[2] if len(args) >= 3 else kwargs.get("fem_format", None)
        fem_converter = kwargs.get("fem_converter", "default")
        if fem_format is None:
            fem_file = args[1]
            fem_format = interpret_fem(fem_file)

        if fem_format not in fem_map.keys() and fem_converter == "default":
            raise Exception(
                f"Currently not supporting import of fem type '{kwargs['fem_format']}' "
                "using the default fem converter. You could try to import using the 'meshio' fem_converter."
            )

        if fem_converter == "default":
            kwargs.pop("fem_converter", None)
            kwargs["convert_func"] = fem_map[fem_format]
        elif fem_converter.lower() == "meshio":
            if f_name == "read_fem":
                kwargs["convert_func"] = meshio_read_fem
            else:  # f_name == 'to_fem'
                if "metadata" not in kwargs.keys():
                    kwargs["metadata"] = dict()
                kwargs["metadata"]["fem_format"] = fem_format
                kwargs["convert_func"] = meshio_to_fem
        else:
            raise ValueError(f'Unrecognized fem_converter "{fem_converter}". Only "meshio" and "default" are supported')

        f(*args, **kwargs)

    return convert_fem_wrapper

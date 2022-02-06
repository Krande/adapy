import logging

from ada.core.file_system import get_list_of_files


def read_using_ccx2paraview(file_ref, overwrite):
    try:
        from ccx2paraview import Converter
    except ModuleNotFoundError as e:
        logging.error(e)
        raise ModuleNotFoundError("ccx2paraview not found. In order to convert please install ccx2paraview first")

    result_files = get_list_of_files(file_ref.parent, ".vtu")

    if len(result_files) != 0 and overwrite is False:
        return result_files

    convert = Converter(str(file_ref), ["vtu"])
    convert.run()
    result_files = get_list_of_files(file_ref.parent, ".vtu")

    return result_files

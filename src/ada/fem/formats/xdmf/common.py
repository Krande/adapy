import numpy


def cell_data_from_raw(cells, cell_data_raw):
    cs = numpy.cumsum([len(c[1]) for c in cells])[:-1]
    return {name: numpy.split(d, cs) for name, d in cell_data_raw.items()}


def attribute_type(data):
    # <http://www.xdmf.org/index.php/XDMF_Model_and_Format#Attribute>
    if len(data.shape) == 1 or (len(data.shape) == 2 and data.shape[1] == 1):
        return "Scalar"
    elif len(data.shape) == 2 and data.shape[1] in [2, 3]:
        return "Vector"
    elif (len(data.shape) == 2 and data.shape[1] == 9) or (
        len(data.shape) == 3 and data.shape[1] == 3 and data.shape[2] == 3
    ):
        return "Tensor"
    elif len(data.shape) == 2 and data.shape[1] == 6:
        return "Tensor6"

    # if len(data.shape) != 3:
    #     raise ReadError()
    return "Matrix"


dtype_to_format_string = {
    "int32": "%d",
    "int64": "%d",
    "unit32": "%d",
    "uint64": "%d",
    "float32": "%.7e",
    "float64": "%.16e",
}


def raw_from_cell_data(cell_data):
    return {name: numpy.concatenate(value) for name, value in cell_data.items()}


class ReadError(Exception):
    pass


numpy_to_xdmf_dtype = {
    "int32": ("Int", "4"),
    "int64": ("Int", "8"),
    "uint32": ("UInt", "4"),
    "uint64": ("UInt", "8"),
    "float32": ("Float", "4"),
    "float64": ("Float", "8"),
}
xdmf_to_numpy_type = {v: k for k, v in numpy_to_xdmf_dtype.items()}

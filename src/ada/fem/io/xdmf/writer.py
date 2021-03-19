import os
import pathlib
from io import BytesIO
from xml.etree import ElementTree as ET

import numpy
from meshio.xdmf.common import meshio_to_xdmf_type, meshio_type_to_xdmf_index

from .common import (
    attribute_type,
    dtype_to_format_string,
    numpy_to_xdmf_dtype,
    raw_from_cell_data,
)


class XdmfWriter:
    """

    XDMF writer from Meshio (28.09.2020). Local copy for testing

    :param filename:
    :param data_format:
    :param compression:
    :param compression_opts:
    """

    def __init__(self, filename, data_format="HDF", compression="gzip", compression_opts=4):

        if data_format not in ["XML", "Binary", "HDF"]:
            raise ValueError("Unknown XDMF data format " f"'{data_format}' (use 'XML', 'Binary', or 'HDF'.)")

        self.filename = pathlib.Path(filename).with_suffix(".xdmf")
        os.makedirs(self.filename.parent, exist_ok=True)
        self.data_format = data_format
        self.data_counter = 0
        self.compression = compression
        self.compression_opts = None if compression is None else compression_opts

        if data_format == "HDF":
            import h5py

            self.h5_filename = os.path.splitext(self.filename)[0] + ".h5"
            self.h5_file = h5py.File(self.h5_filename, "w")

        self.xdmf_file = ET.Element("Xdmf", Version="3.0")

        domain = ET.SubElement(self.xdmf_file, "Domain")
        self.grid = ET.SubElement(domain, "Grid", Name="Grid")

    def write(self):
        ET.register_namespace("xi", "https://www.w3.org/2001/XInclude/")
        tree = ET.ElementTree(self.xdmf_file)
        tree.write(self.filename)

    def add_points(self, points):
        if points.shape[1] == 1:
            geometry_type = "X"
        elif points.shape[1] == 2:
            geometry_type = "XY"
        else:
            if points.shape[1] != 3:
                raise ValueError()
            geometry_type = "XYZ"

        geo = ET.SubElement(self.grid, "Geometry", GeometryType=geometry_type)
        dt, prec = numpy_to_xdmf_dtype[points.dtype.name]
        dim = "{} {}".format(*points.shape)
        data_item = ET.SubElement(
            geo,
            "DataItem",
            DataType=dt,
            Dimensions=dim,
            Format=self.data_format,
            Precision=prec,
        )
        data_item.text = self.numpy_to_xml_string(points)

    def add_cells(self, cells):
        import collections

        CellBlock = collections.namedtuple("CellBlock", ["type", "data"])
        if isinstance(cells, dict):
            cells = [CellBlock(cell_type, data) for cell_type, data in cells.items()]
        else:
            cells = [CellBlock(cell_type, data) for cell_type, data in cells]

        if len(cells) == 1:
            meshio_type = cells[0].type
            num_cells = len(cells[0].data)
            xdmf_type = meshio_to_xdmf_type[meshio_type][0]
            topo = ET.SubElement(
                self.grid,
                "Topology",
                TopologyType=xdmf_type,
                NumberOfElements=str(num_cells),
                NodesPerElement=str(cells[0].data.shape[1]),
            )
            dt, prec = numpy_to_xdmf_dtype[cells[0].data.dtype.name]
            dim = "{} {}".format(*cells[0].data.shape)
            data_item = ET.SubElement(
                topo,
                "DataItem",
                DataType=dt,
                Dimensions=dim,
                Format=self.data_format,
                Precision=prec,
            )
            data_item.text = self.numpy_to_xml_string(cells[0].data)

        else:
            assert len(cells) > 1
            total_num_cells = sum(c.data.shape[0] for c in cells)
            topo = ET.SubElement(
                self.grid,
                "Topology",
                TopologyType="Mixed",
                NumberOfElements=str(total_num_cells),
            )
            total_num_cell_items = sum(numpy.prod(c.data.shape) for c in cells)
            num_vertices_and_lines = sum(c.data.shape[0] for c in cells if c.type in {"vertex", "line"})
            dim = str(total_num_cell_items + total_num_cells + num_vertices_and_lines)
            cd = numpy.concatenate(
                [
                    numpy.hstack(
                        [
                            numpy.full(
                                (value.shape[0], 2 if key in {"vertex", "line"} else 1),
                                meshio_type_to_xdmf_index[key],
                            ),
                            value,
                        ]
                    ).flatten()
                    for key, value in cells
                ]
            )
            dt, prec = numpy_to_xdmf_dtype[cd.dtype.name]
            data_item = ET.SubElement(
                topo,
                "DataItem",
                DataType=dt,
                Dimensions=dim,
                Format=self.data_format,
                Precision=prec,
            )
            data_item.text = self.numpy_to_xml_string(cd)

    def add_point_data(self, point_data):
        for name, data in point_data.items():
            att = ET.SubElement(
                self.grid,
                "Attribute",
                Name=name,
                AttributeType=attribute_type(data),
                Center="Node",
            )
            dt, prec = numpy_to_xdmf_dtype[data.dtype.name]
            dim = " ".join([str(s) for s in data.shape])
            data_item = ET.SubElement(
                att,
                "DataItem",
                DataType=dt,
                Dimensions=dim,
                Format=self.data_format,
                Precision=prec,
            )
            data_item.text = self.numpy_to_xml_string(data)

    def add_cell_data(self, cell_data):
        raw = raw_from_cell_data(cell_data)
        for name, data in raw.items():
            att = ET.SubElement(
                self.grid,
                "Attribute",
                Name=name,
                AttributeType=attribute_type(data),
                Center="Cell",
            )
            dt, prec = numpy_to_xdmf_dtype[data.dtype.name]
            dim = " ".join([str(s) for s in data.shape])
            data_item = ET.SubElement(
                att,
                "DataItem",
                DataType=dt,
                Dimensions=dim,
                Format=self.data_format,
                Precision=prec,
            )
            data_item.text = self.numpy_to_xml_string(data)

    def numpy_to_xml_string(self, data):
        if self.data_format == "XML":
            s = BytesIO()
            fmt = dtype_to_format_string[data.dtype.name]
            numpy.savetxt(s, data, fmt)
            return "\n" + s.getvalue().decode()
        elif self.data_format == "Binary":
            base = os.path.splitext(self.filename)[0]
            bin_filename = f"{base}{self.data_counter}.bin"
            self.data_counter += 1
            # write binary data to file
            with open(bin_filename, "wb") as f:
                data.tofile(f)
            return bin_filename

        if self.data_format != "HDF":
            raise ValueError(f'Unknown data format "{self.data_format}"')
        name = f"data{self.data_counter}"
        self.data_counter += 1
        self.h5_file.create_dataset(
            name,
            data=data,
            compression=self.compression,
            compression_opts=self.compression_opts,
        )
        return os.path.basename(self.h5_filename) + ":/" + name

    # The original idea was to implement field data as XML CDATA. Unfortunately, in
    # Python's XML, CDATA handled poorly. There are workarounds, e.g.,
    # <https://stackoverflow.com/a/30019607/353337>, but those mess around with
    # ET._serialize_xml and lead to bugs elsewhere
    # <https://github.com/nschloe/meshio/issues/806>.
    # def field_data(self, field_data, information):
    #     info = ET.Element("main")
    #     for name, data in field_data.items():
    #         data_item = ET.SubElement(info, "map", key=name, dim=str(data[1]))
    #         data_item.text = str(data[0])
    #     information.text = ET.CDATA(ET.tostring(info))
    #     information.append(CDATA(ET.tostring(info).decode("utf-8")))

import pathlib
from xml.etree import ElementTree as ET

import numpy
from meshio.xdmf.common import CellBlock, translate_mixed_cells, xdmf_to_meshio_type

from .common import ReadError, cell_data_from_raw, xdmf_to_numpy_type


class XdmfReader:
    def __init__(self, filename):
        self.filename = filename

    def read(self):
        parser = ET.XMLParser()
        tree = ET.parse(self.filename, parser)
        root = tree.getroot()

        if root.tag != "Xdmf":
            raise ReadError()

        version = root.get("Version")

        if version.split(".")[0] != "3":
            raise ReadError(f"Unknown XDMF version {version}.")

        return self.read_xdmf3(root)

    def _read_data_item(self, data_item, root=None):
        import h5py

        reference = data_item.get("Reference")
        if reference:
            xpath = (data_item.text if reference == "XML" else reference).strip()
            if xpath.startswith("/"):
                return self._read_data_item(root.find(".//" + "/".join(xpath.split("/")[2:])), root)
            raise ValueError(f"Can't read XPath {xpath}.")

        dims = [int(d) for d in data_item.get("Dimensions").split()]

        # Actually, `NumberType` is XDMF2 and `DataType` XDMF3, but many files
        # out there use both keys interchangeably.
        if data_item.get("DataType"):
            if data_item.get("NumberType"):
                raise ReadError()
            data_type = data_item.get("DataType")
        elif data_item.get("NumberType"):
            if data_item.get("DataType"):
                raise ReadError()
            data_type = data_item.get("NumberType")
        else:
            # Default, see
            # <http://www.xdmf.org/index.php/XDMF_Model_and_Format#XML_Element_.28Xdmf_ClassName.29_and_Default_XML_Attributes>
            data_type = "Float"

        try:
            precision = data_item.attrib["Precision"]
        except KeyError:
            precision = "4"

        if data_item.get("Format") == "XML":
            return numpy.fromstring(
                data_item.text,
                dtype=xdmf_to_numpy_type[(data_type, precision)],
                sep=" ",
            ).reshape(dims)
        elif data_item.get("Format") == "Binary":
            return numpy.fromfile(data_item.text.strip(), dtype=xdmf_to_numpy_type[(data_type, precision)]).reshape(
                dims
            )
        elif data_item.get("Format") != "HDF":
            fmt = data_item.get("Format")
            raise ReadError(f"Unknown XDMF Format '{fmt}'.")

        info = data_item.text.strip()
        filename, h5path = info.split(":")

        # The HDF5 file path is given with respect to the XDMF (XML) file.
        dirname = pathlib.Path(self.filename).resolve().parent
        full_hdf5_path = dirname / filename

        f = h5py.File(full_hdf5_path, "r")

        # Some files don't contain the leading slash /.
        if h5path[0] == "/":
            h5path = h5path[1:]

        for key in h5path.split("/"):
            f = f[key]
        # `[()]` gives a numpy.ndarray
        return f[()]

    def read_information(self, c_data):
        field_data = {}
        root = ET.fromstring(c_data)
        for child in root:
            str_tag = child.get("key")
            dim = int(child.get("dim"))
            num_tag = int(child.text)
            field_data[str_tag] = numpy.array([num_tag, dim])
        return field_data

    def read_xdmf3(self, root):  # noqa: C901
        domains = list(root)
        if len(domains) != 1:
            raise ReadError()
        domain = domains[0]
        if domain.tag != "Domain":
            raise ReadError()

        grids = list(domain)
        if len(grids) != 1:
            raise ReadError("XDMF reader: Only supports one grid right now.")
        grid = grids[0]
        if grid.tag != "Grid":
            raise ReadError()

        points = None
        cells = []
        point_data = {}
        cell_data_raw = {}
        field_data = {}

        for c in grid:
            if c.tag == "Topology":
                data_items = list(c)
                if len(data_items) != 1:
                    raise ReadError()
                data_item = data_items[0]

                data = self._read_data_item(data_item)

                # The XDMF2 key is `TopologyType`, just `Type` for XDMF3.
                # Allow both.
                if c.get("Type"):
                    if c.get("TopologyType"):
                        raise ReadError()
                    cell_type = c.get("Type")
                else:
                    cell_type = c.get("TopologyType")

                if cell_type == "Mixed":
                    cells = translate_mixed_cells(data)
                else:
                    cells.append(CellBlock(xdmf_to_meshio_type[cell_type], data))

            elif c.tag == "Geometry":
                if c.get("Type"):
                    if c.get("GeometryType"):
                        raise ReadError()
                    geometry_type = c.get("Type")
                else:
                    geometry_type = c.get("GeometryType")

                if geometry_type not in ["XY", "XYZ"]:
                    raise ReadError(f'Illegal geometry type "{geometry_type}".')

                data_items = list(c)
                if len(data_items) != 1:
                    raise ReadError()
                data_item = data_items[0]
                points = self._read_data_item(data_item)

            elif c.tag == "Information":
                c_data = c.text
                if not c_data:
                    raise ReadError()
                field_data = self.read_information(c_data)

            elif c.tag == "Attribute":
                # Don't be too strict here: FEniCS, for example, calls this
                # 'AttributeType'.
                # assert c.attrib['Type'] == 'None'

                data_items = list(c)
                if len(data_items) != 1:
                    raise ReadError()
                data_item = data_items[0]

                data = self._read_data_item(data_item)

                name = c.get("Name")
                if c.get("Center") == "Node":
                    point_data[name] = data
                else:
                    if c.get("Center") != "Cell":
                        raise ReadError()
                    cell_data_raw[name] = data
            else:
                raise ReadError(f"Unknown section '{c.tag}'.")

        cell_data = cell_data_from_raw(cells, cell_data_raw)
        from meshio import Mesh

        return Mesh(
            points,
            cells,
            point_data=point_data,
            cell_data=cell_data,
            field_data=field_data,
        )

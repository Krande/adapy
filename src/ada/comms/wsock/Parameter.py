# automatically generated by the FlatBuffers compiler, do not modify

# namespace: wsock

import flatbuffers
from flatbuffers.compat import import_numpy
np = import_numpy()

class Parameter(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAs(cls, buf, offset=0):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = Parameter()
        x.Init(buf, n + offset)
        return x

    @classmethod
    def GetRootAsParameter(cls, buf, offset=0):
        """This method is deprecated. Please switch to GetRootAs."""
        return cls.GetRootAs(buf, offset)
    # Parameter
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)

    # Parameter
    def Name(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return self._tab.String(o + self._tab.Pos)
        return None

    # Parameter
    def Type(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(6))
        if o != 0:
            return self._tab.String(o + self._tab.Pos)
        return None

    # Parameter
    def Value(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(8))
        if o != 0:
            return self._tab.String(o + self._tab.Pos)
        return None

def ParameterStart(builder):
    builder.StartObject(3)

def Start(builder):
    ParameterStart(builder)

def ParameterAddName(builder, name):
    builder.PrependUOffsetTRelativeSlot(0, flatbuffers.number_types.UOffsetTFlags.py_type(name), 0)

def AddName(builder, name):
    ParameterAddName(builder, name)

def ParameterAddType(builder, type):
    builder.PrependUOffsetTRelativeSlot(1, flatbuffers.number_types.UOffsetTFlags.py_type(type), 0)

def AddType(builder, type):
    ParameterAddType(builder, type)

def ParameterAddValue(builder, value):
    builder.PrependUOffsetTRelativeSlot(2, flatbuffers.number_types.UOffsetTFlags.py_type(value), 0)

def AddValue(builder, value):
    ParameterAddValue(builder, value)

def ParameterEnd(builder):
    return builder.EndObject()

def End(builder):
    return ParameterEnd(builder)
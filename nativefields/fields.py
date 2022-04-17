from copy import deepcopy
import struct
from typing import Iterable, Tuple
import numpy as np
from nativefields.base import NativeStruct, NativeField


class StructField(NativeField):
    def __init__(self, offset: Tuple[NativeField, int], struct_type: type, **kwargs):
        assert issubclass(struct_type, NativeStruct), 'struct_type must be an inheritant of type NativeStruct'
        self.offset = offset
        self.struct_type = struct_type
        self.size = struct_type.min_size
        self.is_instance = True
        self.inner = None
        super().__init__(**kwargs)

    def _getvalue(self, native_struct: NativeStruct):
        if not self.inner:
            inner = self.struct_type(master_offset=self.offset)
            self._setvalue(native_struct, inner)

        return self.inner

    def _setvalue(self, native_struct: NativeStruct, value):
        old_size = self.size
        self.size = value.size
        if self.size != old_size:
            native_struct._resize_data(self, old_size)

        offset = native_struct._calc_offset(self)
        native_struct.data[offset:offset + self.size] = value.data[:]
        self.inner = deepcopy(value)
        self.inner.data = native_struct.data
        self.inner.master_offset = self.offset


class SimpleField(NativeField):
    def __init__(self, offset: Tuple[NativeField, int], struct_format: str, **kwargs):
        self.offset = offset
        self.format = struct_format
        self.size = struct.calcsize(struct_format)
        super().__init__(**kwargs)

    def _getvalue(self, native_struct: NativeStruct):
        offset = native_struct._calc_offset(self)
        if offset + self.size > len(native_struct.data):
            raise Exception('Failed to get value: field is out of bounds')

        return struct.unpack(self.format, native_struct.data[offset:offset + self.size])[0]

    def _setvalue(self, native_struct: NativeStruct, value):
        offset = native_struct._calc_offset(self)
        if offset + self.size > len(native_struct.data):
            raise Exception('Failed to set value: field is out of bounds')

        native_struct.data[offset:offset + self.size] = struct.pack(self.format, value)


class ByteArrayField(NativeField):
    def __init__(self, offset: Tuple[NativeField, int], length: int, **kwargs):
        self.offset = offset
        if length is None:
            self.size = 0
            self.is_instance = True
        else:
            self.size = length

        super().__init__(**kwargs)

    def resize(self, native_struct: NativeStruct, length: int):
        old_size = self.size
        self.size = length

        native_struct._resize_data(self, old_size)

    def _getvalue(self, native_struct: NativeStruct):
        offset = native_struct._calc_offset(self)
        if offset + self.size > len(native_struct.data):
            raise Exception('Failed to get value: field is out of bounds')

        return native_struct.data[offset:offset + self.size]

    def _setvalue(self, native_struct: NativeStruct, value):
        new_length = len(value)
        if self.is_instance and new_length != self.size:
            self.resize(native_struct, new_length)
        else:
            assert new_length == self.size, f'bytearray size {new_length} not matching, should be {self.size}'

        offset = native_struct._calc_offset(self)
        if offset + self.size > len(native_struct.data):
            raise Exception(
                f'Failed to set value: field at offset {offset} and size {self.size} '
                f'is out of bounds for struct with size {len(native_struct.data)}'
            )

        native_struct.data[offset:offset + self.size] = value


class IntegerField(SimpleField):
    def __init__(self, offset: Tuple[NativeField, int], signed: bool = True, size: int = 4, **kwargs):
        if size == 4:
            super().__init__(offset, 'i' if signed else 'I', **kwargs)
        elif size == 2:
            super().__init__(offset, 'h' if signed else 'H', **kwargs)
        elif size == 1:
            super().__init__(offset, 'b' if signed else 'B', **kwargs)
        else:
            raise Exception('size has to be either 4, 2 or 1')


class DoubleField(SimpleField):
    def __init__(self, offset: Tuple[NativeField, int], **kwargs):
        super().__init__(offset, 'd', **kwargs)


class FloatField(SimpleField):
    def __init__(self, offset: Tuple[NativeField, int], **kwargs):
        super().__init__(offset, 'f', **kwargs)


class BooleanField(IntegerField):
    def __init__(self, offset: Tuple[NativeField, int], **kwargs):
        super().__init__(offset, signed=False, **kwargs)

    def _getvalue(self, native_struct: NativeStruct) -> bool:
        return bool(super()._getvalue(native_struct))

    def _setvalue(self, native_struct: NativeStruct, value: bool):
        return super()._setvalue(native_struct, int(value))


class StringField(SimpleField):
    def __init__(self, offset: Tuple[NativeField, int], length: int, encoding='utf-8', **kwargs):
        self.encoding = encoding
        if length is None:
            self.is_instance = True
            super().__init__(offset, '0s', **kwargs)
        else:
            super().__init__(offset, f'{length}s', **kwargs)

    def resize(self, native_struct: NativeStruct, length: int):
        old_size = self.size
        self.size = length
        self.format = f'{self.size}s'

        native_struct._resize_data(self, old_size)

    def _getvalue(self, native_struct: NativeStruct) -> str:
        return super()._getvalue(native_struct).decode(self.encoding)

    def _setvalue(self, native_struct: NativeStruct, value: str):
        new_length = len(value)
        if self.is_instance and new_length != self.size:
            self.resize(native_struct, new_length)

        return super()._setvalue(native_struct, value.encode(self.encoding))


class ArrayField(NativeField):
    def __init__(
        self,
        offset: Tuple[NativeField, int],
        shape: Tuple[tuple, int],
        elem_field_type: type,
        **kwargs
    ):
        self.offset = offset

        if issubclass(elem_field_type, NativeField):
            self._elem_field = elem_field_type(0, **kwargs)
        else:
            self._elem_field = StructField(0, elem_field_type)

        if shape:
            self.shape = (shape,) if isinstance(shape, int) else shape[:]
        else:
            self.shape = (0,)
            self.is_instance = True

        self._update_size()

    def resize(self, native_struct: NativeStruct, shape: Tuple[tuple, int]):
        old_size = self.size
        self.shape = (shape,) if isinstance(shape, int) else shape[:]

        self._update_size()
        native_struct._resize_data(self, old_size)

    def _update_size(self):
        self.size = self._elem_field.size * int(np.prod(self.shape))

    @staticmethod
    def get_array_index(shape: tuple, index: tuple) -> int:
        assert shape, 'shape cannot be an empty list'
        assert len(shape) == len(index), 'shape and index need to be the same length'
        return sum([index[i] * int(np.prod(shape[i + 1:])) for i in range(len(index) - 1)]) + index[-1]

    def _getvalue(self, native_struct: NativeStruct) -> np.array:
        arr = np.empty(self.shape, dtype=object)

        for index in np.ndindex(self.shape):
            self._elem_field.offset = (
                self.real_offset + ArrayField.get_array_index(self.shape, index) * self._elem_field.size
            )

            arr[index] = self._elem_field._getvalue(native_struct)

        return arr

    def _setvalue(self, native_struct: NativeStruct, value: Iterable):
        if isinstance(value, np.ndarray):
            assert value.shape == self.shape, f'array shape {value.shape} not matching, should be {self.shape}'

        value_shape = np.array(value).shape
        if self.is_instance and value_shape != self.shape:
            self.resize(native_struct, value_shape)

        arr = np.empty(self.shape, dtype=object)
        arr[:] = value

        for index in np.ndindex(self.shape):
            self._elem_field.offset = (
                self.real_offset + ArrayField.get_array_index(self.shape, index) * self._elem_field.size
            )

            self._elem_field._setvalue(native_struct, arr[index])


class VariableField(NativeField):
    def __init__(self, offset: Tuple[NativeField, int], **kwargs):
        self.offset = offset
        self.size = 0
        self.child = None
        self.is_instance = True

    def resize(self, native_struct: NativeStruct, child: NativeField):
        old_size = self.size
        self.size = child.size
        self.child = child
        self.child.offset = self.offset

        native_struct._resize_data(self, old_size)

    def _getvalue(self, native_struct: NativeStruct):
        if not self.child:
            raise Exception('VariableField does not contain any field')

        return self.child._getvalue(native_struct)

    def _setvalue(self, native_struct: NativeStruct, value):
        if not self.child:
            raise Exception('VariableField does not contain any field')

        val = self.child._setvalue(native_struct, value)
        self.size = self.child.size
        return val


def unpack_bytes(data: Tuple[bytearray, NativeStruct], field: NativeField):
    if isinstance(data, NativeStruct):
        return field._getvalue(data)

    elem = NativeStruct(data)
    return field._getvalue(elem)


def pack_value(value, field: NativeField):
    elem = NativeStruct(bytearray(field.real_offset + field.size))
    field._setvalue(elem, value)
    return elem
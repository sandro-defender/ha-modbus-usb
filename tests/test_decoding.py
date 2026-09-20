"""Unit tests for pure decoding and normalization helpers."""

from __future__ import annotations

import struct

from custom_components.modbus_usb.decoding import (
    as_float,
    decode_words,
    normalize_enum,
)


def test_normalize_enum_treats_none_strings_and_invalid_values_as_none() -> None:
    from homeassistant.components.sensor import SensorDeviceClass

    assert normalize_enum("none", SensorDeviceClass, "x") is None
    assert normalize_enum(None, SensorDeviceClass, "x") is None
    assert normalize_enum("", SensorDeviceClass, "x") is None
    assert normalize_enum("not-a-class", SensorDeviceClass, "x") is None
    assert (
        normalize_enum("temperature", SensorDeviceClass, "x")
        is SensorDeviceClass.TEMPERATURE
    )


def test_as_float_falls_back_for_null_and_garbage() -> None:
    assert as_float(None, 5) == 5
    assert as_float("", 5) == 5
    assert as_float("12.5", 5) == 12.5
    assert as_float(3, 5) == 3


def test_decode_words_16bit() -> None:
    assert decode_words([42], "uint16") == 42
    assert decode_words([0xFFFF], "uint16") == 0xFFFF
    assert decode_words([42], "int16") == 42
    assert decode_words([0xFFFF], "int16") == -1
    assert decode_words([0x8000], "int16") == -32768


def test_decode_words_32bit_big_endian() -> None:
    assert decode_words([0x0001, 0x0000], "uint32") == 0x10000
    assert decode_words([0xFFFF, 0xFFFF], "uint32") == 0xFFFFFFFF
    assert decode_words([0xFFFF, 0xFFFE], "int32") == -2


def test_decode_words_float32() -> None:
    high, low = struct.unpack(">HH", struct.pack(">f", 23.5))
    assert decode_words([high, low], "float32") == 23.5


def test_decode_words_unknown_type_returns_first_word() -> None:
    assert decode_words([7, 8], "bool") == 7

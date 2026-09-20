"""Unit tests for board protocol detection and reader selection."""

from __future__ import annotations

from custom_components.modbus_usb.boards import PROTOCOL_R4D6F20, select_block_reader
from custom_components.modbus_usb.boards.r4d6f20 import (
    read_command1_blocks,
    read_command2_blocks,
)
from custom_components.modbus_usb.boards.r413e16 import (
    R413E16_OFF_VALUE,
    R413E16_ON_VALUE,
    is_r413e16_switch_config,
)


def test_r413e16_command_values_match_verified_map() -> None:
    assert R413E16_ON_VALUE == 0x0100
    assert R413E16_OFF_VALUE == 0x0200


def test_is_r413e16_switch_config_detects_verified_map() -> None:
    assert (
        is_r413e16_switch_config(
            {"register_type": "holding", "on_value": 256, "off_value": 512}
        )
        is True
    )
    # Older panel versions could persist numbers as JSON strings.
    assert (
        is_r413e16_switch_config(
            {"register_type": "holding", "on_value": "256", "off_value": "512"}
        )
        is True
    )


def test_is_r413e16_switch_config_rejects_other_maps() -> None:
    assert (
        is_r413e16_switch_config(
            {"register_type": "coil", "on_value": 256, "off_value": 512}
        )
        is False
    )
    assert (
        is_r413e16_switch_config(
            {"register_type": "holding", "on_value": 1, "off_value": 0}
        )
        is False
    )
    assert is_r413e16_switch_config({}) is False
    assert (
        is_r413e16_switch_config(
            {"register_type": "holding", "on_value": "on", "off_value": "off"}
        )
        is False
    )


def test_select_block_reader_r4d6f20_by_protocol() -> None:
    device = {"device_controls": {"protocol": PROTOCOL_R4D6F20}}
    assert select_block_reader(device) is read_command1_blocks


def test_select_block_reader_r4d6f20_command2_when_m0_shorted() -> None:
    device = {"device_controls": {"protocol": PROTOCOL_R4D6F20}, "m0_short": True}
    assert select_block_reader(device) is read_command2_blocks


def test_select_block_reader_falls_back_to_model_name() -> None:
    device = {"model": "Eletechsup R4D6F20 Multifunction Relay Board"}
    assert select_block_reader(device) is read_command1_blocks


def test_select_block_reader_returns_none_for_generic_devices() -> None:
    assert select_block_reader({}) is None
    assert select_block_reader({"model": "XY-MD02"}) is None
    assert (
        select_block_reader({"device_controls": {"protocol": "unknown_board"}}) is None
    )

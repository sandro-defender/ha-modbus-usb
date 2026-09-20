"""One config entry must own a serial adapter, regardless of slave ID."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.data_entry_flow import AbortFlow

from custom_components.modbus_usb.config_flow import ModbusUsbConfigFlow


async def test_duplicate_port_with_different_slave_is_rejected():
    flow = ModbusUsbConfigFlow()
    flow._async_current_entries = Mock(return_value=[
        SimpleNamespace(data={"port": "/dev/ttyUSB0", "slave_id": 1}, options={}),
    ])
    with pytest.raises(AbortFlow) as error:
        await flow.async_step_user({"port": "/dev/ttyUSB0", "slave_id": 2})
    assert error.value.reason == "already_configured"

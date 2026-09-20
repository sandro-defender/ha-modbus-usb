"""Unit tests for multi-register block read optimizer and circuit breaker."""

from __future__ import annotations

import pytest

from custom_components.modbus_usb.circuit_breaker import (
    STATE_DEGRADED,
    STATE_HEALTHY,
    STATE_OFFLINE,
    SlaveCircuitBreaker,
)
from custom_components.modbus_usb.optimizer import (
    group_entities_into_blocks,
)

pytestmark = pytest.mark.fast


def test_group_contiguous_registers() -> None:
    entities = [
        {"id": "e1", "register_type": "holding", "address": 0, "data_type": "uint16"},
        {"id": "e2", "register_type": "holding", "address": 1, "data_type": "uint16"},
        {"id": "e3", "register_type": "holding", "address": 2, "data_type": "uint16"},
    ]
    blocks, unhandled = group_entities_into_blocks(
        entities, max_read_registers=64, max_gap_tolerance=2
    )
    assert not unhandled
    assert len(blocks) == 1
    assert blocks[0].register_type == "holding"
    assert blocks[0].start_address == 0
    assert blocks[0].count == 3
    assert len(blocks[0].spans) == 3


def test_group_registers_with_tolerated_gap() -> None:
    # Addresses 0 and 2 (gap of 1 unused register at address 1)
    entities = [
        {"id": "e1", "register_type": "input", "address": 0, "data_type": "uint16"},
        {"id": "e2", "register_type": "input", "address": 2, "data_type": "uint16"},
    ]
    blocks, unhandled = group_entities_into_blocks(
        entities, max_read_registers=64, max_gap_tolerance=2
    )
    assert not unhandled
    assert len(blocks) == 1
    assert blocks[0].start_address == 0
    assert blocks[0].count == 3  # covers 0, 1, 2


def test_split_registers_when_gap_exceeds_tolerance() -> None:
    # Addresses 0 and 10 (gap of 9, exceeds tolerance 2)
    entities = [
        {"id": "e1", "register_type": "holding", "address": 0, "data_type": "uint16"},
        {"id": "e2", "register_type": "holding", "address": 10, "data_type": "uint16"},
    ]
    blocks, unhandled = group_entities_into_blocks(
        entities, max_read_registers=64, max_gap_tolerance=2
    )
    assert len(blocks) == 2
    assert blocks[0].start_address == 0
    assert blocks[0].count == 1
    assert blocks[1].start_address == 10
    assert blocks[1].count == 1


def test_split_registers_when_max_limit_exceeded() -> None:
    # 65 registers with max 64
    entities = [
        {
            "id": f"e_{i}",
            "register_type": "holding",
            "address": i,
            "data_type": "uint16",
        }
        for i in range(65)
    ]
    blocks, _ = group_entities_into_blocks(
        entities, max_read_registers=64, max_gap_tolerance=2
    )
    assert len(blocks) == 2
    assert blocks[0].count == 64
    assert blocks[1].count == 1


def test_circuit_breaker_state_transitions() -> None:
    breaker = SlaveCircuitBreaker(
        degraded_threshold=2,
        offline_threshold=4,
        initial_backoff=10.0,
        max_backoff=60.0,
    )
    slave_id = 5
    assert breaker.should_poll(slave_id) is True
    assert breaker.get_state(slave_id).state == STATE_HEALTHY

    # 1 failure -> still healthy
    breaker.record_failure(slave_id, "Timeout 1")
    assert breaker.get_state(slave_id).state == STATE_HEALTHY

    # 2 failures -> DEGRADED
    breaker.record_failure(slave_id, "Timeout 2")
    assert breaker.get_state(slave_id).state == STATE_DEGRADED
    assert breaker.should_poll(slave_id) is False

    # 4 failures -> OFFLINE
    breaker.record_failure(slave_id, "Timeout 3")
    breaker.record_failure(slave_id, "Timeout 4")
    assert breaker.get_state(slave_id).state == STATE_OFFLINE
    assert breaker.should_poll(slave_id) is False

    # Success recovers to HEALTHY immediately
    breaker.record_success(slave_id)
    assert breaker.get_state(slave_id).state == STATE_HEALTHY
    assert breaker.should_poll(slave_id) is True
    assert breaker.get_state(slave_id).consecutive_failures == 0


def test_coordinator_packed_block_read() -> None:
    """Verify coordinator packs contiguous holding registers into a single frame."""
    from unittest.mock import Mock

    from custom_components.modbus_usb.coordinator import ModbusUsbCoordinator

    class MockResponse:
        def __init__(self, registers):
            self.registers = registers

        def isError(self):
            return False

    client = Mock()
    client.connected = True
    client.read_holding_registers.return_value = MockResponse([100, 200, 300])

    coordinator = object.__new__(ModbusUsbCoordinator)
    coordinator.client = client
    coordinator.slave_id = 1
    coordinator.entry_id = "test-entry"
    from collections import deque
    from threading import Lock

    coordinator._serial_lock = Lock()
    coordinator.transaction_log = deque(maxlen=200)
    from custom_components.modbus_usb.const import (
        DIAG_CONSECUTIVE_FAILURES,
        DIAG_FAILED_READS,
        DIAG_LAST_ERROR,
        DIAG_LAST_SUCCESS,
        DIAG_TOTAL_READS,
    )

    coordinator.diag = {
        DIAG_TOTAL_READS: 0,
        DIAG_FAILED_READS: 0,
        DIAG_CONSECUTIVE_FAILURES: 0,
        DIAG_LAST_ERROR: None,
        DIAG_LAST_SUCCESS: None,
    }
    coordinator.circuit_breaker = SlaveCircuitBreaker()
    coordinator._entity_last_poll = {}
    coordinator.hass = None

    entities = [
        {
            "id": "e1",
            "name": "R0",
            "register_type": "holding",
            "address": 0,
            "data_type": "uint16",
        },
        {
            "id": "e2",
            "name": "R1",
            "register_type": "holding",
            "address": 1,
            "data_type": "uint16",
        },
        {
            "id": "e3",
            "name": "R2",
            "register_type": "holding",
            "address": 2,
            "data_type": "uint16",
        },
    ]

    data = coordinator._read_all(entities)
    assert data["e1"] == 100
    assert data["e2"] == 200
    assert data["e3"] == 300
    assert client.read_holding_registers.call_count == 1
    call_args = client.read_holding_registers.call_args
    # address 0, count 3
    assert call_args[0][0] == 0
    assert call_args[1]["count"] == 3

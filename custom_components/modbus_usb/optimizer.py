"""Multi-register block read optimizer and register packing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    DATA_TYPE_UINT16,
    DATA_TYPE_WORD_COUNT,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
)


@dataclass(frozen=True)
class EntityRegisterSpan:
    entity: dict[str, Any]
    entity_id: str
    register_type: str
    start_address: int
    count: int
    data_type: str
    scale: float


@dataclass
class RegisterBlock:
    register_type: str
    start_address: int
    count: int
    spans: list[EntityRegisterSpan]


def group_entities_into_blocks(
    entities: list[dict[str, Any]],
    *,
    max_read_registers: int = 64,
    max_gap_tolerance: int = 2,
) -> tuple[list[RegisterBlock], list[dict[str, Any]]]:
    """Auto-pack adjacent or contiguous register reads into single Modbus blocks.

    - Supports FC01 (coils), FC02 (discrete inputs), FC03 (holding registers), FC04 (input registers).
    - Respects max_read_registers per frame.
    - Bridges gaps <= max_gap_tolerance unused registers if within limit.
    - Single-register or un-groupable entities are handled safely.
    """
    # Filter entities that have valid address and register_type
    eligible: dict[str, list[EntityRegisterSpan]] = {
        REGISTER_TYPE_HOLDING: [],
        REGISTER_TYPE_INPUT: [],
        REGISTER_TYPE_COIL: [],
        REGISTER_TYPE_DISCRETE: [],
    }
    unhandled: list[dict[str, Any]] = []

    for ent in entities:
        rtype = ent.get(CONF_REGISTER_TYPE)
        addr = ent.get(CONF_ADDRESS)
        ent_id = ent.get("id")
        if rtype not in eligible or addr is None or not ent_id:
            unhandled.append(ent)
            continue

        try:
            start_addr = int(addr)
        except (ValueError, TypeError):
            unhandled.append(ent)
            continue

        data_type = ent.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
        # Word count: 1 for coils/discrete, or DATA_TYPE_WORD_COUNT for registers
        if rtype in (REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE):
            count = 1
        else:
            count = DATA_TYPE_WORD_COUNT.get(data_type, 1)

        scale = ent.get(CONF_SCALE, 1)
        try:
            scale_val = float(scale) if scale is not None else 1.0
        except (ValueError, TypeError):
            scale_val = 1.0

        span = EntityRegisterSpan(
            entity=ent,
            entity_id=ent_id,
            register_type=rtype,
            start_address=start_addr,
            count=count,
            data_type=data_type,
            scale=scale_val,
        )
        eligible[rtype].append(span)

    blocks: list[RegisterBlock] = []

    for rtype, spans in eligible.items():
        if not spans:
            continue
        # Sort by start address
        sorted_spans = sorted(spans, key=lambda s: s.start_address)

        current_block: RegisterBlock | None = None
        for span in sorted_spans:
            if current_block is None:
                current_block = RegisterBlock(
                    register_type=rtype,
                    start_address=span.start_address,
                    count=span.count,
                    spans=[span],
                )
                continue

            current_end = current_block.start_address + current_block.count
            gap = span.start_address - current_end

            # Check if this span fits into the current block:
            # Overlapping or contiguous (gap <= 0) or small gap <= max_gap_tolerance
            if gap <= max_gap_tolerance:
                new_end = max(current_end, span.start_address + span.count)
                new_count = new_end - current_block.start_address
                if new_count <= max_read_registers:
                    current_block.count = new_count
                    current_block.spans.append(span)
                    continue

            # Finish current block and start a new one
            blocks.append(current_block)
            current_block = RegisterBlock(
                register_type=rtype,
                start_address=span.start_address,
                count=span.count,
                spans=[span],
            )

        if current_block is not None:
            blocks.append(current_block)

    return blocks, unhandled

"""Offline slave circuit breaker and health state machine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_OFFLINE = "offline"


@dataclass
class SlaveBreakerState:
    slave_id: int
    state: str = STATE_HEALTHY
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_failure_time: datetime | None = None
    last_success_time: datetime | None = None
    last_error: str | None = None
    backoff_seconds: float = 0.0
    next_probe_time: datetime | None = None
    total_failures: int = 0
    total_successes: int = 0


class SlaveCircuitBreaker:
    """Tracks slave health and backs off unresponsive slaves to protect the bus.

    - Healthy: normal polling.
    - Degraded: after failure_threshold (e.g. 2) consecutive failures.
    - Offline: after offline_threshold (e.g. 4) consecutive failures.
    - Uses exponential backoff (e.g. 5s -> 10s -> 30s -> 60s max) before allowing a probe read.
    - 1 success restores healthy state and clears backoff.
    """

    def __init__(
        self,
        *,
        degraded_threshold: int = 2,
        offline_threshold: int = 4,
        initial_backoff: float = 5.0,
        max_backoff: float = 60.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.degraded_threshold = degraded_threshold
        self.offline_threshold = offline_threshold
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_factor = backoff_factor
        self._states: dict[int, SlaveBreakerState] = {}

    def get_state(self, slave_id: int) -> SlaveBreakerState:
        if slave_id not in self._states:
            self._states[slave_id] = SlaveBreakerState(slave_id=slave_id)
        return self._states[slave_id]

    def should_poll(self, slave_id: int) -> bool:
        """Return True if the slave is healthy or ready for a recovery probe."""
        state = self.get_state(slave_id)
        if state.state == STATE_HEALTHY:
            return True

        now = datetime.now(UTC)
        if state.next_probe_time is None or now >= state.next_probe_time:
            return True

        return False

    def record_success(self, slave_id: int) -> None:
        """Record successful communication, restoring healthy state."""
        state = self.get_state(slave_id)
        now = datetime.now(UTC)
        state.consecutive_successes += 1
        state.total_successes += 1
        state.last_success_time = now
        state.consecutive_failures = 0
        state.last_error = None
        state.backoff_seconds = 0.0
        state.next_probe_time = None
        if state.state != STATE_HEALTHY:
            _LOGGER.info("Slave ID %d recovered: restored to healthy state", slave_id)
            state.state = STATE_HEALTHY

    def record_failure(self, slave_id: int, error: Exception | str) -> None:
        """Record communication failure, escalating backoff and breaker state."""
        state = self.get_state(slave_id)
        now = datetime.now(UTC)
        state.consecutive_failures += 1
        state.total_failures += 1
        state.consecutive_successes = 0
        state.last_failure_time = now
        state.last_error = str(error)

        if state.consecutive_failures >= self.offline_threshold:
            state.state = STATE_OFFLINE
        elif state.consecutive_failures >= self.degraded_threshold:
            state.state = STATE_DEGRADED

        # Calculate exponential backoff
        power = max(0, state.consecutive_failures - self.degraded_threshold)
        backoff = min(
            self.max_backoff, self.initial_backoff * (self.backoff_factor**power)
        )
        state.backoff_seconds = backoff
        import datetime as dt

        state.next_probe_time = now + dt.timedelta(seconds=backoff)

        if (
            state.state == STATE_OFFLINE
            and state.consecutive_failures == self.offline_threshold
        ):
            _LOGGER.warning(
                "Slave ID %d is now OFFLINE after %d consecutive failures. Backing off for %.1fs: %s",
                slave_id,
                state.consecutive_failures,
                backoff,
                error,
            )

    def _clear_state(self, state: SlaveBreakerState) -> None:
        state.state = STATE_HEALTHY
        state.consecutive_failures = 0
        state.last_error = None
        state.backoff_seconds = 0.0
        state.next_probe_time = None

    def reset(self, slave_id: int | None = None) -> list[int]:
        """Manually restore healthy state for one slave (or all slaves).

        Returns the slave IDs that were reset. A slave that was never tracked
        is silently ignored so automations can reset defensively.
        """
        if slave_id is None:
            reset_ids = sorted(self._states)
            for tracked in self._states.values():
                self._clear_state(tracked)
            return reset_ids
        state = self._states.get(int(slave_id))
        if state is None:
            return []
        self._clear_state(state)
        _LOGGER.info("Slave ID %d circuit breaker manually reset", int(slave_id))
        return [int(slave_id)]

    def get_summary(self) -> dict[int, dict[str, Any]]:
        """Return a UI-friendly status dictionary for all monitored slaves."""
        return {
            sid: {
                "slave_id": sid,
                "state": st.state,
                "consecutive_failures": st.consecutive_failures,
                "last_error": st.last_error,
                "backoff_seconds": round(st.backoff_seconds, 1),
                "last_success": st.last_success_time.isoformat()
                if st.last_success_time
                else None,
                "last_failure": st.last_failure_time.isoformat()
                if st.last_failure_time
                else None,
            }
            for sid, st in self._states.items()
        }

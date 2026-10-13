import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


logger = logging.getLogger("uvicorn.error")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class BackendState:
    url: str
    healthy: bool = True
    consecutive_failures: int = 0
    last_checked: datetime | None = None
    failure_generation: int = 0
    circuit_state: CircuitState = CircuitState.CLOSED
    circuit_failures: int = 0
    circuit_open_until: float | None = None
    half_open_probe_in_flight: bool = False


class BackendPool:
    def __init__(
        self,
        backend_urls: list[str],
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if not backend_urls:
            raise ValueError("BackendPool requires at least one backend URL.")
        if len({url.rstrip("/") for url in backend_urls}) != len(backend_urls):
            raise ValueError("BackendPool backend URLs must be unique.")
        if type(failure_threshold) is not int or failure_threshold < 1:
            raise ValueError("Circuit failure threshold must be a positive integer.")
        if (
            isinstance(cooldown_seconds, bool)
            or not isinstance(cooldown_seconds, (int, float))
            or not math.isfinite(cooldown_seconds)
            or cooldown_seconds <= 0
        ):
            raise ValueError("Circuit cooldown must be a positive number.")

        self.backends = [BackendState(url=url) for url in backend_urls]
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._next_index = 0

    @staticmethod
    def _transition(backend: BackendState, state: CircuitState) -> None:
        if backend.circuit_state == state:
            return
        previous_state = backend.circuit_state
        backend.circuit_state = state
        logger.warning(
            "%s circuit %s -> %s at %s",
            backend.url,
            previous_state.value,
            state.value,
            datetime.now(timezone.utc).isoformat(),
        )

    def _advance_circuit(self, backend: BackendState, now: float) -> None:
        if (
            backend.circuit_state == CircuitState.OPEN
            and backend.circuit_open_until is not None
            and now >= backend.circuit_open_until
        ):
            backend.circuit_open_until = None
            backend.half_open_probe_in_flight = False
            self._transition(backend, CircuitState.HALF_OPEN)

    def next_backend(self, exclude_url: str | None = None) -> str | None:
        now = time.monotonic()
        for offset in range(len(self.backends)):
            index = (self._next_index + offset) % len(self.backends)
            backend = self.backends[index]
            self._advance_circuit(backend, now)
            if (
                not backend.healthy
                or backend.url == exclude_url
                or backend.circuit_state == CircuitState.OPEN
            ):
                continue
            if backend.circuit_state == CircuitState.HALF_OPEN:
                if backend.half_open_probe_in_flight:
                    continue
                backend.half_open_probe_in_flight = True

            self._next_index = (index + 1) % len(self.backends)
            return backend.url
        return None

    def is_half_open_probe(self, backend_url: str) -> bool:
        backend = self.get_backend_state(backend_url)
        return bool(
            backend is not None
            and backend.circuit_state == CircuitState.HALF_OPEN
            and backend.half_open_probe_in_flight
        )

    def abandon_half_open_probe(self, backend_url: str) -> None:
        backend = self.get_backend_state(backend_url)
        if (
            backend is not None
            and backend.circuit_state == CircuitState.HALF_OPEN
        ):
            backend.half_open_probe_in_flight = False

    def record_request_result(
        self,
        backend_url: str,
        succeeded: bool,
        half_open_probe: bool = False,
    ) -> None:
        backend = self.get_backend_state(backend_url)
        if backend is None:
            return

        if half_open_probe:
            if (
                backend.circuit_state != CircuitState.HALF_OPEN
                or not backend.half_open_probe_in_flight
            ):
                return
            backend.half_open_probe_in_flight = False
            if succeeded:
                backend.circuit_failures = 0
                backend.circuit_open_until = None
                self._transition(backend, CircuitState.CLOSED)
            else:
                self._open_circuit(backend)
            return

        if backend.circuit_state != CircuitState.CLOSED:
            return
        if succeeded:
            backend.circuit_failures = 0
            return

        backend.circuit_failures += 1
        if backend.circuit_failures >= self.failure_threshold:
            self._open_circuit(backend)

    def _open_circuit(self, backend: BackendState) -> None:
        backend.half_open_probe_in_flight = False
        backend.circuit_open_until = time.monotonic() + self.cooldown_seconds
        self._transition(backend, CircuitState.OPEN)

    def get_backend_state(self, backend_url: str) -> BackendState | None:
        return next(
            (backend for backend in self.backends if backend.url == backend_url),
            None,
        )

    def has_healthy_backends(self) -> bool:
        return any(backend.healthy for backend in self.backends)

    def get_status(self) -> list[dict[str, object]]:
        now = time.monotonic()
        statuses: list[dict[str, object]] = []
        for backend in self.backends:
            self._advance_circuit(backend, now)
            retry_after = (
                max(0.0, backend.circuit_open_until - now)
                if backend.circuit_state == CircuitState.OPEN
                and backend.circuit_open_until is not None
                else 0.0
            )
            statuses.append(
                {
                    "url": backend.url,
                    "healthy": backend.healthy,
                    "consecutive_failures": backend.consecutive_failures,
                    "last_checked": backend.last_checked,
                    "circuit_state": backend.circuit_state.value,
                    "circuit_failures": backend.circuit_failures,
                    "circuit_retry_after_seconds": round(retry_after, 2),
                }
            )
        return statuses

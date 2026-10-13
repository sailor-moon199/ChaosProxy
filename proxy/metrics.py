from collections.abc import Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from proxy.router import BackendState

REGISTRY = CollectorRegistry()

REQUESTS_TOTAL = Counter(
    "chaosproxy_requests_total",
    "Total HTTP requests handled by ChaosProxy.",
    ["backend", "status_code"],
    registry=REGISTRY,
)
REQUEST_LATENCY_SECONDS = Histogram(
    "chaosproxy_request_latency_seconds",
    "End-to-end request latency in seconds.",
    ["backend"],
    registry=REGISTRY,
)
BACKEND_HEALTH = Gauge(
    "chaosproxy_backend_health",
    "Backend health state: 1 for healthy and 0 for unhealthy.",
    ["backend"],
    registry=REGISTRY,
)
FAULTS_INJECTED_TOTAL = Counter(
    "chaosproxy_faults_injected_total",
    "Total injected faults by type.",
    ["type"],
    registry=REGISTRY,
)
RETRIES_TOTAL = Counter(
    "chaosproxy_retries_total",
    "Total passive failover retries attempted.",
    registry=REGISTRY,
)
CIRCUIT_STATE = Gauge(
    "chaosproxy_circuit_state",
    "Backend circuit state: 0 closed, 1 half-open, and 2 open.",
    ["backend"],
    registry=REGISTRY,
)


def set_backend_health(backend_url: str, healthy: bool) -> None:
    BACKEND_HEALTH.labels(backend=backend_url).set(1 if healthy else 0)


def update_backend_health(backends: Iterable[BackendState]) -> None:
    for backend in backends:
        set_backend_health(backend.url, backend.healthy)


def update_backend_circuit_states(backends: Iterable[BackendState]) -> None:
    for backend in backends:
        circuit_state_value = {
            "CLOSED": 0,
            "HALF_OPEN": 1,
            "OPEN": 2,
        }[backend.circuit_state.value]
        CIRCUIT_STATE.labels(backend=backend.url).set(circuit_state_value)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

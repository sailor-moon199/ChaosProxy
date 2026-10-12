import unittest

from starlette.requests import Request
from starlette.responses import Response

from proxy.faults import record_fault_injected
from proxy.health import record_backend_failure, record_backend_success
from proxy.main import MANAGEMENT_PATHS, record_request_metrics
from proxy.metrics import (
    FAULTS_INJECTED_TOTAL,
    REGISTRY,
    update_backend_health,
)
from proxy.router import BackendState


def _sample_value(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
        }
    )


class MetricsInstrumentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_request_records_backend_status_and_latency(self) -> None:
        backend_url = "http://metrics-test-backend"
        request = _request("/private/path")
        labels = {"backend": backend_url, "status_code": "201"}
        request_count_before = _sample_value("chaosproxy_requests_total", labels)
        latency_count_before = _sample_value(
            "chaosproxy_request_latency_seconds_count",
            {"backend": backend_url},
        )

        async def call_next(current_request: Request) -> Response:
            current_request.state.metrics_backend = backend_url
            return Response(status_code=201)

        response = await record_request_metrics(request, call_next)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            _sample_value("chaosproxy_requests_total", labels),
            request_count_before + 1,
        )
        self.assertEqual(
            _sample_value(
                "chaosproxy_request_latency_seconds_count",
                {"backend": backend_url},
            ),
            latency_count_before + 1,
        )

    async def test_management_requests_are_not_counted(self) -> None:
        before = {
            path: _sample_value(
                "chaosproxy_requests_total",
                {"backend": "none", "status_code": "200"},
            )
            for path in MANAGEMENT_PATHS
        }

        for path in MANAGEMENT_PATHS:
            async def call_next(request: Request) -> Response:
                return Response(status_code=200)

            await record_request_metrics(_request(path), call_next)

        after = {
            path: _sample_value(
                "chaosproxy_requests_total",
                {"backend": "none", "status_code": "200"},
            )
            for path in MANAGEMENT_PATHS
        }
        self.assertEqual(after, before)

    async def test_fault_counter_and_backend_health_gauge_update(self) -> None:
        fault_before = _sample_value(
            "chaosproxy_faults_injected_total",
            {"type": "drop"},
        )
        record_fault_injected("drop")
        self.assertEqual(
            _sample_value(
                "chaosproxy_faults_injected_total",
                {"type": "drop"},
            ),
            fault_before + 1,
        )

        backend = BackendState("http://health-metrics-test")
        update_backend_health([backend])
        self.assertEqual(
            _sample_value(
                "chaosproxy_backend_health",
                {"backend": backend.url},
            ),
            1.0,
        )
        record_backend_failure(backend)
        record_backend_failure(backend)
        record_backend_failure(backend)
        self.assertEqual(
            _sample_value(
                "chaosproxy_backend_health",
                {"backend": backend.url},
            ),
            0.0,
        )
        record_backend_success(backend)
        self.assertEqual(
            _sample_value(
                "chaosproxy_backend_health",
                {"backend": backend.url},
            ),
            1.0,
        )

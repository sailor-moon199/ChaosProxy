import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import Request

from proxy.faults import fault_rules
from proxy.main import proxy_request
from proxy.router import BackendPool, CircuitState


class CircuitBreakerTests(unittest.TestCase):
    def test_consecutive_failures_open_circuit_and_success_resets_count(self) -> None:
        pool = BackendPool(
            ["http://one", "http://two"],
            failure_threshold=3,
            cooldown_seconds=30,
        )
        backend_url = "http://one"

        pool.record_request_result(backend_url, succeeded=False)
        pool.record_request_result(backend_url, succeeded=False)
        pool.record_request_result(backend_url, succeeded=True)
        pool.record_request_result(backend_url, succeeded=False)
        pool.record_request_result(backend_url, succeeded=False)

        backend = pool.get_backend_state(backend_url)
        self.assertIsNotNone(backend)
        self.assertEqual(backend.circuit_failures, 2)
        self.assertEqual(backend.circuit_state, CircuitState.CLOSED)

        pool.record_request_result(backend_url, succeeded=False)
        self.assertEqual(backend.circuit_state, CircuitState.OPEN)
        self.assertEqual(pool.next_backend(), "http://two")
        self.assertIsNone(pool.next_backend(exclude_url="http://two"))

    def test_cooldown_allows_exactly_one_half_open_trial(self) -> None:
        pool = BackendPool(
            ["http://one"],
            failure_threshold=2,
            cooldown_seconds=10,
        )
        backend_url = pool.next_backend()
        self.assertEqual(backend_url, "http://one")

        with patch("proxy.router.time.monotonic", return_value=100):
            pool.record_request_result(backend_url, succeeded=False)
            pool.record_request_result(backend_url, succeeded=False)
            backend = pool.get_backend_state(backend_url)
            self.assertEqual(backend.circuit_open_until, 110)
            self.assertIsNone(pool.next_backend())

        with patch("proxy.router.time.monotonic", return_value=110):
            self.assertEqual(pool.next_backend(), backend_url)
            self.assertTrue(pool.is_half_open_probe(backend_url))
            self.assertIsNone(pool.next_backend())
            pool.record_request_result(
                backend_url,
                succeeded=True,
                half_open_probe=True,
            )

        backend = pool.get_backend_state(backend_url)
        self.assertEqual(backend.circuit_state, CircuitState.CLOSED)
        self.assertEqual(backend.circuit_failures, 0)
        self.assertEqual(pool.next_backend(), backend_url)

    def test_failed_half_open_trial_reopens_for_a_fresh_cooldown(self) -> None:
        pool = BackendPool(
            ["http://one"],
            failure_threshold=1,
            cooldown_seconds=10,
        )
        backend_url = pool.next_backend()
        with patch("proxy.router.time.monotonic", return_value=100):
            pool.record_request_result(backend_url, succeeded=False)

        with patch("proxy.router.time.monotonic", return_value=110):
            self.assertEqual(pool.next_backend(), backend_url)
            pool.record_request_result(
                backend_url,
                succeeded=False,
                half_open_probe=True,
            )
            backend = pool.get_backend_state(backend_url)
            self.assertEqual(backend.circuit_state, CircuitState.OPEN)
            self.assertEqual(backend.circuit_open_until, 120)
            self.assertEqual(pool.get_status()[0]["circuit_retry_after_seconds"], 10)

    def test_late_closed_request_cannot_complete_half_open_trial(self) -> None:
        pool = BackendPool(
            ["http://one"],
            failure_threshold=1,
            cooldown_seconds=1,
        )
        backend_url = pool.next_backend()
        with patch("proxy.router.time.monotonic", return_value=1):
            pool.record_request_result(backend_url, succeeded=False)

        with patch("proxy.router.time.monotonic", return_value=2):
            self.assertEqual(pool.next_backend(), backend_url)
            pool.record_request_result(backend_url, succeeded=True)

        backend = pool.get_backend_state(backend_url)
        self.assertEqual(backend.circuit_state, CircuitState.HALF_OPEN)
        self.assertTrue(backend.half_open_probe_in_flight)


class CircuitBreakerProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_rules = (
            fault_rules.latency_ms,
            fault_rules.error_rate,
            fault_rules.drop_rate,
        )
        fault_rules.latency_ms = 0
        fault_rules.error_rate = 0
        fault_rules.drop_rate = 0

    async def asyncTearDown(self) -> None:
        (
            fault_rules.latency_ms,
            fault_rules.error_rate,
            fault_rules.drop_rate,
        ) = self.original_rules

    @staticmethod
    def _request(pool: BackendPool | None = None) -> Request:
        if pool is None:
            pool = BackendPool(
                ["http://misbehaving-backend"],
                failure_threshold=2,
                cooldown_seconds=30,
            )
        class Client:
            async def request(self, **kwargs: object) -> httpx.Response:
                return httpx.Response(
                    500,
                    request=httpx.Request("GET", str(kwargs["url"])),
                )

        app = SimpleNamespace(
            state=SimpleNamespace(
                backend_pool=pool,
                http_client=Client(),
                config=SimpleNamespace(max_body_bytes=1024),
            )
        )
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [],
                "server": ("proxy", 8000),
                "client": ("test", 1234),
                "app": app,
            },
            receive=lambda: _empty_request_message(),
        )
        request.scope["app"] = app
        return request

    async def test_real_backend_5xx_opens_circuit_and_stops_forwarding(self) -> None:
        pool = BackendPool(
            ["http://misbehaving-backend"],
            failure_threshold=2,
            cooldown_seconds=30,
        )

        first = await proxy_request("", self._request(pool))
        second = await proxy_request("", self._request(pool))
        third = await proxy_request("", self._request(pool))

        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 500)
        self.assertEqual(third.status_code, 503)
        self.assertEqual(
            pool.get_backend_state("http://misbehaving-backend").circuit_state,
            CircuitState.OPEN,
        )

    async def test_injected_503_does_not_count_as_circuit_failure(self) -> None:
        pool = BackendPool(
            ["http://misbehaving-backend"],
            failure_threshold=2,
            cooldown_seconds=30,
        )
        request = self._request(pool)
        fault_rules.error_rate = 1

        with patch("proxy.main.random.random", return_value=0.5):
            response = await proxy_request("", request)

        self.assertEqual(response.status_code, 503)
        backend = pool.get_backend_state("http://misbehaving-backend")
        self.assertEqual(backend.circuit_state, CircuitState.CLOSED)
        self.assertEqual(backend.circuit_failures, 0)

    async def test_connection_error_and_timeout_count_as_failures(self) -> None:
        for error, expected_status in (
            (httpx.ConnectError("connection failed"), 502),
            (httpx.ReadTimeout("request timed out"), 504),
        ):
            with self.subTest(error=type(error).__name__):
                pool = BackendPool(
                    ["http://misbehaving-backend"],
                    failure_threshold=1,
                    cooldown_seconds=30,
                )
                request = self._request(pool)

                class Client:
                    async def request(self, **kwargs: object) -> httpx.Response:
                        raise error

                request.app.state.http_client = Client()
                response = await proxy_request("", request)

                self.assertEqual(response.status_code, expected_status)
                backend = pool.get_backend_state(
                    "http://misbehaving-backend"
                )
                self.assertEqual(backend.circuit_state, CircuitState.OPEN)
                self.assertEqual(backend.circuit_failures, 1)


async def _empty_request_message() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}

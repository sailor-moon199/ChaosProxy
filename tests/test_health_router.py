import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from proxy.health import (
    _check_backend,
    health_check_loop,
    record_backend_failure,
)
from proxy.router import BackendPool, BackendState


class HealthStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_success_does_not_clear_newer_passive_failure(self) -> None:
        backend = BackendState("http://backend")

        class Client:
            async def get(self, url: str, timeout: float) -> httpx.Response:
                record_backend_failure(backend)
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                )

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = Client().get
        await _check_backend(client, backend)

        self.assertTrue(backend.healthy)
        self.assertEqual(backend.consecutive_failures, 1)

    async def test_unexpected_check_error_does_not_stop_health_loop(self) -> None:
        backends = [
            BackendState("http://one"),
            BackendState("http://two"),
        ]
        calls = {backend.url: 0 for backend in backends}
        second_cycle = asyncio.Event()

        async def fake_check(
            client: httpx.AsyncClient,
            backend: BackendState,
        ) -> None:
            calls[backend.url] += 1
            if backend.url == "http://one" and calls[backend.url] == 1:
                raise RuntimeError("unexpected check failure")
            if all(count >= 2 for count in calls.values()):
                second_cycle.set()

        with patch("proxy.health._check_backend", side_effect=fake_check):
            async with httpx.AsyncClient() as client:
                task = asyncio.create_task(
                    health_check_loop(
                        client,
                        backends,
                        interval_seconds=0.01,
                    )
                )
                try:
                    await asyncio.wait_for(second_cycle.wait(), timeout=1.0)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        self.assertGreaterEqual(calls["http://one"], 2)
        self.assertGreaterEqual(calls["http://two"], 2)


class BackendPoolTests(unittest.TestCase):
    def test_duplicate_urls_are_rejected_after_trailing_slash_normalization(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            BackendPool(["http://backend/", "http://backend"])

    def test_round_robin_skips_unhealthy_backends(self) -> None:
        pool = BackendPool(["http://one", "http://two", "http://three"])
        pool.backends[1].healthy = False

        self.assertEqual(pool.next_backend(), "http://one")
        self.assertEqual(pool.next_backend(), "http://three")
        self.assertEqual(pool.next_backend(), "http://one")

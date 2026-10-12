import asyncio
import logging
from datetime import datetime, timezone

import httpx

from proxy.router import BackendState

logger = logging.getLogger("uvicorn.error")
UNHEALTHY_FAILURE_THRESHOLD = 3


def record_backend_failure(
    backend: BackendState,
    checked_at: datetime | None = None,
) -> None:
    checked_at = checked_at or datetime.now(timezone.utc)
    backend.last_checked = checked_at
    backend.consecutive_failures += 1
    backend.failure_generation += 1
    if (
        backend.healthy
        and backend.consecutive_failures >= UNHEALTHY_FAILURE_THRESHOLD
    ):
        backend.healthy = False
        logger.warning(
            "%s HEALTHY -> UNHEALTHY at %s",
            backend.url,
            checked_at.isoformat(),
        )


def record_backend_success(
    backend: BackendState,
    checked_at: datetime | None = None,
    expected_failure_generation: int | None = None,
) -> None:
    checked_at = checked_at or datetime.now(timezone.utc)
    backend.last_checked = checked_at
    if (
        expected_failure_generation is not None
        and backend.failure_generation != expected_failure_generation
    ):
        return
    backend.consecutive_failures = 0
    if not backend.healthy:
        backend.healthy = True
        logger.info(
            "%s UNHEALTHY -> HEALTHY at %s",
            backend.url,
            checked_at.isoformat(),
        )


async def _check_backend(
    client: httpx.AsyncClient,
    backend: BackendState,
) -> None:
    failure_generation = backend.failure_generation
    health_url = f"{backend.url.rstrip('/')}/health"
    try:
        response = await client.get(health_url, timeout=2.0)
        succeeded = response.is_success
    except httpx.TimeoutException:
        logger.warning("Health check timed out for %s", backend.url)
        succeeded = False
    except httpx.RequestError as error:
        logger.warning("Health check failed for %s: %r", backend.url, error)
        succeeded = False
    else:
        if not succeeded:
            logger.warning(
                "Health check returned HTTP %d for %s",
                response.status_code,
                backend.url,
            )

    if succeeded:
        record_backend_success(
            backend,
            expected_failure_generation=failure_generation,
        )
        return

    record_backend_failure(backend)


async def health_check_loop(
    client: httpx.AsyncClient,
    backends: list[BackendState],
    interval_seconds: float = 5.0,
) -> None:
    loop = asyncio.get_running_loop()
    while True:
        cycle_started = loop.time()
        results = await asyncio.gather(
            *(_check_backend(client, backend) for backend in backends),
            return_exceptions=True,
        )
        for backend, result in zip(backends, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.error(
                    "Unexpected error checking backend %s: %r",
                    backend.url,
                    result,
                )
        elapsed = loop.time() - cycle_started
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))
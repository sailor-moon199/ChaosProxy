import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from proxy.config import load_config
from proxy.faults import FaultRules, fault_rules
from proxy.health import health_check_loop, record_backend_failure
from proxy.router import BackendPool
from proxy.server import DropConnectionResponse

HTTP_METHODS = [
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
]
IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE"}
HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}


def _connection_headers(headers: list[tuple[bytes, bytes]]) -> set[bytes]:
    excluded = HOP_BY_HOP_HEADERS.copy()
    for name, value in headers:
        if name.lower() == b"connection":
            excluded.update(
                token.strip().lower()
                for token in value.split(b",")
                if token.strip()
            )
    return excluded


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_config()
    app.state.config = config
    app.state.http_client = httpx.AsyncClient()
    app.state.backend_pool = BackendPool(config.backends)
    fault_rules.latency_ms = config.faults.latency_ms
    fault_rules.error_rate = config.faults.error_rate
    fault_rules.drop_rate = config.faults.drop_rate
    app.state.health_check_task = asyncio.create_task(
        health_check_loop(app.state.http_client, app.state.backend_pool.backends),
        name="backend-health-checker",
    )
    try:
        yield
    finally:
        app.state.health_check_task.cancel()
        try:
            await app.state.health_check_task
        except asyncio.CancelledError:
            pass
        await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)


class FaultRulesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    drop_rate: float = Field(ge=0, le=1)


@app.get("/control")
async def get_control() -> FaultRules:
    return fault_rules


@app.post("/control")
async def update_control(rules: FaultRulesUpdate) -> FaultRules:
    fault_rules.latency_ms = rules.latency_ms
    fault_rules.error_rate = rules.error_rate
    fault_rules.drop_rate = rules.drop_rate
    return fault_rules


@app.get("/status")
async def get_status(request: Request) -> dict[str, list[dict[str, object]]]:
    return {"backends": request.app.state.backend_pool.get_status()}


@app.api_route("/{path:path}", methods=HTTP_METHODS)
async def proxy_request(path: str, request: Request) -> Response:
    backend_pool: BackendPool = request.app.state.backend_pool
    if not backend_pool.has_healthy_backends():
        return JSONResponse(
            status_code=503,
            content={"error": "no healthy backends"},
        )

    if random.random() < fault_rules.drop_rate:
        return DropConnectionResponse(request.client)
    if random.random() < fault_rules.error_rate:
        return JSONResponse(
            status_code=503,
            content={"detail": "Injected error"},
        )
    if fault_rules.latency_ms > 0:
        await asyncio.sleep(fault_rules.latency_ms / 1000)

    max_body_bytes: int = request.app.state.config.max_body_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
                headers={"Connection": "close"},
            )
        if declared_length < 0:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
                headers={"Connection": "close"},
            )
        if declared_length > max_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body exceeds the configured limit"},
                headers={"Connection": "close"},
            )

    body_chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > max_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body exceeds the configured limit"},
                headers={"Connection": "close"},
            )
        body_chunks.append(chunk)
    request_body = b"".join(body_chunks)

    client: httpx.AsyncClient = request.app.state.http_client
    backend_url = backend_pool.next_backend()
    if backend_url is None:
        return JSONResponse(
            status_code=503,
            content={"error": "no healthy backends"},
        )
    request_headers = request.headers.raw
    excluded_request_headers = _connection_headers(request_headers) | {
        b"host",
        b"content-length",
    }
    headers = [
        (name, value)
        for name, value in request_headers
        if name.lower() not in excluded_request_headers
    ]

    target_url = httpx.URL(f"{backend_url}/{path}").copy_with(
        query=request.url.query.encode("ascii")
    )
    retried = False
    while True:
        try:
            backend_response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=request_body,
            )
            break
        except httpx.TransportError as error:
            failed_backend = backend_pool.get_backend_state(backend_url)
            if failed_backend is not None:
                record_backend_failure(failed_backend)

            if not retried and request.method in IDEMPOTENT_METHODS:
                retry_backend_url = backend_pool.next_backend(
                    exclude_url=backend_url
                )
                if retry_backend_url is not None:
                    backend_url = retry_backend_url
                    target_url = httpx.URL(f"{backend_url}/{path}").copy_with(
                        query=request.url.query.encode("ascii")
                    )
                    retried = True
                    continue

            status_code = 504 if isinstance(error, httpx.TimeoutException) else 502
            detail = (
                "Backend request timed out"
                if status_code == 504
                else "Backend request failed"
            )
            response_headers = {"X-Retried": "true"} if retried else None
            return JSONResponse(
                status_code=status_code,
                content={"detail": detail},
                headers=response_headers,
            )
        except httpx.RequestError:
            failed_backend = backend_pool.get_backend_state(backend_url)
            if failed_backend is not None:
                record_backend_failure(failed_backend)
            response_headers = {"X-Retried": "true"} if retried else None
            return JSONResponse(
                status_code=502,
                content={"detail": "Backend request failed"},
                headers=response_headers,
            )

    response = Response(
        content=backend_response.content,
        status_code=backend_response.status_code,
    )
    excluded_response_headers = _connection_headers(
        backend_response.headers.raw
    ) | {b"content-length", b"x-served-by"}
    response.raw_headers = [
        (name, value)
        for name, value in backend_response.headers.raw
        if name.lower() not in excluded_response_headers
    ] + [
        (name, value)
        for name, value in response.raw_headers
        if name.lower() == b"content-length"
    ]
    response.headers["X-Served-By"] = backend_url
    if retried:
        response.headers["X-Retried"] = "true"
    return response
import asyncio

import uvicorn
from starlette.datastructures import Address
from starlette.responses import Response
from starlette.types import Receive, Scope, Send
from uvicorn.protocols.http.h11_impl import H11Protocol

from proxy.config import load_config

_connections: dict[tuple[str, int], asyncio.Transport] = {}


def _peer_key(peer: object) -> tuple[str, int] | None:
    if (
        isinstance(peer, tuple)
        and len(peer) >= 2
        and isinstance(peer[0], str)
        and isinstance(peer[1], int)
    ):
        return peer[0], peer[1]
    return None


def _abort_connection(client: Address | None) -> bool:
    if client is None:
        return False
    transport = _connections.get((client.host, client.port))
    if transport is None or transport.is_closing():
        return False
    transport.abort()
    return True


class DropAwareH11Protocol(H11Protocol):
    def connection_made(self, transport: asyncio.Transport) -> None:
        super().connection_made(transport)
        key = _peer_key(transport.get_extra_info("peername"))
        if key is not None:
            _connections[key] = transport

    def connection_lost(self, exc: Exception | None) -> None:
        key = _peer_key(self.transport.get_extra_info("peername"))
        if key is not None and _connections.get(key) is self.transport:
            del _connections[key]
        super().connection_lost(exc)


class DropConnectionResponse(Response):
    def __init__(self, client: Address | None) -> None:
        super().__init__(status_code=444, headers={"Connection": "close"})
        self.client = client

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if _abort_connection(self.client):
            await asyncio.sleep(0)
            return
        await super().__call__(scope, receive, send)


def run() -> None:
    config = load_config()
    uvicorn.run(
        "proxy.main:app",
        host="127.0.0.1",
        port=config.listen_port,
        http="proxy.server:DropAwareH11Protocol",
        server_header=False,
        date_header=False,
    )


if __name__ == "__main__":
    run()

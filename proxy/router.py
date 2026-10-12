from dataclasses import dataclass
from datetime import datetime


@dataclass
class BackendState:
    url: str
    healthy: bool = True
    consecutive_failures: int = 0
    last_checked: datetime | None = None
    failure_generation: int = 0


class BackendPool:
    def __init__(self, backend_urls: list[str]) -> None:
        if not backend_urls:
            raise ValueError("BackendPool requires at least one backend URL.")
        canonical_urls = [url.rstrip("/") for url in backend_urls]
        if len(set(canonical_urls)) != len(canonical_urls):
            raise ValueError("BackendPool backend URLs must be unique.")
        self.backends = [BackendState(url=url) for url in backend_urls]
        self._next_index = 0

    def next_backend(self, exclude_url: str | None = None) -> str | None:
        for offset in range(len(self.backends)):
            index = (self._next_index + offset) % len(self.backends)
            backend = self.backends[index]
            if backend.healthy and backend.url != exclude_url:
                self._next_index = (index + 1) % len(self.backends)
                return backend.url
        return None

    def get_backend_state(self, backend_url: str) -> BackendState | None:
        return next(
            (backend for backend in self.backends if backend.url == backend_url),
            None,
        )

    def has_healthy_backends(self) -> bool:
        return any(backend.healthy for backend in self.backends)

    def get_status(self) -> list[dict[str, object]]:
        return [
            {
                "url": backend.url,
                "healthy": backend.healthy,
                "consecutive_failures": backend.consecutive_failures,
                "last_checked": backend.last_checked,
            }
            for backend in self.backends
        ]
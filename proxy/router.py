class BackendPool:
    def __init__(self, backend_urls: list[str]) -> None:
        if not backend_urls:
            raise ValueError("BackendPool requires at least one backend URL.")
        self.backend_urls = backend_urls
        self._next_index = 0

    def next_backend(self) -> str:
        backend_url = self.backend_urls[self._next_index]
        self._next_index = (self._next_index + 1) % len(self.backend_urls)
        return backend_url
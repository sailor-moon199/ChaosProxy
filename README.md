# ChaosProxy

The proxy reads its backend URLs, listening port, and startup fault rules from
`config.yaml`. Start the backend processes, then run the proxy from the project
root:

```powershell
.\venv\Scripts\python.exe -m proxy.server
```

The proxy listens on the `listen_port` in `config.yaml`. Changes to startup
fault defaults take effect after restarting the proxy; the `/control` API can
still update rules while it is running. The `max_body_bytes` setting limits
the size of client request bodies; it defaults to 10 MiB.

The background health checker tests every backend every five seconds. Three
consecutive failed checks mark a backend unhealthy; a successful check marks it
healthy again. Use `GET /status` to view each backend's health state and last
check time. Backend URLs must be unique. An unexpected error checking one
backend is logged without stopping checks for the others, and a health result
that began before a newer passive failure cannot erase that failure.

Prometheus metrics are exposed at `GET /metrics` in the standard text
exposition format. The endpoint reports proxy request totals and latency,
backend health, injected fault counts, and passive retries. `/metrics`,
`/status`, and `/control` requests are excluded from request and latency
metrics. Labels use backend URLs and status codes or fault types; request paths
are never metric labels. Backend health metrics are initialized at startup and
updated when a backend changes health state.

For `GET`, `HEAD`, `PUT`, and `DELETE`, a connection-level upstream failure or
timeout is counted against that backend and retried once on another healthy
backend. Requests are never retried after receiving an HTTP response, and
non-idempotent methods such as `POST` are not retried. A response following a
retry includes `X-Retried: true`.

Run the health-state and backend-pool unit tests from the project root with:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```
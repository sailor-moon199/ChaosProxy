# ChaosProxy

The proxy reads its backend URLs, listening port, and startup fault rules from
`config.yaml`. Start the backend processes, then run the proxy from the project
root:

```powershell
.\venv\Scripts\python.exe -m proxy.server
```

The proxy listens on the `listen_port` in `config.yaml`. Changes to startup
fault defaults and circuit-breaker settings take effect after restarting the
proxy; the `/control` API can still update fault rules while it is running.
The `max_body_bytes` setting limits the size of client request bodies; it
defaults to 10 MiB.

The background health checker tests every backend every five seconds. Three
consecutive failed checks mark a backend unhealthy; a successful check marks it
healthy again. Use `GET /status` to view each backend's health state and last
check time. Backend URLs must be unique. An unexpected error checking one
backend is logged without stopping checks for the others, and a health result
that began before a newer passive failure cannot erase that failure.

For a circuit-breaker demo, start the dummy backend processes with distinct
ports and names, for example:

```powershell
.\venv\Scripts\python.exe backends\backend.py --port 9002 --name backend-2
```

Toggle only that backend into and out of failing mode with:

```powershell
curl.exe -X POST http://127.0.0.1:9002/fail
curl.exe -X POST http://127.0.0.1:9002/heal
```

In failing mode, `GET /` returns HTTP 500 while `GET /health` continues to
return `{"status":"ok"}`. Send enough requests through the proxy for that
backend to receive five failures (with three backends, about 15 round-robin
requests), then inspect `GET /status`; the backend remains healthy but its
circuit opens and traffic is routed to the other backends. After the cooldown,
call `/heal` before the next trial request. The single HALF_OPEN trial then
succeeds and closes the circuit.

Each backend also has an independent circuit breaker. By default, five
consecutive real request failures open its circuit for 30 seconds. Connection
errors, timeouts, and real backend 5xx responses count as failures; successful
non-5xx responses reset the consecutive-failure count. Injected faults do not
reach a backend and do not count. After the cooldown, exactly one request is
allowed as a HALF_OPEN trial; success closes the circuit and failure opens it
again for a fresh cooldown. Routing requires both a healthy backend and a
circuit that is not OPEN. Configure `failure_threshold` and `cooldown_seconds`
in the `circuit_breaker` section of `config.yaml`. `/status` and the dashboard
show each backend's circuit state and remaining open time.

The Streamlit dashboard polls backend status, fault rules, and Prometheus
metrics every two seconds. It shows gateway availability, backend health, and
request/error rates over the last 60 seconds; mean request latency is charted
when histogram data is available. Use the sidebar latency, error-rate, and
drop-rate sliders with **Apply** to update `/control`, or **Reset** to set all
rules to zero. Run it from the project root with:

```powershell
.\venv\Scripts\python.exe -m streamlit run dashboard\app.py
```

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
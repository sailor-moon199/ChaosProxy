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
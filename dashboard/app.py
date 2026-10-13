import html
import time
from datetime import datetime
from typing import Any

import altair as alt
import httpx
import pandas as pd
import streamlit as st
from prometheus_client.parser import text_string_to_metric_families

PROXY_BASE_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_SECONDS = 2.0
HISTORY_SECONDS = 60
POLL_INTERVAL_SECONDS = 2
BACKEND_COUNT = 3

st.set_page_config(
    layout="wide",
    page_title="ChaosProxy",
    page_icon="🛡️",
)

st.markdown(
    """
    <style>
    .gateway-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        border: 1px solid;
        border-radius: 999px;
        padding: 0.42rem 0.8rem;
        font-size: 0.82rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        white-space: nowrap;
    }
    .gateway-online, .backend-healthy {
        color: #42d392;
        background: rgba(66, 211, 146, 0.12);
        border-color: rgba(66, 211, 146, 0.38);
    }
    .gateway-offline, .backend-unhealthy {
        color: #ff6b75;
        background: rgba(255, 107, 117, 0.12);
        border-color: rgba(255, 107, 117, 0.38);
    }
    .backend-card {
        min-height: 154px;
        padding: 1rem;
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 0.85rem;
    }
    .backend-card-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
        margin-bottom: 1rem;
    }
    .backend-dot {
        width: 0.65rem;
        height: 0.65rem;
        flex: 0 0 auto;
        border-radius: 50%;
    }
    .dot-healthy {
        background: #42d392;
        box-shadow: 0 0 0.65rem rgba(66, 211, 146, 0.65);
    }
    .dot-unhealthy {
        background: #ff6b75;
        box-shadow: 0 0 0.65rem rgba(255, 107, 117, 0.6);
    }
    .backend-state {
        border: 1px solid;
        border-radius: 999px;
        padding: 0.2rem 0.55rem;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }
    .backend-url {
        overflow-wrap: anywhere;
        color: #e6edf3;
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 0.84rem;
    }
    .backend-failures {
        margin-top: 0.85rem;
        color: #9da7b3;
        font-size: 0.82rem;
    }
    .fault-badge {
        display: inline-block;
        margin: 0 0.25rem 0.35rem 0;
        border: 1px solid #30363d;
        border-radius: 999px;
        padding: 0.22rem 0.55rem;
        color: #c9d1d9;
        background: #161b22;
        font-size: 0.76rem;
    }
    .fault-active {
        color: #f2cc60;
        border-color: rgba(242, 204, 96, 0.45);
        background: rgba(242, 204, 96, 0.1);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.session_state.setdefault("dashboard_rate_history", [])
st.session_state.setdefault("dashboard_previous_totals", None)
st.session_state.setdefault("dashboard_previous_latency", None)


def _request_totals(metrics_text: str) -> tuple[float, float]:
    request_total = 0.0
    error_total = 0.0
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name != "chaosproxy_requests_total":
                continue
            request_total += sample.value
            status_code = int(sample.labels.get("status_code", "0"))
            if status_code >= 500 or status_code == 444:
                error_total += sample.value
    return request_total, error_total


def _latency_totals(metrics_text: str) -> tuple[float, float]:
    latency_sum = 0.0
    latency_count = 0.0
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name == "chaosproxy_request_latency_seconds_sum":
                latency_sum += sample.value
            elif sample.name == "chaosproxy_request_latency_seconds_count":
                latency_count += sample.value
    return latency_sum, latency_count


def _counter_delta(current: float, previous: float) -> float:
    if current >= previous:
        return current - previous
    return current


def _poll_rates() -> None:
    response = httpx.get(
        f"{PROXY_BASE_URL}/metrics",
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    request_total, error_total = _request_totals(response.text)
    latency_sum, latency_count = _latency_totals(response.text)
    now = time.monotonic()
    previous = st.session_state.get("dashboard_previous_totals")
    previous_latency = st.session_state.get("dashboard_previous_latency")

    if previous is None:
        request_rate = 0.0
        error_rate = 0.0
    else:
        elapsed = now - previous["time"]
        request_delta = _counter_delta(request_total, previous["requests"])
        error_delta = _counter_delta(error_total, previous["errors"])
        request_rate = request_delta / elapsed if elapsed > 0 else 0.0
        error_rate = (
            100 * error_delta / request_delta if request_delta > 0 else 0.0
        )

    latency_seconds = None
    if previous_latency is not None:
        interval_latency_count = _counter_delta(
            latency_count,
            previous_latency["count"],
        )
        interval_latency_sum = _counter_delta(
            latency_sum,
            previous_latency["sum"],
        )
        if interval_latency_count > 0:
            latency_seconds = interval_latency_sum / interval_latency_count

    st.session_state["dashboard_previous_totals"] = {
        "time": now,
        "requests": request_total,
        "errors": error_total,
    }
    st.session_state["dashboard_previous_latency"] = {
        "sum": latency_sum,
        "count": latency_count,
    }
    history: list[dict[str, Any]] = st.session_state["dashboard_rate_history"]
    history.append(
        {
            "time": datetime.now(),
            "requests_per_second": request_rate,
            "error_percent": error_rate,
            "latency_ms": (
                latency_seconds * 1000 if latency_seconds is not None else None
            ),
        }
    )
    cutoff = time.time() - HISTORY_SECONDS
    st.session_state["dashboard_rate_history"] = [
        point for point in history if point["time"].timestamp() >= cutoff
    ]


def _fetch_json(path: str) -> dict[str, Any]:
    response = httpx.get(
        f"{PROXY_BASE_URL}{path}",
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected response from {path}.")
    return payload


def _submit_fault_rules(reset: bool = False) -> None:
    if reset:
        rules = {"latency_ms": 0, "error_rate": 0.0, "drop_rate": 0.0}
    else:
        rules = {
            "latency_ms": st.session_state["dashboard_latency_ms"],
            "error_rate": st.session_state["dashboard_error_rate"],
            "drop_rate": st.session_state["dashboard_drop_rate"],
        }

    try:
        response = httpx.post(
            f"{PROXY_BASE_URL}/control",
            json=rules,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as error:
        st.session_state["dashboard_fault_action_error"] = str(error)
        st.session_state.pop("dashboard_fault_action_success", None)
        return

    if reset:
        st.session_state["dashboard_latency_ms"] = 0
        st.session_state["dashboard_error_rate"] = 0.0
        st.session_state["dashboard_drop_rate"] = 0.0
    st.session_state["dashboard_fault_action_success"] = (
        "Fault rules reset to zero." if reset else "Fault rules applied."
    )
    st.session_state.pop("dashboard_fault_action_error", None)


def _render_backend_card(backend: dict[str, Any] | None) -> None:
    if backend is None:
        st.markdown(
            """
            <div class="backend-card">
                <div class="backend-card-top">
                    <span class="backend-dot dot-unhealthy"></span>
                    <span class="backend-state backend-unhealthy">NOT CONFIGURED</span>
                </div>
                <div class="backend-url">No backend configured</div>
                <div class="backend-failures">Consecutive failures: —</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    healthy = bool(backend["healthy"])
    state = "HEALTHY" if healthy else "UNHEALTHY"
    state_class = "backend-healthy" if healthy else "backend-unhealthy"
    dot_class = "dot-healthy" if healthy else "dot-unhealthy"
    url = html.escape(str(backend["url"]))
    failures = int(backend["consecutive_failures"])
    st.markdown(
        f"""
        <div class="backend-card">
            <div class="backend-card-top">
                <span class="backend-dot {dot_class}"></span>
                <span class="backend-state {state_class}">{state}</span>
            </div>
            <div class="backend-url">{url}</div>
            <div class="backend-failures">Consecutive failures: {failures}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> None:
    st.title("Fault controls")
    try:
        rules = _fetch_json("/control")
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        st.session_state["dashboard_control_online"] = False
        st.error(f"Gateway offline: could not load fault rules ({error}).")
        return
    st.session_state["dashboard_control_online"] = True
    st.session_state["dashboard_active_rules"] = rules

    badges = []
    for label, value, active in (
        ("Latency", f"{rules['latency_ms']} ms", rules["latency_ms"] > 0),
        ("Error", f"{rules['error_rate']:.0%}", rules["error_rate"] > 0),
        ("Drop", f"{rules['drop_rate']:.0%}", rules["drop_rate"] > 0),
    ):
        active_class = "fault-badge fault-active" if active else "fault-badge"
        badges.append(
            f'<span class="{active_class}">{label}: {value}</span>'
        )
    st.markdown("".join(badges), unsafe_allow_html=True)

    st.session_state.setdefault("dashboard_latency_ms", rules["latency_ms"])
    st.session_state.setdefault("dashboard_error_rate", rules["error_rate"])
    st.session_state.setdefault("dashboard_drop_rate", rules["drop_rate"])
    with st.form("fault_rules_form"):
        st.slider(
            "Latency (ms)",
            min_value=0,
            max_value=10000,
            step=100,
            key="dashboard_latency_ms",
        )
        st.slider(
            "Error rate",
            min_value=0.0,
            max_value=1.0,
            step=0.05,
            format="%.2f",
            key="dashboard_error_rate",
        )
        st.slider(
            "Drop rate",
            min_value=0.0,
            max_value=1.0,
            step=0.05,
            format="%.2f",
            key="dashboard_drop_rate",
        )
        apply_column, reset_column = st.columns(2)
        with apply_column:
            st.form_submit_button("Apply", on_click=_submit_fault_rules)
        with reset_column:
            st.form_submit_button(
                "Reset",
                on_click=_submit_fault_rules,
                args=(True,),
            )

    if error := st.session_state.get("dashboard_fault_action_error"):
        st.error(f"Could not update fault rules: {error}")
    if success := st.session_state.get("dashboard_fault_action_success"):
        st.success(success)


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def render_sidebar() -> None:
    with st.sidebar:
        _render_sidebar()


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def render_dashboard() -> None:
    updated_at = datetime.now().strftime("%H:%M:%S")
    metrics_available = True
    status_available = True
    metrics_error = ""
    status_error = ""

    try:
        _poll_rates()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        metrics_available = False
        metrics_error = str(error)

    try:
        status_payload = _fetch_json("/status")
        backends = status_payload["backends"]
    except (
        httpx.RequestError,
        httpx.HTTPStatusError,
        KeyError,
        ValueError,
    ) as error:
        status_available = False
        status_error = str(error)
        backends = []

    gateway_online = (
        metrics_available
        and status_available
        and st.session_state.get("dashboard_control_online", False)
    )
    title_column, gateway_column = st.columns([5, 1])
    with title_column:
        st.title("ChaosProxy")
        st.caption("Adaptive fault-injection gateway with autonomous failover")
    with gateway_column:
        pill_class = "gateway-online" if gateway_online else "gateway-offline"
        gateway_state = "ONLINE" if gateway_online else "OFFLINE"
        st.markdown(
            f'<div style="text-align:right;padding-top:0.8rem">'
            f'<span class="gateway-pill {pill_class}">'
            f'<span class="backend-dot {"dot-healthy" if gateway_online else "dot-unhealthy"}"></span>'
            f'Gateway {gateway_state}</span></div>',
            unsafe_allow_html=True,
        )

    if not metrics_available:
        st.error(f"Could not load Prometheus metrics: {metrics_error}")
    if not status_available:
        st.error(f"Could not load backend status: {status_error}")

    rules = st.session_state.get("dashboard_active_rules", {})
    active_fault_count = sum(
        (
            rules.get("latency_ms", 0) > 0,
            rules.get("error_rate", 0) > 0,
            rules.get("drop_rate", 0) > 0,
        )
    )
    healthy_count = sum(bool(backend.get("healthy")) for backend in backends)
    healthy_display = f"{healthy_count}/{BACKEND_COUNT}"
    history = st.session_state.get("dashboard_rate_history", [])
    latest = history[-1] if history else {}

    st.subheader("Live overview")
    kpi_columns = st.columns(4)
    kpi_columns[0].metric(
        "Request rate",
        f"{latest.get('requests_per_second', 0.0):.2f} req/s",
    )
    kpi_columns[1].metric(
        "Error rate",
        f"{latest.get('error_percent', 0.0):.1f}%",
    )
    kpi_columns[2].metric("Healthy backends", healthy_display)
    kpi_columns[3].metric("Enabled fault rules", str(active_fault_count))

    st.subheader("Backend health")
    backend_columns = st.columns(3)
    for index, column in enumerate(backend_columns):
        with column:
            backend = backends[index] if index < len(backends) else None
            _render_backend_card(backend)

    st.subheader("Traffic over the last 60 seconds")
    if history:
        chart_data = pd.DataFrame(history).set_index("time")
        chart_columns = st.columns(2)
        with chart_columns[0]:
            request_chart = (
                alt.Chart(chart_data.reset_index())
                .mark_area(
                    line={"color": "#00D4AA"},
                    color="#00D4AA",
                    opacity=0.35,
                )
                .encode(
                    x=alt.X("time:T", title=None),
                    y=alt.Y(
                        "requests_per_second:Q",
                        title="Requests per second",
                        scale=alt.Scale(domainMin=0, zero=True),
                        axis=alt.Axis(format=".1f"),
                    ),
                    tooltip=[
                        alt.Tooltip("time:T", title="Time"),
                        alt.Tooltip(
                            "requests_per_second:Q",
                            title="Requests/s",
                            format=".2f",
                        ),
                    ],
                )
                .properties(height=330)
            )
            st.altair_chart(request_chart, use_container_width=True)
        with chart_columns[1]:
            error_chart = (
                alt.Chart(chart_data.reset_index())
                .mark_area(
                    line={"color": "#ff6b75"},
                    color="#ff6b75",
                    opacity=0.28,
                )
                .encode(
                    x=alt.X("time:T", title=None),
                    y=alt.Y(
                        "error_percent:Q",
                        title="Errors (%)",
                        scale=alt.Scale(domainMin=0, zero=True),
                        axis=alt.Axis(format=".1f"),
                    ),
                    tooltip=[
                        alt.Tooltip("time:T", title="Time"),
                        alt.Tooltip(
                            "error_percent:Q",
                            title="Errors (%)",
                            format=".1f",
                        ),
                    ],
                )
                .properties(height=330)
            )
            st.altair_chart(error_chart, use_container_width=True)

        latency_points = chart_data.dropna(subset=["latency_ms"])
        if not latency_points.empty:
            latency_chart = (
                alt.Chart(latency_points.reset_index())
                .mark_area(
                    line={"color": "#8b9cff"},
                    color="#8b9cff",
                    opacity=0.28,
                )
                .encode(
                    x=alt.X("time:T", title=None),
                    y=alt.Y(
                        "latency_ms:Q",
                        title="Mean request latency (ms)",
                        scale=alt.Scale(domainMin=0, zero=True),
                        axis=alt.Axis(format=".1f"),
                    ),
                    tooltip=[
                        alt.Tooltip("time:T", title="Time"),
                        alt.Tooltip(
                            "latency_ms:Q",
                            title="Latency (ms)",
                            format=".1f",
                        ),
                    ],
                )
                .properties(height=280)
            )
            st.subheader("Mean request latency")
            st.altair_chart(latency_chart, use_container_width=True)
        st.caption(
            "Error rate counts HTTP 5xx responses and the proxy's 444 "
            "dropped-connection marker."
        )
    else:
        st.info("Traffic rates will appear after the first metrics poll.")

    st.caption(f"Last updated {updated_at}")


render_sidebar()
render_dashboard()

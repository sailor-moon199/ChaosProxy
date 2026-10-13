import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from proxy.faults import FaultRules

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class ConfigError(ValueError):
    """Raised when the proxy configuration cannot be loaded or validated."""


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int
    cooldown_seconds: float


@dataclass
class ProxyConfig:
    backends: list[str]
    listen_port: int
    max_body_bytes: int
    faults: FaultRules
    circuit_breaker: CircuitBreakerConfig


def _require_mapping(value: object, section: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ConfigError(f"'{section}' must be a YAML mapping.")
    return value


def _require_exact_keys(
    value: dict[str, object],
    expected: set[str],
    section: str,
) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing keys: {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unknown keys: {', '.join(sorted(unknown))}")
        raise ConfigError(
            f"Invalid '{section}' configuration ({'; '.join(details)})."
        )


def _validate_backend_urls(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError("'backends' must be a non-empty list of HTTP URLs.")

    urls: list[str] = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, str)
            or not item
            or any(char.isspace() for char in item)
        ):
            raise ConfigError(f"'backends[{index}]' must be a valid HTTP URL.")
        try:
            parsed = urlsplit(item)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname is not None
            )
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ConfigError(f"'backends[{index}]' must be a valid HTTP URL.")
        urls.append(item.rstrip("/"))
    return urls


def _validate_port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ConfigError("'listen_port' must be an integer between 1 and 65535.")
    return value


def _validate_max_body_bytes(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ConfigError("'max_body_bytes' must be a positive integer.")
    return value


def _validate_fault_rules(value: object) -> FaultRules:
    rules = _require_mapping(value, "faults")
    _require_exact_keys(rules, {"latency_ms", "error_rate", "drop_rate"}, "faults")

    latency_ms = rules["latency_ms"]
    if type(latency_ms) is not int or latency_ms < 0:
        raise ConfigError("'faults.latency_ms' must be a non-negative integer.")

    rates: dict[str, float] = {}
    for name in ("error_rate", "drop_rate"):
        rate = rules[name]
        if (
            isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or rate < 0
            or rate > 1
            or (isinstance(rate, float) and not math.isfinite(rate))
        ):
            raise ConfigError(f"'faults.{name}' must be a number between 0 and 1.")
        rates[name] = float(rate)

    return FaultRules(
        latency_ms=latency_ms,
        error_rate=rates["error_rate"],
        drop_rate=rates["drop_rate"],
    )


def _validate_circuit_breaker(value: object) -> CircuitBreakerConfig:
    settings = _require_mapping(value, "circuit_breaker")
    _require_exact_keys(
        settings,
        {"failure_threshold", "cooldown_seconds"},
        "circuit_breaker",
    )

    threshold = settings["failure_threshold"]
    if type(threshold) is not int or threshold < 1:
        raise ConfigError(
            "'circuit_breaker.failure_threshold' must be a positive integer."
        )

    cooldown = settings["cooldown_seconds"]
    if (
        isinstance(cooldown, bool)
        or not isinstance(cooldown, (int, float))
        or not math.isfinite(cooldown)
        or cooldown <= 0
    ):
        raise ConfigError(
            "'circuit_breaker.cooldown_seconds' must be a positive number."
        )

    return CircuitBreakerConfig(
        failure_threshold=threshold,
        cooldown_seconds=float(cooldown),
    )


def load_config(path: str | Path | None = None) -> ProxyConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        with config_path.open(encoding="utf-8") as config_file:
            raw_config = yaml.safe_load(config_file)
    except FileNotFoundError as error:
        raise ConfigError(
            f"Configuration file not found: {config_path}"
        ) from error
    except yaml.YAMLError as error:
        raise ConfigError(
            f"Invalid YAML in configuration file {config_path}: {error}"
        ) from error
    except OSError as error:
        raise ConfigError(
            f"Cannot read configuration file {config_path}: {error}"
        ) from error

    config = _require_mapping(raw_config, "root")
    _require_exact_keys(
        config,
        {
            "backends",
            "listen_port",
            "max_body_bytes",
            "faults",
            "circuit_breaker",
        },
        "root",
    )
    return ProxyConfig(
        backends=_validate_backend_urls(config["backends"]),
        listen_port=_validate_port(config["listen_port"]),
        max_body_bytes=_validate_max_body_bytes(config["max_body_bytes"]),
        faults=_validate_fault_rules(config["faults"]),
        circuit_breaker=_validate_circuit_breaker(config["circuit_breaker"]),
    )
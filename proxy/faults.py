from dataclasses import dataclass
from typing import Literal

from proxy.metrics import FAULTS_INJECTED_TOTAL


@dataclass
class FaultRules:
    latency_ms: int = 0
    error_rate: float = 0.0
    drop_rate: float = 0.0


fault_rules = FaultRules()


def record_fault_injected(fault_type: Literal["latency", "error", "drop"]) -> None:
    FAULTS_INJECTED_TOTAL.labels(type=fault_type).inc()
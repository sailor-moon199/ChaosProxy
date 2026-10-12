from dataclasses import dataclass


@dataclass
class FaultRules:
    latency_ms: int = 0
    error_rate: float = 0.0
    drop_rate: float = 0.0


fault_rules = FaultRules()
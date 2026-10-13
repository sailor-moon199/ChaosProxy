import tempfile
import unittest
from pathlib import Path

from proxy.config import ConfigError, load_config


class CircuitBreakerConfigTests(unittest.TestCase):
    def test_default_configuration_has_expected_breaker_values(self) -> None:
        config = load_config()
        self.assertEqual(config.circuit_breaker.failure_threshold, 5)
        self.assertEqual(config.circuit_breaker.cooldown_seconds, 30)

    def test_invalid_breaker_threshold_is_rejected(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        config_text = config_path.read_text(encoding="utf-8").replace(
            "failure_threshold: 5",
            "failure_threshold: 0",
        )
        with tempfile.TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "config.yaml"
            invalid_path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "failure_threshold"):
                load_config(invalid_path)

    def test_invalid_breaker_cooldown_is_rejected(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        config_text = config_path.read_text(encoding="utf-8").replace(
            "cooldown_seconds: 30",
            "cooldown_seconds: .nan",
        )
        with tempfile.TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "config.yaml"
            invalid_path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "cooldown_seconds"):
                load_config(invalid_path)

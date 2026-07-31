from __future__ import annotations

import unittest

from research_lab.protocol import ExperimentSpec, SpecError, sha256_json


class ProtocolTests(unittest.TestCase):
    def test_hash_is_order_independent(self) -> None:
        self.assertEqual(
            sha256_json({"a": 1, "b": [2, 3]}),
            sha256_json({"b": [2, 3], "a": 1}),
        )

    def test_matrix_expansion_is_deterministic(self) -> None:
        spec = ExperimentSpec.from_dict(
            {
                "name": "grid",
                "stage": "search",
                "command": ["echo", "{a}", "{b}"],
                "matrix": {"b": [2, 3], "a": [1, 4]},
                "protocol": {
                    "research_question": "q",
                    "primary_metric": "m",
                    "initialization_mode": "from_scratch",
                    "training_scope": "full_model",
                },
            }
        )
        self.assertEqual(
            spec.expand_trials(),
            [
                {"a": 1, "b": 2},
                {"a": 1, "b": 3},
                {"a": 4, "b": 2},
                {"a": 4, "b": 3},
            ],
        )

    def test_required_protocol_fields(self) -> None:
        with self.assertRaises(SpecError):
            ExperimentSpec.from_dict(
                {
                    "name": "bad",
                    "stage": "pilot",
                    "command": ["echo"],
                    "protocol": {},
                }
            )

    def test_unknown_command_placeholder_fails_during_validation(self) -> None:
        with self.assertRaises(SpecError):
            ExperimentSpec.from_dict(
                {
                    "name": "bad-placeholder",
                    "stage": "pilot",
                    "command": ["echo", "{missing}"],
                    "matrix": {"x": [1]},
                    "protocol": {
                        "research_question": "q",
                        "primary_metric": "m",
                        "initialization_mode": "from_scratch",
                        "training_scope": "full_model",
                    },
                }
            )

    def test_environment_values_do_not_enter_protocol_hash_document(self) -> None:
        base = {
            "name": "env",
            "stage": "pilot",
            "command": ["echo", "ok"],
            "protocol": {
                "research_question": "q",
                "primary_metric": "m",
                "initialization_mode": "from_scratch",
                "training_scope": "full_model",
            },
        }
        one = ExperimentSpec.from_dict({**base, "environment": {"RUN_LABEL": "one"}})
        two = ExperimentSpec.from_dict({**base, "environment": {"RUN_LABEL": "two"}})
        self.assertEqual(one.protocol_hash, two.protocol_hash)
        self.assertEqual(one.protocol_document["environment_keys"], ["RUN_LABEL"])
        self.assertNotIn("environment_values", one.protocol_document)

    def test_sensitive_environment_keys_are_rejected(self) -> None:
        raw = {
            "name": "sensitive-env",
            "stage": "pilot",
            "command": ["echo", "ok"],
            "environment": {"API_TOKEN": "not-recorded"},
            "protocol": {
                "research_question": "q",
                "primary_metric": "m",
                "initialization_mode": "from_scratch",
                "training_scope": "full_model",
            },
        }
        with self.assertRaisesRegex(SpecError, "sensitive keys"):
            ExperimentSpec.from_dict(raw)


if __name__ == "__main__":
    unittest.main()

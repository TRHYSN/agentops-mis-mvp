from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_template_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_template_manifest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TemplateManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        cls.instance = json.loads((ROOT / "examples" / "building-wireframe-lab.instance.json").read_text(encoding="utf-8"))

    def test_reference_manifest_and_instance_pass(self) -> None:
        self.assertEqual(MODULE.validate(self.manifest, self.instance), [])

    def test_external_only_connector_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["connectors"][0]["summary_projection"] = False
        errors = MODULE.validate(candidate, self.instance)
        self.assertTrue(any("summary projection" in error for error in errors))

    def test_route_outside_workspace_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["navigation"][0]["route"] = "https://mlflow.example/runs"
        errors = MODULE.validate(candidate, self.instance)
        self.assertTrue(any("not embedded" in error for error in errors))

    def test_credential_key_is_rejected(self) -> None:
        instance = copy.deepcopy(self.instance)
        instance["connectors"]["github"]["token"] = "do-not-store"
        errors = MODULE.validate(self.manifest, instance)
        self.assertTrue(any("forbidden credential keys" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

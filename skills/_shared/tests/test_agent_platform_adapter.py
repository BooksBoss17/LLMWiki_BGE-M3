import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / "scripts/agent_platform_adapter.py"
SPEC = importlib.util.spec_from_file_location("agent_platform_adapter_v3", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class StaticPolicyTests(unittest.TestCase):
    def test_fixed_codex_mapping(self) -> None:
        self.assertEqual({
            "bounded": ("gpt-5.6-luna", "xhigh"),
            "ambiguous": ("gpt-5.6-sol", "medium"),
            "complex": ("gpt-5.6-sol", "high"),
            "critical": ("gpt-5.6-sol", "xhigh"),
        }, {key: (value["model"], value["reasoning"]) for key, value in MODULE.CODEX_POLICY.items()})

    def test_lookup_creates_no_attestation_or_runtime_proof(self) -> None:
        value = MODULE.resolve_runtime("complex")
        self.assertEqual("gpt-5.6-sol", value["model"])
        self.assertNotIn("attestation", value)
        self.assertNotIn("observed", value)


if __name__ == "__main__":
    unittest.main()

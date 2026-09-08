"""Keep frontend Turbo metadata aligned with the authoritative native policy."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workflow_adapter_turbo_policy", ROOT / "nodes/minimax_h3_turbo_profile.py")
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)


class WorkflowAdapterParityTests(unittest.TestCase):
    def test_turbo_cross_language_matrix_and_rejections(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for workflow adapter parity")
        source = (ROOT / "web/js/workflow-adapter.js").read_text(encoding="utf-8")
        source = "\n".join(line for line in source.splitlines() if not line.startswith(("import ", "export {")))
        cases = [[mode, width, height, preset]
                 for mode in ("t2va", "i2va", "fl2va", "l2va", "ref2va", " REF2VA ")
                 for width, height in ((1344, 768), (768, 1344), (1024, 576), (1344.9, 768.4))
                 for preset in ("auto_quality", "fast_4step", " AUTO_QUALITY ")]
        cases += [["t2va", "1344", "768", "auto_quality"],
                  ["bad", 1344, 768, "auto_quality"], ["t2va", 0, 768, "auto_quality"],
                  ["t2va", 1344, -1, "auto_quality"], ["t2va", 1344, 768, "bad"],
                  ["t2va", "1344.0", 768, "auto_quality"], ["t2va", None, 768, "auto_quality"]]
        script = source + "\nconst cases = " + json.dumps(cases) + ";\n" + """
console.log(JSON.stringify({version:TURBO_POLICY_VERSION, results:cases.map(args=>{
  try{return selectTurboProfile(...args);}catch{return {invalid:true};}
})}));
"""
        result = subprocess.run([node, "--input-type=module"], input=script, text=True, capture_output=True, check=True)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["version"], policy.TURBO_POLICY_VERSION)
        expected = []
        for args in cases:
            try:
                expected.append(asdict(policy.select_turbo_profile(*args)))
            except (ValueError, TypeError):
                expected.append({"invalid": True})
        self.assertEqual(actual["results"], expected)


if __name__ == "__main__":
    unittest.main()

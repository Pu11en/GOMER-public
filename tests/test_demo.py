import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DemoTest(unittest.TestCase):
    def test_demo_regenerates_safe_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run([
                sys.executable, str(ROOT / "examples/demo-case/demo.py"),
                "--output", temporary, "--check",
            ], cwd=ROOT, check=True)
            packet = json.loads((Path(temporary) / "packet.json").read_text())
            report = (Path(temporary) / "report.html").read_text()
            self.assertEqual(packet["as_of_date"], "2030-01-15")
            self.assertIn("Before and now", report)
            self.assertNotIn("private patient", report.casefold())


if __name__ == "__main__":
    unittest.main()

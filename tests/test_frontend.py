"""Запускае тэсты інтэрфейсу (tests/js, Node.js + jsdom) з `python -m unittest`.

Калі няма Node.js або не ўсталяваны jsdom (`npm install`), тэст прапускаецца.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestFrontend(unittest.TestCase):
    def test_js_suite(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("няма Node.js")
        if not (ROOT / "node_modules" / "jsdom").is_dir():
            self.skipTest("няма jsdom: запусціце npm install")
        tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
        proc = subprocess.run([node, "--test", *tests], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()

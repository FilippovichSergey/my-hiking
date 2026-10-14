"""Запускае тэсты інтэрфейсу (tests/js, Node.js + jsdom) з `python -m unittest`.

Калі няма Node.js або не ўсталяваны jsdom (`npm install`), тэст прапускаецца — апрача запуску з зменнай
асяроддзя REQUIRE_JS_TESTS=1 (тэсты на GitHub): там прапуск схаваў бы зламаны інтэрфейс, таму гэта памылка.
"""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestFrontend(unittest.TestCase):
    def test_js_suite(self):
        node = shutil.which("node")
        missing = "няма Node.js" if not node else None if (ROOT / "node_modules" / "jsdom").is_dir() else "няма jsdom: запусціце npm install"
        if missing and os.environ.get("REQUIRE_JS_TESTS") == "1":
            self.fail(missing)
        if missing:
            self.skipTest(missing)
        tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
        proc = subprocess.run([node, "--test", *tests], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()

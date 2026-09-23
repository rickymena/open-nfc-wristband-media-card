"""Actually execute the web UI's JavaScript.

The other web tests check structure: that IDs exist and that the page only
posts to implemented actions. They cannot catch a function that is referenced
but never defined -- which happened, and left the page frozen on its
placeholder markup. That failure looks exactly like the server not sending
events, so it is worth a test that runs the script for real.

Skipped when node is unavailable.
"""

import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).parent.parent
STATIC = ROOT / "src" / "wristband" / "web" / "static"
HARNESS = pathlib.Path(__file__).parent / "dom_harness.mjs"


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class TestAppJsRuns(unittest.TestCase):
    def test_loads_and_renders_every_state(self):
        result = subprocess.run(
            ["node", str(HARNESS), str(STATIC / "index.html"), str(STATIC / "app.js")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"web UI JavaScript failed:\n{result.stderr or result.stdout}",
        )

    def test_syntax(self):
        result = subprocess.run(
            ["node", "--check", str(STATIC / "app.js")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

"""Web UI asset tests.

The server module imports pyscard, so it cannot be imported here. These
tests cover what can go wrong without a reader: assets missing from the
package, and the page referencing files that do not exist.
"""

import pathlib
import re
import unittest

STATIC = pathlib.Path(__file__).parent.parent / "src" / "wristband" / "web" / "static"


class TestStaticAssets(unittest.TestCase):
    def test_assets_exist(self):
        for name in ("index.html", "style.css", "app.js"):
            with self.subTest(name=name):
                self.assertTrue((STATIC / name).is_file(), f"{name} missing")

    def test_page_references_resolve(self):
        html = (STATIC / "index.html").read_text()
        refs = re.findall(r'(?:href|src)="/static/([^"]+)"', html)
        self.assertTrue(refs, "page references no static assets")
        for ref in refs:
            with self.subTest(ref=ref):
                self.assertTrue((STATIC / ref).is_file(), f"/static/{ref} does not exist")

    def test_every_element_js_looks_up_exists_in_the_page(self):
        html = (STATIC / "index.html").read_text()
        js = (STATIC / "app.js").read_text()
        ids = set(re.findall(r'id="([^"]+)"', html))
        for wanted in re.findall(r'\$\("([^"]+)"\)', js):
            with self.subTest(id=wanted):
                self.assertIn(wanted, ids, f'app.js reads #{wanted}, not in index.html')

    def test_js_posts_only_to_implemented_actions(self):
        js = (STATIC / "app.js").read_text()
        server = (STATIC.parent / "server.py").read_text()
        # Covers both post() and postCfg().
        actions = set(re.findall(r'\bpost(?:Cfg)?\("([a-z-]+)"', js))
        self.assertTrue(actions, "no POST actions found in app.js")
        for action in sorted(actions):
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', server, f"server has no {action!r} action")

    def test_all_new_config_actions_are_wired(self):
        js = (STATIC / "app.js").read_text()
        actions = set(re.findall(r'\bpost(?:Cfg)?\("([a-z-]+)"', js))
        for expected in ("write-url", "lock", "mirror", "counter", "password"):
            with self.subTest(action=expected):
                self.assertIn(expected, actions, f"UI never posts {expected!r}")

    def test_mirror_widths_match_the_python_model(self):
        js = (STATIC / "app.js").read_text()
        ntag = (STATIC.parent.parent / "ntag.py").read_text()
        # A placeholder sized wrongly silently corrupts the URL, so the two
        # definitions of the widths must not drift apart.
        for mode, width in (("uid", 14), ("counter", 6), ("both", 21)):
            with self.subTest(mode=mode):
                self.assertRegex(js, rf"{mode}:\s*{width}")
                self.assertRegex(ntag, rf"MIRROR_{mode.upper()}:\s*{width}")

    def test_ios_warning_is_present(self):
        # The Smart Poster/iOS trap cost a real debugging cycle; the UI must
        # surface it rather than let someone rediscover it.
        html = (STATIC / "index.html").read_text()
        self.assertIn("Smart Poster", html)
        self.assertRegex(html, r"iPhone|iOS")


if __name__ == "__main__":
    unittest.main()


class TestServerProtocol(unittest.TestCase):
    """Guards the HTTP-level details that break SSE only in a browser.

    curl -N happily streams an HTTP/1.0 response, so a command-line check
    passes while EventSource buffers forever and the page never updates.
    """

    SERVER = STATIC.parent / "server.py"

    def test_http_1_1(self):
        self.assertIn('protocol_version = "HTTP/1.1"', self.SERVER.read_text())

    def test_event_stream_closes_the_connection(self):
        src = self.SERVER.read_text()
        stream = src[src.index("def _stream_events"):src.index("def do_POST")]
        self.assertIn('"Connection", "close"', stream)
        self.assertIn("self.close_connection = True", stream)
        self.assertNotIn("keep-alive", stream)

    def test_event_stream_declares_the_right_content_type(self):
        src = self.SERVER.read_text()
        stream = src[src.index("def _stream_events"):src.index("def do_POST")]
        self.assertIn("text/event-stream", stream)

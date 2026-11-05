"""Operator console: static page integrity and the SSE snapshot stream."""
from __future__ import annotations

import http.client
import json
import threading
import unittest

from sentinel_agent.agent import Agent, AgentConfig, AgentHTTPServer
from sentinel_agent.console import console_html


class _ServerHarness:
    def __init__(self) -> None:
        self.agent = Agent(AgentConfig(stale_after=60.0, poll_interval=0.2))
        self.server = AgentHTTPServer(("127.0.0.1", 0), self.agent)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ConsolePageTests(unittest.TestCase):
    def test_page_is_static_single_file_with_version_only(self) -> None:
        html = console_html("9.9.9-test")
        self.assertIn("operator console", html)
        self.assertIn("9.9.9-test", html)
        self.assertIn("<canvas", html)
        self.assertIn("EventSource", html)
        # No dynamic data is embedded: run data reaches the page via /stream.
        self.assertNotIn("run-1", html)
        self.assertNotIn("hang-demo", html)
        # Version interpolation is HTML-escaped.
        escaped = console_html('<script>"x"</script>')
        self.assertNotIn("<script>\"x\"</script>", escaped.replace('<script>\n"use strict";', ""))
        self.assertIn("&lt;script&gt;", escaped)

    def test_root_served_with_html_content_type(self) -> None:
        harness = _ServerHarness()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
            conn.request("GET", "/")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.getheader("Content-Type"))
            body = response.read().decode("utf-8")
            self.assertIn("operator console", body)
            conn.close()
        finally:
            harness.close()

    def test_unknown_paths_still_404(self) -> None:
        harness = _ServerHarness()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
            conn.request("GET", "/console.js")
            response = conn.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            conn.close()
        finally:
            harness.close()


class StreamTests(unittest.TestCase):
    def test_sse_stream_emits_parseable_snapshots(self) -> None:
        harness = _ServerHarness()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
            conn.request("GET", "/stream")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("text/event-stream", response.getheader("Content-Type"))
            buffer = b""
            while b"\n\n" not in buffer:
                buffer += response.read(256)
            first_frame = buffer.split(b"\n\n")[0]
            self.assertTrue(first_frame.startswith(b"data: "))
            snapshot = json.loads(first_frame[len(b"data: "):])
            self.assertIn("counters", snapshot)
            self.assertIn("stale_after", snapshot)
            self.assertEqual(snapshot["stale_after"], 60.0)
            conn.close()
        finally:
            harness.close()

    def test_stream_reflects_ingested_runs(self) -> None:
        from rl_risk_replay.events import make_event

        harness = _ServerHarness()
        try:
            harness.agent.ingest(make_event("e1", "run-1", "start", 1.0, 1.0,
                                             {"task": "t", "seed": "s",
                                              "planned_steps": 10}).to_dict())
            conn = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
            conn.request("GET", "/stream")
            response = conn.getresponse()
            buffer = b""
            while b"\n\n" not in buffer:
                buffer += response.read(256)
            snapshot = json.loads(buffer.split(b"\n\n")[0][len(b"data: "):])
            self.assertIn("run-1", snapshot["runs"])
            self.assertEqual(snapshot["runs"]["run-1"]["task"], "t")
            conn.close()
        finally:
            harness.close()


if __name__ == "__main__":
    unittest.main()

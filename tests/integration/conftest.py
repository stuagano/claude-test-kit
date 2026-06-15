"""
Integration fixtures: real dependencies, set up and torn down for real.

  * db_path     : a real sqlite file in an isolated temp dir.
  * live_server : a real HTTP server bound to a real port in a background thread,
                  yielding its base URL. Torn down after the test.

These use stdlib only. In a real project you'd swap live_server for
`testcontainers` (real Postgres/Redis in Docker) or `pytest-httpserver` — the
fixture *shape* is the same: yield a connection target, clean up after.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "store.db")


def _make_handler(routes: dict[str, dict]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            if self.path in routes:
                body = json.dumps(routes[self.path]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):  # silence server logging in tests
            pass

    return Handler


@pytest.fixture
def live_server():
    """
    Start a real HTTP server. Returns a helper:
        url = live_server({"/user": {"name": "Ada"}})
    """
    servers: list[ThreadingHTTPServer] = []

    def start(routes: dict[str, dict]) -> str:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(routes))
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        host, port = srv.server_address
        return f"http://{host}:{port}"

    yield start

    for srv in servers:
        srv.shutdown()
        srv.server_close()

#!/usr/bin/env python3
"""Serve the fixtures as a fake authentik API over HTTP.

    python3 evals/mock_server.py [--port 19000] [--fixtures evals/fixtures] [--token mock-token] [--log requests.log]

Then: AUTHENTIK_HOST=http://127.0.0.1:19000 AUTHENTIK_TOKEN=mock-token python3 scripts/authentik.py info
Useful for trying the skill (or plain curl) without an authentik server. Writes are echoed, not stored.
With --log, every request is appended as "<iso time> <METHOD> <path> <status>" so a test can
prove that no mutating request was sent. --max-page caps page_size to force pagination.
The token "<token>-ro" is accepted for reads and refused for writes, like a read-only user.
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from authentik import MockBackend, ApiError  # noqa: E402

BASE = "/api/v3"


def make_handler(backend, token, log_path=None, max_page=None):
    class H(BaseHTTPRequestHandler):
        def _send(self, status, payload=None, headers=None):
            if log_path:
                with open(log_path, "a") as f:
                    f.write("%s %s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.command, self.path, status))
            raw = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _handle(self, method):
            u = urlparse(self.path)
            if not u.path.startswith(BASE + "/"):
                return self._send(404, {"detail": "Not found."})
            if not u.path.endswith("/"):
                return self._send(301, None, {"Location": u.path + "/"})
            auth = self.headers.get("Authorization") or ""
            if not auth.lower().startswith("bearer "):
                return self._send(403, {"detail": "Authentication credentials were not provided."})
            given = auth.split(" ", 1)[1]
            if given not in (token, token + "-ro"):
                return self._send(403, {"detail": "Token invalid/expired"})
            if method != "GET" and given.endswith("-ro"):
                return self._send(403, {"detail": "You do not have permission to perform this action."})
            query = {k: v[0] for k, v in parse_qs(u.query).items()}
            if max_page and "page_size" in query:
                query["page_size"] = str(min(int(query["page_size"]), max_page))
            body = None
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n))
                except json.JSONDecodeError:
                    return self._send(400, {"non_field_errors": ["JSON parse error"]})
            try:
                data = backend.request(method, u.path[len(BASE):], query, body)
            except ApiError as e:
                return self._send(e.status, e.payload)
            if data is None:
                return self._send(204)
            if u.path == BASE + "/admin/system/":  # the real endpoint echoes the request's own headers
                data = dict(data, http_headers=dict(data["http_headers"], HTTP_AUTHORIZATION=auth),
                            note="seen " + auth)
            return self._send(201 if method == "POST" else 200, data)

        def do_GET(self): self._handle("GET")
        def do_POST(self): self._handle("POST")
        def do_PUT(self): self._handle("PUT")
        def do_PATCH(self): self._handle("PATCH")
        def do_DELETE(self): self._handle("DELETE")

        def log_message(self, fmt, *args):
            pass
    return H


def serve(fixtures, token="mock-token", port=0, log_path=None, max_page=None):
    """Start the server on 127.0.0.1 and return it; port 0 picks a free one."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(MockBackend(fixtures), token, log_path, max_page))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19000)
    ap.add_argument("--fixtures", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
    ap.add_argument("--token", default="mock-token")
    ap.add_argument("--log", help="append one line per request to this file")
    ap.add_argument("--max-page", type=int, help="cap page_size to force pagination")
    a = ap.parse_args()
    srv = serve(a.fixtures, a.token, a.port, a.log, a.max_page)
    print("mock authentik API on http://127.0.0.1:%d%s (Authorization: Bearer %s)" % (a.port, BASE, a.token))
    srv.serve_forever()


if __name__ == "__main__":
    main()

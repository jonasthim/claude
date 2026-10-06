#!/usr/bin/env python3
"""Serve the fixtures as a fake Pangolin Integration API over HTTP.

    python3 evals/mock_server.py [--port 13003] [--fixtures evals/fixtures] [--key mock.key] [--log requests.log]

Then: PANGOLIN_HOST=http://127.0.0.1:13003 PANGOLIN_API_KEY=mock.key PANGOLIN_ORG=acme python3 scripts/pangolin.py info
Useful for trying the skill (or plain curl) without a Pangolin server. Writes are echoed, not stored.
With --log, every request is appended as "<iso time> <METHOD> <path> <status>" so a test can
prove that no mutating request was sent. --max-page caps the page size to force pagination.
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from pangolin import MockBackend, ApiError  # noqa: E402


def envelope(data, status=200, message="OK"):
    ok = status < 400
    out = {"data": data if ok else None, "success": ok, "error": not ok, "message": message, "status": status}
    if not ok:
        out["stack"] = None
    return out


def make_handler(backend, key, log_path=None, max_page=None):
    class H(BaseHTTPRequestHandler):
        def _send(self, status, payload, headers=None):
            if log_path:
                with open(log_path, "a") as f:
                    f.write("%s %s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.command, self.path, status))
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _handle(self, method):
            u = urlparse(self.path)
            if not u.path.startswith("/v1"):
                return self._send(404, envelope(None, 404, "The Integration API lives under /v1"))
            path = u.path[len("/v1"):]
            if path in ("", "/"):
                return self._send(200, {"message": "Healthy"})
            if path == "/redirect":
                return self._send(302, {}, {"Location": "http://203.0.113.1/v1/"})
            auth = self.headers.get("Authorization") or ""
            if not auth.startswith("Bearer "):
                return self._send(401, envelope(None, 401, "API key required"))
            if auth != "Bearer " + key:
                return self._send(401, envelope(None, 401, "Invalid API key"))
            query = {k: v[0] for k, v in parse_qs(u.query).items()}
            if max_page:
                for size in ("pageSize", "limit"):
                    if size in query:
                        query[size] = str(min(int(query[size]), max_page))
            if path.startswith("/orgs"):
                return self._send(403, envelope(None, 403, "Key does not have root access"))
            body = None
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n))
                except json.JSONDecodeError:
                    return self._send(400, envelope(None, 400, "body is not JSON"))
            try:
                data = backend.request(method, path, query, body)
                return self._send(201 if method == "PUT" else 200, envelope(data, 201 if method == "PUT" else 200))
            except ApiError as e:
                return self._send(e.status, envelope(None, e.status, e.payload.get("message", "error")))

        def do_GET(self): self._handle("GET")
        def do_POST(self): self._handle("POST")
        def do_PUT(self): self._handle("PUT")
        def do_DELETE(self): self._handle("DELETE")

        def log_message(self, fmt, *args):
            pass
    return H


def serve(fixtures, key="mock.key", port=0, log_path=None, max_page=None):
    """Start the server on 127.0.0.1 and return it; port 0 picks a free one."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(MockBackend(fixtures), key, log_path, max_page))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=13003)
    ap.add_argument("--fixtures", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
    ap.add_argument("--key", default="mock.key")
    ap.add_argument("--log", help="append one line per request to this file")
    ap.add_argument("--max-page", type=int, help="cap pageSize/limit to force pagination")
    a = ap.parse_args()
    srv = serve(a.fixtures, a.key, a.port, a.log, a.max_page)
    print("mock Pangolin Integration API on http://127.0.0.1:%d/v1 (Authorization: Bearer %s)" % (a.port, a.key))
    srv.serve_forever()


if __name__ == "__main__":
    main()

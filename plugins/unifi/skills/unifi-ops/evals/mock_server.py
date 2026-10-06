#!/usr/bin/env python3
"""Serve the fixtures as a fake UniFi Network Integration API over HTTP.

    python3 evals/mock_server.py [--port 18443] [--fixtures evals/fixtures] [--key mock-key] [--log requests.log]

Then: UNIFI_HOST=http://127.0.0.1:18443 UNIFI_API_KEY=mock-key python3 scripts/unifi.py info
Useful for trying the skill (or plain curl) without a console. Writes are echoed, not stored.
With --log, every request is appended as "<iso time> <METHOD> <path> <status>" so a test can
prove that no mutating request was sent.
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from unifi import MockBackend, ApiError  # noqa: E402

PREFIX = "/proxy/network/integration"


def make_handler(backend, key, log_path=None):
    class H(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            if log_path:
                with open(log_path, "a") as f:
                    f.write("%s %s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.command, self.path, status))
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _handle(self, method):
            u = urlparse(self.path)
            if not u.path.startswith(PREFIX + "/v1"):
                return self._send(404, {"code": "NOT_FOUND", "status": "NOT_FOUND",
                                        "message": "Integration API lives under %s/v1" % PREFIX})
            if self.headers.get("X-API-Key") != key and self.headers.get("X-API-KEY") != key:
                return self._send(401, {"code": "UNAUTHORIZED", "status": "UNAUTHORIZED",
                                        "message": "Missing or invalid X-API-Key header"})
            path = u.path[len(PREFIX):]
            query = {k: v[0] for k, v in parse_qs(u.query).items()}
            body = None
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n))
                except json.JSONDecodeError:
                    return self._send(400, {"code": "INVALID_BODY", "status": "BAD_REQUEST", "message": "body is not JSON"})
            if method in ("POST", "PUT", "PATCH") and body is None:
                return self._send(400, {"code": "INVALID_BODY", "status": "BAD_REQUEST", "message": "JSON body required"})
            try:
                return self._send(200, backend.request(method, path, query, body))
            except ApiError as e:
                payload = dict(e.payload)
                payload.setdefault("status", "NOT_FOUND" if e.status == 404 else "BAD_REQUEST")
                return self._send(e.status, payload)

        def do_GET(self): self._handle("GET")
        def do_POST(self): self._handle("POST")
        def do_PUT(self): self._handle("PUT")
        def do_PATCH(self): self._handle("PATCH")
        def do_DELETE(self): self._handle("DELETE")

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, self.path))
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18443)
    ap.add_argument("--fixtures", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
    ap.add_argument("--key", default="mock-key")
    ap.add_argument("--log", help="append one line per request to this file")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(MockBackend(a.fixtures), a.key, a.log))
    print("mock UniFi Integration API on http://127.0.0.1:%d%s/v1 (X-API-Key: %s)" % (a.port, PREFIX, a.key))
    srv.serve_forever()


if __name__ == "__main__":
    main()

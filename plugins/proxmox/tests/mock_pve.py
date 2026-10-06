#!/usr/bin/env python3
"""Minimal Proxmox VE API mock for script tests (Python 3 stdlib only).

Usage: python3 -I tests/mock_pve.py --port 0 [--fixtures tests/fixtures/routes.json]

Prints "PORT=<n>" on stdout once listening, then serves until SIGTERM/SIGINT.

Behaviour:
  - Token auth: header "Authorization: PVEAPIToken=test@pve!ci=0123-secret".
    Anything else -> 401 {"data":null,"message":"authentication failure"}.
  - Only /api2/json/ paths are served; GET/DELETE with a body -> 501
    "no body allowed"; unknown routes -> 501 "no such uri".
  - Routes come from "<METHOD> <path>" keys in the fixtures file:
    {"status": 200, "body": <data>} or {"status": 4xx, "message": "...", "errors": {...}}.
  - Stateful task handlers for /nodes/<node>/tasks/<upid>/status and /log.
  - Introspection without auth: GET /__mock/last, GET /__mock/calls,
    POST /__mock/reset.
Error bodies follow the PVE envelope {"data":null,"message":"...","errors":{...}}.
"""

import argparse
import json
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN_HEADER = "PVEAPIToken=test@pve!ci=0123-secret"
API_PREFIX = "/api2/json"
UPID_RE = re.compile(
    r"^UPID:[^:]+:[0-9A-Fa-f]{8}:[0-9A-Fa-f]+:[0-9A-Fa-f]{8}:[^:]*:[^:]*:[^:]*:$"
)
TASK_RE = re.compile(r"^/nodes/([^/]+)/tasks/([^/]+)/(status|log)$")

LOCK = threading.Lock()
STATE = {"calls": [], "task_polls": {}}
ROUTES = {}


def envelope_error(message, errors=None):
    return {"data": None, "message": message, "errors": errors or {}}


def load_routes(path):
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    routes = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        method, _, route = key.partition(" ")
        route = route.strip()
        if route.startswith(API_PREFIX):
            route = route[len(API_PREFIX):]
        route = "/" + route.strip("/")
        routes[(method.upper(), route)] = value
    return routes


def task_fields(upid):
    parts = upid.split(":")
    if len(parts) < 8:
        return None
    return {"node": parts[1], "type": parts[5], "id": parts[6], "user": parts[7]}


def task_status(upid):
    """Return (http_status, data) for a task status request."""
    if not UPID_RE.match(upid):
        return 500, None
    fields = task_fields(upid)
    key = fields["type"] + ":" + fields["id"]
    with LOCK:
        polls = STATE["task_polls"].get(upid, 0) + 1
        STATE["task_polls"][upid] = polls
    base = {
        "upid": upid,
        "node": fields["node"],
        "type": fields["type"],
        "id": fields["id"],
        "user": fields["user"],
        "pid": 1234,
        "pstart": 1,
        "starttime": 1726000000,
    }
    if fields["type"] == "qmforever":
        return 200, dict(base, status="running")
    if key == "qmstart:100":
        if polls <= 2:
            return 200, dict(base, status="running")
        return 200, dict(base, status="stopped", exitstatus="OK")
    if key == "qmstop:101":
        return 200, dict(base, status="stopped",
                         exitstatus="command 'kill' failed: exit code 1")
    if key == "vzdump:102":
        return 200, dict(base, status="stopped", exitstatus="WARNINGS: 1")
    if key == "vzdump:103":
        return 200, dict(base, status="stopped", exitstatus="job errors")
    return 200, dict(base, status="stopped", exitstatus="OK")


def task_log(upid, query):
    if not UPID_RE.match(upid):
        return 500, None
    fields = task_fields(upid)
    key = fields["type"] + ":" + fields["id"]
    if key == "qmstop:101":
        last = "TASK ERROR: command 'kill' failed: exit code 1"
    elif key == "vzdump:102":
        last = "TASK WARNINGS: 1"
    elif key == "vzdump:103":
        last = "TASK ERROR: job errors"
    else:
        last = "TASK OK"
    lines = [{"n": 1, "t": "starting task"}, {"n": 2, "t": last}]
    try:
        start = int(query.get("start", ["0"])[0])
        limit = int(query.get("limit", ["50"])[0])
    except ValueError:
        start, limit = 0, 50
    start = max(start, 0)
    limit = max(limit, 0)
    return 200, lines[start:start + limit]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pve-api-mock/1.0"

    def log_message(self, fmt, *args):  # silence default logging
        return

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def record(self, method, path, query, raw_body, form):
        headers = {}
        for name, value in self.headers.items():
            if name.lower() == "authorization":
                value = "PVEAPIToken=<masked>"
            headers[name] = value
        entry = {
            "method": method,
            "path": path,
            "query": {k: (v[0] if len(v) == 1 else v) for k, v in query.items()},
            "headers": headers,
            "body": raw_body.decode("utf-8", "replace"),
            "form": form,
        }
        with LOCK:
            STATE["calls"].append(entry)
        return entry

    def parse_form(self, raw_body):
        ctype = (self.headers.get("Content-Type") or "").lower()
        if not raw_body:
            return {}
        if "application/json" in ctype:
            try:
                data = json.loads(raw_body.decode("utf-8"))
                return data if isinstance(data, dict) else {"_json": data}
            except ValueError:
                return {"_invalid_json": True}
        parsed = parse_qs(raw_body.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}

    def handle_any(self, method):
        split = urlsplit(self.path)
        path = split.path
        query = parse_qs(split.query, keep_blank_values=True)
        raw_body = self.read_body()

        # Introspection endpoints (no auth, outside the API prefix).
        if path.startswith("/__mock/"):
            return self.handle_mock(method, path)

        form = self.parse_form(raw_body)
        self.record(method, path, query, raw_body, form)

        if not path.startswith(API_PREFIX + "/") and path != API_PREFIX:
            return self.send_json(501, envelope_error("no such uri"))
        api_path = path[len(API_PREFIX):] or "/"
        api_path = "/" + api_path.strip("/")

        if self.headers.get("Authorization") != TOKEN_HEADER:
            return self.send_json(401, envelope_error("authentication failure"))

        if method in ("GET", "DELETE") and raw_body:
            return self.send_json(501, envelope_error("no body allowed"))

        match = TASK_RE.match(api_path)
        if match and method == "GET":
            node, upid, kind = match.groups()
            upid = unquote(upid)
            if kind == "status":
                status, data = task_status(upid)
            else:
                status, data = task_log(upid, query)
            if status != 200:
                return self.send_json(500, envelope_error("no such task '%s'" % upid))
            return self.send_json(200, {"data": data})

        route = ROUTES.get((method, api_path))
        if route is None:
            return self.send_json(501, envelope_error("no such uri"))
        status = int(route.get("status", 200))
        if 200 <= status < 300:
            return self.send_json(status, {"data": route.get("body")})
        return self.send_json(status, envelope_error(route.get("message", "error"),
                                                    route.get("errors")))

    def handle_mock(self, method, path):
        if path == "/__mock/last" and method == "GET":
            with LOCK:
                last = STATE["calls"][-1] if STATE["calls"] else None
            return self.send_json(200, last)
        if path == "/__mock/calls" and method == "GET":
            with LOCK:
                calls = list(STATE["calls"])
            return self.send_json(200, calls)
        if path == "/__mock/reset" and method == "POST":
            with LOCK:
                STATE["calls"] = []
                STATE["task_polls"] = {}
            return self.send_json(200, {"reset": True})
        return self.send_json(404, {"error": "unknown mock endpoint"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_DELETE(self):
        self.handle_any("DELETE")


def main():
    parser = argparse.ArgumentParser(description="Proxmox VE API mock")
    parser.add_argument("--port", type=int, default=0, help="listen port (0 = random)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    default_fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "fixtures", "routes.json")
    parser.add_argument("--fixtures", default=default_fixtures, help="routes JSON file")
    args = parser.parse_args()

    global ROUTES
    ROUTES = load_routes(args.fixtures)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    sys.stdout.write("PORT=%d\n" % port)
    sys.stdout.flush()

    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

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
  - In-memory state seeded from the fixtures and served by the handlers in
    DYNAMIC below (guests, their config/status/snapshots, HA resources and
    rules, /cluster/resources and /cluster/nextid). Fixture entries stay the
    base data; writes (create, start/stop, snapshot, HA) change the state and
    return a UPID that resolves to stopped/OK. Where a fixture already holds a
    UPID for the same path and guest (e.g. qmstart:100) that UPID is reused so
    its special task behaviour is kept.
  - Introspection without auth: GET /__mock/last, GET /__mock/calls,
    POST /__mock/reset (clears calls, task polls and the in-memory state).
Error bodies follow the PVE envelope {"data":null,"message":"...","errors":{...}}.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN_HEADER = "PVEAPIToken=test@pve!ci=0123-secret"
TOKEN_USER = TOKEN_HEADER[len("PVEAPIToken="):].rsplit("=", 1)[0]
API_PREFIX = "/api2/json"
UPID_RE = re.compile(
    r"^UPID:[^:]+:[0-9A-Fa-f]{8}:[0-9A-Fa-f]+:[0-9A-Fa-f]{8}:[^:]*:[^:]*:[^:]*:$"
)
TASK_RE = re.compile(r"^/nodes/([^/]+)/tasks/([^/]+)/(status|log)$")
MIB = 1048576
PARAM_ERROR = "Parameter verification failed."
MISSING = "property is missing and it is not optional"
# Create-only parameters that never end up in a guest's config.
CREATE_ONLY = {"vmid", "ostemplate", "archive", "storage", "force", "unique", "restore",
               "pool", "start", "bwlimit", "password", "ssh-public-keys",
               "live-restore", "ha-managed"}
HA_STATES = ("started", "stopped", "enabled", "disabled", "ignored")
RESOURCE_TYPES = ("vm", "storage", "node", "sdn")

LOCK = threading.RLock()
STATE = {"calls": [], "task_polls": {}, "task_seq": 0,
         "guests": {}, "ha_resources": {}, "ha_rules": {}}
ROUTES = {}


class Fallback(Exception):
    """Raised by a dynamic handler to answer from the fixtures instead."""


class ApiError(Exception):
    """An error response in the PVE envelope."""

    def __init__(self, status, message, errors=None):
        Exception.__init__(self, message)
        self.status = status
        self.message = message
        self.errors = errors or {}


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


def fixture_body(method, route, default=None):
    """Deep copy of a 2xx fixture body, or default when there is none."""
    route = ROUTES.get((method, route))
    if route is None or not 200 <= int(route.get("status", 200)) < 300:
        return default
    return copy.deepcopy(route.get("body"))


def digest_of(data):
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ state
def known_nodes():
    return [n["node"] for n in fixture_body("GET", "/nodes", []) if "node" in n]


def guest_sid(guest):
    return ("vm" if guest["type"] == "qemu" else "ct") + ":" + str(guest["vmid"])


def default_name(gtype, vmid):
    return "VM %d" % vmid if gtype == "qemu" else "CT%d" % vmid


def reset_state():
    """(Re)build the in-memory state from the fixtures."""
    guests = {}
    for item in fixture_body("GET", "/cluster/resources", []):
        gtype = item.get("type")
        vmid = to_int(item.get("vmid"))
        if gtype not in ("qemu", "lxc") or vmid is None:
            continue
        node = item.get("node", "")
        base = "/nodes/%s/%s/%d" % (node, gtype, vmid)
        config = fixture_body("GET", base + "/config")
        if config is None:
            config = {"cores": item.get("maxcpu", 1),
                      "memory": int(item.get("maxmem", 512 * MIB)) // MIB}
            config["name" if gtype == "qemu" else "hostname"] = item.get("name")
        digest = config.pop("digest", None) or digest_of(config)
        snapshots = [s for s in fixture_body("GET", base + "/snapshot", [])
                     if s.get("name") != "current"]
        guests[vmid] = {
            "vmid": vmid, "type": gtype, "node": node,
            "name": item.get("name") or default_name(gtype, vmid),
            "status": item.get("status", "stopped"),
            "config": config, "digest": digest, "snapshots": snapshots,
        }
    with LOCK:
        STATE["guests"] = guests
        STATE["ha_resources"] = {r["sid"]: r for r in fixture_body("GET", "/cluster/ha/resources", [])
                                 if "sid" in r}
        STATE["ha_rules"] = {r["rule"]: r for r in fixture_body("GET", "/cluster/ha/rules", [])
                             if "rule" in r}
        STATE["task_polls"] = {}
        STATE["task_seq"] = 0


def find_guest(node, gtype, vmid):
    guest = STATE["guests"].get(vmid)
    if guest is None or guest["node"] != node or guest["type"] != gtype:
        return None
    return guest


def require_guest(method, path, node, gtype, vmid):
    """The guest; falls back to a fixture for an unknown one, 500 when there is none."""
    guest = find_guest(node, gtype, vmid)
    if guest is None:
        if (method, path) in ROUTES:
            raise Fallback()
        raise ApiError(500, "Configuration file 'nodes/%s/%s/%d.conf' does not exist"
                       % (node, gtype, vmid))
    return guest


def guest_cores(guest):
    return to_int(guest["config"].get("cores"), 1)


def guest_maxmem(guest):
    return to_int(guest["config"].get("memory"), 512) * MIB


def guest_ha(guest):
    return STATE["ha_resources"].get(guest_sid(guest))


# ------------------------------------------------------------------ tasks
def task_fields(upid):
    parts = upid.split(":")
    if len(parts) < 8:
        return None
    return {"node": parts[1], "type": parts[5], "id": parts[6], "user": parts[7]}


def task_outcome(ttype, tid):
    """Final exitstatus of a task; everything not listed here ends with OK."""
    return {
        "qmstop:101": "command 'kill' failed: exit code 1",
        "vzdump:102": "WARNINGS: 1",
        "vzdump:103": "job errors",
    }.get(ttype + ":" + tid, "OK")


def task_succeeds(ttype, tid):
    outcome = task_outcome(ttype, tid)
    return outcome == "OK" or outcome.startswith("WARNINGS")


def new_upid(node, ttype, tid):
    with LOCK:
        STATE["task_seq"] += 1
        seq = STATE["task_seq"]
    return "UPID:%s:%08X:%08X:%08X:%s:%s:%s:" % (
        node, 0x1000 + seq, seq, 0x66F00100 + seq, ttype, tid, TOKEN_USER)


def route_upid(method, path, node, ttype, tid):
    """The fixture UPID for this path when it names the same task, else a new one."""
    body = fixture_body(method, path)
    if isinstance(body, str) and UPID_RE.match(body):
        fields = task_fields(body)
        if fields["type"] == ttype and fields["id"] == str(tid):
            return body
    return new_upid(node, ttype, tid)


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
    if key == "qmstart:100" and polls <= 2:
        return 200, dict(base, status="running")
    return 200, dict(base, status="stopped",
                     exitstatus=task_outcome(fields["type"], fields["id"]))


def task_log(upid, query):
    if not UPID_RE.match(upid):
        return 500, None
    fields = task_fields(upid)
    outcome = task_outcome(fields["type"], fields["id"])
    if outcome == "OK":
        last = "TASK OK"
    elif outcome.startswith("WARNINGS"):
        last = "TASK " + outcome
    else:
        last = "TASK ERROR: " + outcome
    body = ["starting task"]
    # vzdump:103 is the failed nightly job used by the evals; give it a
    # realistic per-guest log so a diagnosis can be read from the task log.
    if fields["type"] == "vzdump" and fields["id"] == "103":
        body += [
            "INFO: starting new backup job: vzdump 100 101 --storage local --mode snapshot --compress zstd --prune-backups keep-last=3",
            "INFO: Starting Backup of VM 100 (qemu)",
            "INFO: creating vzdump archive '/var/lib/vz/dump/vzdump-qemu-100-2026_10_05-02_00_01.vma.zst'",
            "INFO: Finished Backup of VM 100 (00:00:41)",
            "INFO: Starting Backup of VM 101 (qemu)",
            "INFO: creating vzdump archive '/var/lib/vz/dump/vzdump-qemu-101-2026_10_05-02_00_43.vma.zst'",
            "ERROR: Backup of VM 101 failed - unable to write to '/var/lib/vz/dump/vzdump-qemu-101-2026_10_05-02_00_43.vma.zst.dat': No space left on device",
            "INFO: Failed at 2026-10-05 02:01:40",
        ]
    body.append(last)
    lines = [{"n": i + 1, "t": t} for i, t in enumerate(body)]
    try:
        start = int(query.get("start", ["0"])[0])
        limit = int(query.get("limit", ["50"])[0])
    except ValueError:
        start, limit = 0, 50
    start = max(start, 0)
    limit = max(limit, 0)
    return 200, lines[start:start + limit]


# ------------------------------------------------------------------ dynamic handlers
# Each handler gets a Request (method, path, query, form, regex groups) and
# returns the response data (None -> null); it raises Fallback to answer from
# the fixtures instead and ApiError for an error envelope.
class Request(object):
    def __init__(self, method, path, query, form, groups):
        self.method = method
        self.path = path
        self.query = query
        self.form = form
        self.groups = groups

    def param(self, key, default=None):
        """A query parameter (which wins, as in PVE) or form field."""
        if key in self.query:
            return self.query[key][0]
        value = self.form.get(key, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value

    def has(self, key):
        return key in self.query or key in self.form


def resource_item(guest):
    """A /cluster/resources entry for a guest created at runtime."""
    item = {
        "id": "%s/%d" % (guest["type"], guest["vmid"]), "type": guest["type"],
        "vmid": guest["vmid"], "name": guest["name"], "node": guest["node"],
        "status": guest["status"], "template": 0, "cpu": 0, "maxcpu": guest_cores(guest),
        "mem": 0, "maxmem": guest_maxmem(guest), "disk": 0, "maxdisk": 0, "uptime": 0,
    }
    return item


def h_cluster_resources(req):
    wanted = req.param("type")
    if wanted is not None and wanted not in RESOURCE_TYPES:
        raise ApiError(400, PARAM_ERROR, {"type": "value '%s' does not have a value in the "
                                          "enumeration '%s'" % (wanted, ", ".join(RESOURCE_TYPES))})
    guests = STATE["guests"]
    items, seen = [], set()
    for item in fixture_body("GET", "/cluster/resources", []):
        if item.get("type") in ("qemu", "lxc"):
            guest = guests.get(to_int(item.get("vmid")))
            if guest is None:
                continue  # destroyed at runtime
            seen.add(guest["vmid"])
            item = dict(item, status=guest["status"], name=guest["name"])
            ha = guest_ha(guest)
            if ha:
                item["hastate"] = ha["state"]
        items.append(item)
    for vmid in sorted(guests):
        if vmid in seen:
            continue
        item = resource_item(guests[vmid])
        ha = guest_ha(guests[vmid])
        if ha:
            item["hastate"] = ha["state"]
        items.append(item)
    if wanted == "vm":
        items = [i for i in items if i.get("type") in ("qemu", "lxc")]
    elif wanted is not None:
        items = [i for i in items if i.get("type") == wanted]
    return items


def h_nextid(req):
    taken = set(STATE["guests"])
    if req.has("vmid"):
        vmid = to_int(req.param("vmid"))
        if vmid is None:
            raise ApiError(400, PARAM_ERROR, {"vmid": "type check ('integer') failed - got '%s'"
                                              % req.param("vmid")})
        if vmid in taken:
            raise ApiError(400, PARAM_ERROR, {"vmid": "VM %d already exists" % vmid})
        return str(vmid)
    nextid = to_int(fixture_body("GET", "/cluster/nextid"), 100)
    while nextid in taken:
        nextid += 1
    return str(nextid)


def list_item(guest):
    item = {"vmid": guest["vmid"], "name": guest["name"], "status": guest["status"],
            "cpus": guest_cores(guest), "maxmem": guest_maxmem(guest), "mem": 0,
            "maxdisk": 0, "uptime": 0}
    if guest["type"] == "lxc":
        item["type"] = "lxc"
    return item


def h_guest_list(req):
    node, gtype = req.groups
    if node not in known_nodes():
        raise Fallback()
    fixture = {}
    for item in fixture_body("GET", req.path, []):
        fixture[to_int(item.get("vmid"))] = item
    items = []
    for vmid in sorted(STATE["guests"]):
        guest = STATE["guests"][vmid]
        if guest["node"] != node or guest["type"] != gtype:
            continue
        if vmid in fixture:
            items.append(dict(fixture[vmid], status=guest["status"], name=guest["name"]))
        else:
            items.append(list_item(guest))
    return items


def h_guest_create(req):
    node, gtype = req.groups
    if node not in known_nodes():
        raise Fallback()
    if not req.has("vmid"):
        if (req.method, req.path) in ROUTES:
            raise Fallback()  # keep the fixture answer (e.g. the 400 for POST /nodes/pve1/qemu)
        raise ApiError(400, PARAM_ERROR, {"vmid": MISSING})
    vmid = to_int(req.param("vmid"))
    if vmid is None or vmid < 100:
        raise ApiError(400, PARAM_ERROR, {"vmid": "type check ('integer') failed - got '%s'"
                                          % req.param("vmid")})
    if gtype == "lxc" and not req.has("ostemplate"):
        raise ApiError(400, PARAM_ERROR, {"ostemplate": MISSING})
    if vmid in STATE["guests"]:
        raise ApiError(500, "unable to create %s %d: config file already exists"
                       % ("VM" if gtype == "qemu" else "CT", vmid))
    config = {}
    for key in list(req.query) + list(req.form):
        if key not in CREATE_ONLY and key not in config:
            config[key] = req.param(key)
    name_key = "name" if gtype == "qemu" else "hostname"
    guest = {
        "vmid": vmid, "type": gtype, "node": node,
        "name": config.get(name_key) or default_name(gtype, vmid),
        "status": "running" if str(req.param("start", "0")) == "1" else "stopped",
        "config": config, "digest": digest_of(config), "snapshots": [],
    }
    STATE["guests"][vmid] = guest
    ttype = "qmcreate" if gtype == "qemu" else "vzcreate"
    return route_upid(req.method, req.path, node, ttype, vmid)


def h_guest_destroy(req):
    node, gtype, vmid = req.groups[0], req.groups[1], int(req.groups[2])
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    ha = guest_ha(guest)
    if ha and str(req.param("purge", "0")) != "1":
        raise ApiError(500, "unable to remove %s %d - used in HA resources and purge "
                       "parameter not set." % ("VM" if gtype == "qemu" else "CT", vmid))
    if ha:
        del STATE["ha_resources"][ha["sid"]]
    del STATE["guests"][vmid]
    ttype = "qmdestroy" if gtype == "qemu" else "vzdestroy"
    return route_upid(req.method, req.path, node, ttype, vmid)


def h_config_get(req):
    node, gtype, vmid = req.groups[0], req.groups[1], int(req.groups[2])
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    return dict(guest["config"], digest=guest["digest"])


def h_config_put(req):
    node, gtype, vmid = req.groups[0], req.groups[1], int(req.groups[2])
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    if req.has("digest") and req.param("digest") != guest["digest"]:
        raise ApiError(500, "detected modified configuration - file changed by other user? Try again.")
    for key in str(req.param("delete", "")).split(","):
        guest["config"].pop(key.strip(), None)
    for key in list(req.query) + list(req.form):
        if key not in ("digest", "delete", "revert", "skiplock", "force"):
            guest["config"][key] = req.param(key)
    guest["digest"] = digest_of(guest["config"])
    name_key = "name" if gtype == "qemu" else "hostname"
    guest["name"] = guest["config"].get(name_key) or default_name(gtype, vmid)
    return None


def h_status_current(req):
    node, gtype, vmid = req.groups[0], req.groups[1], int(req.groups[2])
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    data = fixture_body("GET", req.path)
    if data is None:
        data = {"vmid": vmid, "name": guest["name"], "cpus": guest_cores(guest), "cpu": 0,
                "maxmem": guest_maxmem(guest), "mem": 0, "maxdisk": 0, "disk": 0, "uptime": 0}
        if gtype == "lxc":
            data["type"] = "lxc"
    data["status"] = guest["status"]
    data["name"] = guest["name"]
    if gtype == "qemu":
        data["qmpstatus"] = guest["status"]
    ha = guest_ha(guest)
    data["ha"] = {"managed": 1, "state": ha["state"]} if ha else {"managed": 0}
    return data


def h_power(req):
    node, gtype, vmid, action = req.groups[0], req.groups[1], int(req.groups[2]), req.groups[3]
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    ttype = ("qm" if gtype == "qemu" else "vz") + action
    upid = route_upid(req.method, req.path, node, ttype, vmid)
    if task_succeeds(ttype, str(vmid)):
        guest["status"] = "running" if action == "start" else "stopped"
    return upid


def h_snapshot_list(req):
    node, gtype, vmid = req.groups[0], req.groups[1], int(req.groups[2])
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    snaps = copy.deepcopy(guest["snapshots"])
    snaps.append({"name": "current", "description": "You are here!",
                  "running": 1 if guest["status"] == "running" else 0,
                  "parent": snaps[-1]["name"] if snaps else None})
    return snaps


def find_snapshot(guest, name):
    for snap in guest["snapshots"]:
        if snap["name"] == name:
            return snap
    return None


def h_snapshot_create(req):
    node, gtype, vmid = req.groups[0], req.groups[1], int(req.groups[2])
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    name = req.param("snapname")
    if not name:
        raise ApiError(400, PARAM_ERROR, {"snapname": MISSING})
    if find_snapshot(guest, name):
        raise ApiError(500, "snapshot name '%s' already used" % name)
    guest["snapshots"].append({
        "name": name, "description": req.param("description", ""),
        "vmstate": to_int(req.param("vmstate"), 0), "snaptime": int(time.time()),
        "parent": guest["snapshots"][-1]["name"] if guest["snapshots"] else None,
    })
    ttype = "qmsnapshot" if gtype == "qemu" else "vzsnapshot"
    return route_upid(req.method, req.path, node, ttype, vmid)


def h_snapshot_delete(req):
    node, gtype, vmid, name = req.groups[0], req.groups[1], int(req.groups[2]), req.groups[3]
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    snap = find_snapshot(guest, name)
    if snap is None:
        if (req.method, req.path) in ROUTES:
            raise Fallback()
        raise ApiError(500, "snapshot '%s' does not exist" % name)
    guest["snapshots"].remove(snap)
    for other in guest["snapshots"]:
        if other.get("parent") == name:
            other["parent"] = snap.get("parent")
    ttype = "qmdelsnapshot" if gtype == "qemu" else "vzdelsnapshot"
    return route_upid(req.method, req.path, node, ttype, vmid)


def h_snapshot_rollback(req):
    node, gtype, vmid, name = req.groups[0], req.groups[1], int(req.groups[2]), req.groups[3]
    guest = require_guest(req.method, req.path, node, gtype, vmid)
    if find_snapshot(guest, name) is None and (req.method, req.path) not in ROUTES:
        raise ApiError(500, "snapshot '%s' does not exist" % name)
    if str(req.param("start", "0")) == "1":
        guest["status"] = "running"
    ttype = "qmrollback" if gtype == "qemu" else "vzrollback"
    return route_upid(req.method, req.path, node, ttype, vmid)


def ha_state(raw):
    if raw is None:
        return "started"
    if raw not in HA_STATES:
        raise ApiError(400, PARAM_ERROR, {"state": "value '%s' does not have a value in the "
                                          "enumeration '%s'" % (raw, ", ".join(HA_STATES))})
    return {"enabled": "started", "disabled": "stopped"}.get(raw, raw)


def parse_sid(raw, must_exist):
    """Normalise an HA sid ('100', 'vm:100', 'ct:105') to (sid, type)."""
    rtype, _, name = str(raw).rpartition(":")
    vmid = to_int(name)
    guest = STATE["guests"].get(vmid) if vmid is not None else None
    if not rtype:
        if guest is None:
            raise ApiError(400, PARAM_ERROR, {"sid": "unable to determine resource type for '%s'"
                                              % raw})
        rtype = "vm" if guest["type"] == "qemu" else "ct"
    if rtype not in ("vm", "ct"):
        raise ApiError(400, PARAM_ERROR, {"sid": "unknown resource type '%s'" % rtype})
    sid = "%s:%s" % (rtype, name)
    expected = "qemu" if rtype == "vm" else "lxc"
    if must_exist and (guest is None or guest["type"] != expected):
        raise ApiError(500, "resource '%s' does not exist" % sid)
    return sid, rtype


def ha_merge(entry, req, keys):
    for key in str(req.param("delete", "")).split(","):
        key = key.strip()
        if key and key not in ("sid", "rule", "type", "state"):
            entry.pop(key, None)
    for key in keys:
        if req.has(key):
            value = req.param(key)
            entry[key] = to_int(value, value) if key in ("max_restart", "max_relocate", "failback") else value
    if req.has("state"):
        entry["state"] = ha_state(req.param("state"))
    entry["digest"] = digest_of({k: v for k, v in entry.items() if k != "digest"})


def h_ha_resources_list(req):
    items = [copy.deepcopy(r) for _, r in sorted(STATE["ha_resources"].items())]
    wanted = req.param("type")
    if wanted is not None:
        items = [i for i in items if i.get("type") == wanted]
    return items


def h_ha_resources_create(req):
    if not req.has("sid"):
        raise ApiError(400, PARAM_ERROR, {"sid": MISSING})
    sid, rtype = parse_sid(req.param("sid"), must_exist=True)
    if sid in STATE["ha_resources"]:
        raise ApiError(500, "resource ID '%s' already defined" % sid)
    entry = {"sid": sid, "type": rtype, "state": ha_state(req.param("state"))}
    ha_merge(entry, req, ("max_restart", "max_relocate", "failback", "comment", "group"))
    STATE["ha_resources"][sid] = entry
    return None


def ha_resource(req):
    sid, _ = parse_sid(unquote(req.groups[0]), must_exist=False)
    entry = STATE["ha_resources"].get(sid)
    if entry is None:
        raise ApiError(500, "no such resource '%s'" % sid)
    return entry


def h_ha_resources_get(req):
    return copy.deepcopy(ha_resource(req))


def h_ha_resources_update(req):
    entry = ha_resource(req)
    if req.has("digest") and req.param("digest") != entry.get("digest"):
        raise ApiError(500, "detected modified configuration - file changed by other user? Try again.")
    ha_merge(entry, req, ("max_restart", "max_relocate", "failback", "comment", "group"))
    return None


def h_ha_resources_delete(req):
    del STATE["ha_resources"][ha_resource(req)["sid"]]
    return None


RULE_KEYS = ("type", "resources", "nodes", "affinity", "strict", "comment", "disable")


def h_ha_rules_list(req):
    items = [copy.deepcopy(r) for _, r in sorted(STATE["ha_rules"].items())]
    wanted = req.param("type")
    if wanted is not None:
        items = [i for i in items if i.get("type") == wanted]
    return items


def h_ha_rules_create(req):
    errors = {k: MISSING for k in ("rule", "type", "resources") if not req.has(k)}
    if errors:
        raise ApiError(400, PARAM_ERROR, errors)
    rule = req.param("rule")
    if rule in STATE["ha_rules"]:
        raise ApiError(500, "HA rule '%s' already defined" % rule)
    entry = {"rule": rule}
    ha_merge(entry, req, RULE_KEYS)
    STATE["ha_rules"][rule] = entry
    return None


def ha_rule(req):
    rule = unquote(req.groups[0])
    entry = STATE["ha_rules"].get(rule)
    if entry is None:
        raise ApiError(500, "no such HA rule '%s'" % rule)
    return entry


def h_ha_rules_get(req):
    return copy.deepcopy(ha_rule(req))


def h_ha_rules_update(req):
    entry = ha_rule(req)
    if req.has("digest") and req.param("digest") != entry.get("digest"):
        raise ApiError(500, "detected modified configuration - file changed by other user? Try again.")
    ha_merge(entry, req, RULE_KEYS)
    return None


def h_ha_rules_delete(req):
    del STATE["ha_rules"][ha_rule(req)["rule"]]
    return None


def h_ha_status_current(req):
    # Mock-only shape: the real endpoint carries more fields per entry
    # (id, quorate, crm_state, request_state, ...); only these are served.
    items = [{"type": "node", "node": node, "status": "online"} for node in known_nodes()]
    for sid in sorted(STATE["ha_resources"]):
        entry = STATE["ha_resources"][sid]
        guest = STATE["guests"].get(to_int(sid.rpartition(":")[2]))
        items.append({"type": "service", "sid": sid, "state": entry.get("state"),
                      "node": guest["node"] if guest else None})
    return items


GUEST = r"^/nodes/([^/]+)/(qemu|lxc)/(\d+)"
DYNAMIC = [
    ("GET", r"^/cluster/resources$", h_cluster_resources),
    ("GET", r"^/cluster/nextid$", h_nextid),
    ("GET", r"^/cluster/ha/resources$", h_ha_resources_list),
    ("POST", r"^/cluster/ha/resources$", h_ha_resources_create),
    ("GET", r"^/cluster/ha/resources/([^/]+)$", h_ha_resources_get),
    ("PUT", r"^/cluster/ha/resources/([^/]+)$", h_ha_resources_update),
    ("DELETE", r"^/cluster/ha/resources/([^/]+)$", h_ha_resources_delete),
    ("GET", r"^/cluster/ha/rules$", h_ha_rules_list),
    ("POST", r"^/cluster/ha/rules$", h_ha_rules_create),
    ("GET", r"^/cluster/ha/rules/([^/]+)$", h_ha_rules_get),
    ("PUT", r"^/cluster/ha/rules/([^/]+)$", h_ha_rules_update),
    ("DELETE", r"^/cluster/ha/rules/([^/]+)$", h_ha_rules_delete),
    ("GET", r"^/cluster/ha/status/current$", h_ha_status_current),
    ("GET", r"^/nodes/([^/]+)/(qemu|lxc)$", h_guest_list),
    ("POST", r"^/nodes/([^/]+)/(qemu|lxc)$", h_guest_create),
    ("DELETE", GUEST + r"$", h_guest_destroy),
    ("GET", GUEST + r"/config$", h_config_get),
    ("PUT", GUEST + r"/config$", h_config_put),
    ("GET", GUEST + r"/status/current$", h_status_current),
    ("POST", GUEST + r"/status/(start|stop|shutdown)$", h_power),
    ("GET", GUEST + r"/snapshot$", h_snapshot_list),
    ("POST", GUEST + r"/snapshot$", h_snapshot_create),
    ("DELETE", GUEST + r"/snapshot/([^/]+)$", h_snapshot_delete),
    ("POST", GUEST + r"/snapshot/([^/]+)/rollback$", h_snapshot_rollback),
]
DYNAMIC = [(m, re.compile(p), h) for m, p, h in DYNAMIC]


def dispatch_dynamic(method, path, query, form):
    """(handled, data) for the stateful routes; raises ApiError for errors."""
    for route_method, pattern, handler in DYNAMIC:
        if route_method != method:
            continue
        match = pattern.match(path)
        if not match:
            continue
        try:
            with LOCK:
                return True, handler(Request(method, path, query, form, match.groups()))
        except Fallback:
            return False, None
    return False, None


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

        try:
            handled, data = dispatch_dynamic(method, api_path, query, form)
        except ApiError as exc:
            return self.send_json(exc.status, envelope_error(exc.message, exc.errors))
        if handled:
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
                reset_state()
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
    reset_state()

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

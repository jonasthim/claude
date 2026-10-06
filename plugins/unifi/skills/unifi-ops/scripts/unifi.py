#!/usr/bin/env python3
"""unifi.py - stdlib-only CLI for the UniFi Network Integration API and the
UniFi Site Manager (cloud) API.

Output is JSON on stdout by default so the caller (usually Claude) can format
it. Pass --table for a quick markdown table of the common list commands.

Environment:
  UNIFI_HOST            gateway / console host or IP (local Integration API)
  UNIFI_API_KEY         Integration API key (Settings -> Control Plane -> Integrations)
  UNIFI_SITE            site name, default "default"
  UNIFI_VERIFY_TLS      "1" to verify TLS; default off because consoles ship self-signed certs
  UNIFI_CLOUD_API_KEY   Site Manager API key (unifi.ui.com -> API); UNIFI_SITE_MANAGER_API_KEY also accepted
  UNIFI_MOCK_DIR        serve fixture JSON from this directory instead of HTTP
  UNIFI_TIMEOUT         HTTP timeout seconds, default 20

Write safety: every mutating command prints the exact request and exits with
code 3 unless --yes is given. --dry-run always prints and never sends. A dry run
proves the request shape, not that the controller accepts the action: on Network
10.6 the only device action is RESTART (verified live); port and client actions
are documented but unverified, and a 400 lists the valid values.
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE_SIZE = 200
SHOW_SECRETS = False
SECRET_KEY_RE = re.compile(r"(passphrase|password|secret|\bpsk\b|private_?key|api_?key|shared_?secret|x_?password)", re.I)
REDACTED = "<redacted; pass --show-secrets to print>"
EXIT_USAGE = 1
EXIT_API = 2
EXIT_NEEDS_CONFIRM = 3

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def die(msg, code=EXIT_API):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(code)


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def norm_mac(s):
    return re.sub(r"[^0-9a-f]", "", (s or "").lower())


def redact(obj):
    """Replace values of credential-shaped keys. Controllers return WiFi passphrases,
    RADIUS secrets and voucher codes in cleartext; they must not land in a transcript by default."""
    if isinstance(obj, dict):
        return {k: (REDACTED if SECRET_KEY_RE.search(k) and isinstance(v, (str, int, float)) and v != "" else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def emit(obj, table=None, columns=None):
    if not SHOW_SECRETS:
        obj = redact(obj)
    if table and isinstance(obj, list):
        print_table(obj, columns)
    else:
        print(json.dumps(obj, indent=2, sort_keys=False))


def get_path(d, dotted, default=""):
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur if cur is not None else default


def print_table(rows, columns=None):
    if not rows:
        print("_(no rows)_")
        return
    if not columns:
        columns = []
        for k, v in rows[0].items():
            if not isinstance(v, (dict, list)):
                columns.append(k)
            if len(columns) >= 8:
                break
    def cell(r, c):
        v = get_path(r, c, "")
        return "" if v in (None, "", [], {}) else v
    columns = [c for c in columns if any(cell(r, c) != "" for r in rows)] or columns[:1]
    columns = [c for c in columns if not any(o != c and o.startswith(c + ".") for o in columns)]
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    print(header)
    print(sep)
    for r in rows:
        cells = []
        for c in columns:
            v = get_path(r, c, "")
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            v = str(v).replace("|", "\\|").replace("\n", " ")
            cells.append(v)
        print("| " + " | ".join(cells) + " |")


def read_body(spec):
    """--body accepts a file path, '-' for stdin, or an inline JSON string."""
    if spec is None:
        return None
    if spec == "-":
        raw = sys.stdin.read()
    elif os.path.exists(spec):
        with open(spec) as f:
            raw = f.read()
    else:
        raw = spec
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die("--body is not valid JSON (%s)" % e, EXIT_USAGE)


# --------------------------------------------------------------------------
# mock backend
# --------------------------------------------------------------------------


class MockBackend:
    """Serves fixture files from UNIFI_MOCK_DIR.

    Resolution: path segments after /v1/ (and after sites/<id>/) are joined
    with '_' until a matching <name>.json file is found; the next segment, if
    it matches an item id in that collection, selects the item; any trailing
    segments select <collection>_<sub>.json keyed by item id.
    Writes are echoed back with a generated id and never persisted.
    """

    def __init__(self, root):
        self.root = root
        if not os.path.isdir(root):
            die("UNIFI_MOCK_DIR %s is not a directory" % root, EXIT_USAGE)

    def _load(self, name):
        p = os.path.join(self.root, name + ".json")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    @staticmethod
    def _norm(seg):
        return re.sub(r"[^a-z0-9]+", "_", seg.lower())

    def request(self, method, path, query=None, body=None):
        query = query or {}
        segs = [s for s in path.split("/") if s]
        if segs and segs[0] == "v1":
            segs = segs[1:]
        if len(segs) >= 2 and segs[0] == "sites":
            segs = segs[2:]
        if not segs:
            segs = ["sites"]

        coll_key, coll, item, sub = None, None, None, []
        parts = []
        i = 0
        while i < len(segs):
            parts.append(self._norm(segs[i]))
            cand = "_".join(parts)
            data = self._load(cand)
            i += 1
            if data is not None:
                coll_key, coll = cand, data
                break
        if coll is None:
            if method == "GET":
                raise ApiError(404, {"code": "MOCK_NOT_FOUND",
                                     "message": "no fixture for %s" % path})
            return self._echo_write(method, body)

        rest = segs[i:]
        if rest and isinstance(coll, list):
            for it in coll:
                if str(it.get("id")) == rest[0]:
                    item = it
                    rest = rest[1:]
                    break
        sub = rest

        if method != "GET":
            if item is not None and sub == [] and method in ("PUT", "PATCH"):
                merged = dict(item)
                merged.update(body or {})
                return merged
            if item is not None and method == "DELETE":
                return {}
            return self._echo_write(method, body)

        if sub:
            subdata = self._load(coll_key + "_" + "_".join(self._norm(s) for s in sub))
            if subdata is None:
                raise ApiError(404, {"code": "RESOURCE_NOT_FOUND",
                                     "message": "no %s matching %s" % (coll_key, "/".join(rest))})
            if item is not None and isinstance(subdata, dict):
                return subdata.get(str(item.get("id")), subdata.get("_default", {}))
            return subdata
        if item is not None:
            return item
        if rest and isinstance(coll, list):
            raise ApiError(404, {"code": "RESOURCE_NOT_FOUND",
                                 "message": "no %s with id %s" % (coll_key, rest[0])})
        if isinstance(coll, list):
            return self._paginate(self._filter(coll, query.get("filter")), query)
        return coll

    @staticmethod
    def _echo_write(method, body):
        out = dict(body or {})
        if method == "POST" and "action" not in out:
            out.setdefault("id", "mock-%d" % int(time.time()))
        return out

    @staticmethod
    def _paginate(items, query):
        offset = int(query.get("offset", 0))
        limit = int(query.get("limit", 25))
        page = items[offset:offset + limit]
        return {"offset": offset, "limit": limit, "count": len(page),
                "totalCount": len(items), "data": page}

    def _filter(self, items, expr):
        if not expr:
            return items
        pred = self._compile(expr.strip())
        return [it for it in items if pred(it)]

    def _compile(self, expr):
        m = re.match(r"^(and|or|not)\((.*)\)$", expr)
        if m:
            op, inner = m.group(1), m.group(2)
            subs = [self._compile(p) for p in self._split_args(inner)]
            if op == "and":
                return lambda it: all(s(it) for s in subs)
            if op == "or":
                return lambda it: any(s(it) for s in subs)
            return lambda it: not subs[0](it)
        m = re.match(r"^([A-Za-z0-9_.]+)\.(\w+)\((.*)\)$", expr)
        if not m:
            raise ApiError(400, {"code": "INVALID_FILTER", "message": expr})
        prop, fn, args = m.groups()
        vals = [self._lit(a) for a in self._split_args(args)] if args.strip() else []

        def val(it):
            return get_path(it, prop, None)

        if fn == "eq":
            return lambda it: val(it) == vals[0]
        if fn == "ne":
            return lambda it: val(it) != vals[0]
        if fn == "in":
            return lambda it: val(it) in vals
        if fn == "notIn":
            return lambda it: val(it) not in vals
        if fn == "isNull":
            return lambda it: val(it) is None
        if fn == "isNotNull":
            return lambda it: val(it) is not None
        if fn == "like":
            rx = re.compile("^" + re.escape(str(vals[0])).replace("\\*", ".*") + "$", re.I)
            return lambda it: val(it) is not None and bool(rx.match(str(val(it))))
        if fn in ("gt", "ge", "lt", "le"):
            cmp = {"gt": lambda a, b: a > b, "ge": lambda a, b: a >= b,
                   "lt": lambda a, b: a < b, "le": lambda a, b: a <= b}[fn]
            return lambda it: val(it) is not None and cmp(val(it), vals[0])
        raise ApiError(400, {"code": "INVALID_FILTER", "message": "unsupported function " + fn})

    @staticmethod
    def _split_args(s):
        out, depth, cur = [], 0, ""
        for ch in s:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
        return out

    @staticmethod
    def _lit(tok):
        tok = tok.strip()
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
            return tok[1:-1]
        if tok in ("true", "false"):
            return tok == "true"
        try:
            return int(tok)
        except ValueError:
            try:
                return float(tok)
            except ValueError:
                return tok


# --------------------------------------------------------------------------
# HTTP backends
# --------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status, payload):
        super().__init__(status)
        self.status = status
        self.payload = payload


def http_json(method, url, headers, body=None, timeout=20, verify=False):
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers = dict(headers, **{"Content-Type": "application/json"})
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw.decode(errors="replace")}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"message": raw.decode(errors="replace")[:500]}
        if e.code == 429:
            payload["retryAfter"] = e.headers.get("Retry-After") or e.headers.get("X-RateLimit-Reset")
        raise ApiError(e.code, payload)
    except urllib.error.URLError as e:
        die("cannot reach %s (%s). Check UNIFI_HOST / network / VPN." % (url, e.reason))


class NetworkApi:
    """Local UniFi Network Integration API."""

    def __init__(self, args):
        self.timeout = int(env("UNIFI_TIMEOUT", "20"))
        self.verify = truthy(env("UNIFI_VERIFY_TLS", "0"))
        self.site_name = args.site or env("UNIFI_SITE", "default")
        self.mock = MockBackend(env("UNIFI_MOCK_DIR")) if env("UNIFI_MOCK_DIR") else None
        self._site_id = None
        self._base = None
        if not self.mock:
            self.host = env("UNIFI_HOST")
            self.key = env("UNIFI_API_KEY")
            if not self.host or not self.key:
                die("UNIFI_HOST and UNIFI_API_KEY must be set (or UNIFI_MOCK_DIR for offline use)", EXIT_USAGE)
            if not self.host.startswith("http"):
                self.host = "https://" + self.host

    @property
    def base(self):
        if self._base:
            return self._base
        if self.mock:
            self._base = "mock://unifi"
            return self._base
        headers = {"X-API-Key": self.key, "Accept": "application/json"}
        for prefix in ("/proxy/network/integration", "/integration"):
            try:
                http_json("GET", self.host + prefix + "/v1/info", headers,
                          timeout=self.timeout, verify=self.verify)
                self._base = self.host + prefix
                return self._base
            except ApiError as e:
                if e.status in (401, 403):
                    die("API key rejected (%s) at %s%s. Check UNIFI_API_KEY and the key's permissions."
                        % (e.status, self.host, prefix))
                if e.status != 404:
                    raise
        die("Integration API not found under %s/proxy/network/integration or %s/integration. "
            "Is this a UniFi Network 9.x+ console and is the API enabled?" % (self.host, self.host))

    def request(self, method, path, query=None, body=None):
        query = {k: v for k, v in (query or {}).items() if v is not None}
        if self.mock:
            return self.mock.request(method, path, query, body)
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"X-API-Key": self.key, "Accept": "application/json"}
        return http_json(method, url, headers, body, self.timeout, self.verify)

    def list_all(self, path, query=None, limit=None):
        """Follow offset/limit pagination and return the full data list."""
        query = dict(query or {})
        out = []
        offset = 0
        page = min(PAGE_SIZE, limit) if limit else PAGE_SIZE
        while True:
            query.update({"offset": offset, "limit": page})
            resp = self.request("GET", path, query)
            if isinstance(resp, list):
                return resp
            data = resp.get("data", [])
            out.extend(data)
            total = resp.get("totalCount", len(out))
            offset += len(data)
            if limit and len(out) >= limit:
                return out[:limit]
            if not data or offset >= total:
                return out

    @property
    def site_id(self):
        if self._site_id:
            return self._site_id
        sites = self.list_all("/v1/sites")
        if not sites:
            die("controller returned no sites")
        for s in sites:
            if s.get("internalReference") == self.site_name or s.get("name") == self.site_name \
                    or s.get("id") == self.site_name:
                self._site_id = s["id"]
                return self._site_id
        if len(sites) == 1:
            self._site_id = sites[0]["id"]
            return self._site_id
        die("site %r not found; available: %s" % (
            self.site_name, ", ".join("%s (%s)" % (s.get("name"), s.get("internalReference")) for s in sites)))

    def sp(self, suffix):
        return "/v1/sites/%s%s" % (self.site_id, suffix)


class CloudApi:
    """UniFi Site Manager API at api.ui.com."""

    BASE = "https://api.ui.com"

    def __init__(self):
        self.timeout = int(env("UNIFI_TIMEOUT", "20"))
        self.mock = MockBackend(env("UNIFI_MOCK_DIR")) if env("UNIFI_MOCK_DIR") else None
        self.key = env("UNIFI_CLOUD_API_KEY") or env("UNIFI_SITE_MANAGER_API_KEY")
        if not self.mock and not self.key:
            die("UNIFI_CLOUD_API_KEY (or UNIFI_SITE_MANAGER_API_KEY) must be set for cloud commands. "
                "This is the unifi.ui.com key; the local Integration key is not accepted by api.ui.com.", EXIT_USAGE)

    def request(self, method, path, query=None, body=None):
        query = {k: v for k, v in (query or {}).items() if v is not None}
        if self.mock:
            return self.mock.request(method, "cloud" + path.replace("/v1", "", 1), query, body)
        url = self.BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = {"X-API-KEY": self.key, "Accept": "application/json"}
        for attempt in range(3):
            try:
                return http_json(method, url, headers, body, self.timeout, verify=True)
            except ApiError as e:
                if e.status == 429 and attempt < 2:
                    wait = e.payload.get("retryAfter")
                    try:
                        wait = min(float(wait), 30)
                    except (TypeError, ValueError):
                        wait = 5 * (attempt + 1)
                    sys.stderr.write("rate limited, retrying in %ss\n" % wait)
                    time.sleep(wait)
                    continue
                raise

    def list_all(self, path, query=None):
        query = dict(query or {})
        out = []
        while True:
            resp = self.request("GET", path, query)
            if isinstance(resp, list):
                return resp
            data = resp.get("data", [])
            out.extend(data if isinstance(data, list) else [data])
            token = resp.get("nextToken")
            if not token:
                return out
            query["nextToken"] = token


# --------------------------------------------------------------------------
# write guard
# --------------------------------------------------------------------------


def guarded(api, args, method, path, body=None, query=None):
    """Print the request; send it only with --yes and without --dry-run."""
    base = api.base if isinstance(api, NetworkApi) else CloudApi.BASE
    plan = {"dry_run": True, "method": method, "url": base + path, "body": body}
    if query:
        plan["query"] = query
    if args.dry_run or not args.yes:
        emit(plan)
        if not args.dry_run:
            sys.stderr.write("\nNot sent. This changes the network. Re-run with --yes after confirming with the user.\n")
            sys.exit(EXIT_NEEDS_CONFIRM)
        return
    result = api.request(method, path, query, body)
    emit({"applied": True, "method": method, "url": base + path, "result": result})


# --------------------------------------------------------------------------
# column presets for --table
# --------------------------------------------------------------------------

COLUMNS = {
    "sites": ["id", "internalReference", "name"],
    "devices": ["name", "model", "ipAddress", "state", "firmwareVersion", "firmwareUpdatable", "uplinkDeviceName", "id"],
    "clients": ["name", "ipAddress", "macAddress", "type", "uplinkDeviceName", "connectedAt", "id"],
    "networks": ["name", "vlanId", "management", "enabled", "id"],
    "wifi": ["name", "enabled", "securityConfiguration.type", "network.networkId", "clientIsolationEnabled", "id"],
    "zones": ["name", "metadata.origin", "networkIds", "id"],
    "policies": ["name", "enabled", "action.type", "action", "source.zoneId", "destination.zoneId", "id"],
    "acl": ["name", "enabled", "action", "type", "id"],
    "dns": ["name", "enabled", "type", "id"],
    "vouchers": ["code", "name", "expired", "timeLimitMinutes", "id"],
    "wans": ["name", "enabled", "type", "id"],
    "cloud_hosts": ["id", "type", "reportedState.hostname", "reportedState.state", "reportedState.version"],
    "cloud_sites": ["siteId", "meta.name", "hostId", "statistics.counts.totalDevice", "statistics.counts.offlineDevice"],
    "cloud_devices": ["hostId", "hostName"],
}


# --------------------------------------------------------------------------
# command implementations
# --------------------------------------------------------------------------


def cmd_info(api, args):
    emit(api.request("GET", "/v1/info"))


def cmd_sites(api, args):
    emit(api.list_all("/v1/sites", limit=args.limit), args.table, COLUMNS["sites"])


def _device_name_map(api):
    return {d["id"]: d.get("name") or d.get("model") for d in api.list_all(api.sp("/devices"))}


def cmd_devices(api, args):
    v = args.verb
    if v == "list":
        devices = api.list_all(api.sp("/devices"), {"filter": args.filter}, args.limit)
        names = {d["id"]: d.get("name") or d.get("model") for d in devices}
        for d in devices:
            d["uplinkDeviceName"] = names.get(get_path(d, "uplink.deviceId", None), "")
        emit(devices, args.table, COLUMNS["devices"])
    elif v == "get":
        emit(api.request("GET", api.sp("/devices/%s" % args.id)))
    elif v == "stats":
        emit(api.request("GET", api.sp("/devices/%s/statistics/latest" % args.id)))
    elif v == "restart":
        guarded(api, args, "POST", api.sp("/devices/%s/actions" % args.id), {"action": "RESTART"})
    elif v == "action":
        guarded(api, args, "POST", api.sp("/devices/%s/actions" % args.id), {"action": args.action})
    elif v in ("port-cycle", "port-enable", "port-disable"):
        action = {"port-cycle": "POWER_CYCLE", "port-enable": "ENABLE", "port-disable": "DISABLE"}[v]
        guarded(api, args, "POST",
                api.sp("/devices/%s/interfaces/ports/%s/actions" % (args.id, args.port)), {"action": action})
    elif v == "unadopt":
        guarded(api, args, "DELETE", api.sp("/devices/%s" % args.id))
    elif v == "pending":
        emit(api.list_all("/v1/pending-devices", limit=args.limit), args.table,
             ["name", "model", "macAddress", "ipAddress", "id"])
    elif v == "adopt":
        guarded(api, args, "POST", "/v1/pending-devices", {"macAddresses": args.macs})


def _enrich_clients(api, clients):
    names = _device_name_map(api)
    for c in clients:
        c["uplinkDeviceName"] = names.get(c.get("uplinkDeviceId"), "")
        if c.get("type") == "WIRED" and "portIdx" not in c and "uplinkPortIdx" not in c:
            c["switchPort"] = "not exposed by API (ask user or SSH: see ssh-commands.md)"
    return clients


def cmd_clients(api, args):
    v = args.verb
    if v == "list":
        clients = api.list_all(api.sp("/clients"), {"filter": args.filter}, args.limit)
        emit(_enrich_clients(api, clients), args.table, COLUMNS["clients"])
    elif v == "get":
        emit(api.request("GET", api.sp("/clients/%s" % args.id)))
    elif v == "find":
        needle = args.query.lower()
        nmac = norm_mac(needle) if re.fullmatch(r"[0-9a-f:.\-]{12,17}", needle) else None
        hits = []
        for c in api.list_all(api.sp("/clients")):
            hay = [str(c.get(k, "")).lower() for k in ("name", "hostname", "ipAddress", "id")]
            if any(needle in h for h in hay) or (nmac and norm_mac(c.get("macAddress")) == nmac):
                hits.append(c)
        emit(_enrich_clients(api, hits), args.table, COLUMNS["clients"])
    elif v in ("block", "unblock"):
        guarded(api, args, "POST", api.sp("/clients/%s/actions" % args.id), {"action": v.upper()})
    elif v == "authorize":
        body = {"action": "AUTHORIZE_GUEST_ACCESS"}
        if args.minutes:
            body["timeLimitMinutes"] = args.minutes
        guarded(api, args, "POST", api.sp("/clients/%s/actions" % args.id), body)
    elif v == "action":
        guarded(api, args, "POST", api.sp("/clients/%s/actions" % args.id), {"action": args.action})


def zone_query(args):
    """Firewall policy ordering is kept per source/destination zone pair on 10.6
    (the API answers 400 'sourceFirewallZoneId ... is not present' without it)."""
    q = {}
    if getattr(args, "source_zone", None):
        q["sourceFirewallZoneId"] = args.source_zone
    if getattr(args, "dest_zone", None):
        q["destinationFirewallZoneId"] = args.dest_zone
    return q or None


def crud(api, args, base, cols, extra=None):
    """Generic list/get/create/update/patch/delete for a site collection."""
    v = args.verb
    if v == "list":
        emit(api.list_all(api.sp(base), {"filter": args.filter}, args.limit), args.table, cols)
    elif v == "get":
        emit(api.request("GET", api.sp("%s/%s" % (base, args.id))))
    elif v == "create":
        guarded(api, args, "POST", api.sp(base), read_body(args.body))
    elif v == "update":
        guarded(api, args, "PUT", api.sp("%s/%s" % (base, args.id)), read_body(args.body))
    elif v == "patch":
        guarded(api, args, "PATCH", api.sp("%s/%s" % (base, args.id)), read_body(args.body))
    elif v == "delete":
        guarded(api, args, "DELETE", api.sp("%s/%s" % (base, args.id)))
    elif v == "ordering":
        emit(api.request("GET", api.sp(base + "/ordering"), zone_query(args)))
    elif v == "reorder":
        guarded(api, args, "PUT", api.sp(base + "/ordering"), read_body(args.body), zone_query(args))
    elif extra:
        extra(v)


def cmd_networks(api, args):
    def extra(v):
        if v == "references":
            emit(api.request("GET", api.sp("/networks/%s/references" % args.id)))
    crud(api, args, "/networks", COLUMNS["networks"], extra)


def cmd_wifi(api, args):
    crud(api, args, "/wifi/broadcasts", COLUMNS["wifi"])


def cmd_firewall(api, args):
    if args.kind == "zones":
        crud(api, args, "/firewall/zones", COLUMNS["zones"])
    else:
        crud(api, args, "/firewall/policies", COLUMNS["policies"])


def cmd_acl(api, args):
    crud(api, args, "/acl-rules", COLUMNS["acl"])


def cmd_dns(api, args):
    crud(api, args, "/dns/policies", COLUMNS["dns"])


def cmd_traffic(api, args):
    crud(api, args, "/traffic-matching-lists", ["name", "type", "id"])


def cmd_vouchers(api, args):
    if args.verb == "delete" and not args.id:
        if not args.filter:
            die("refusing to bulk-delete vouchers without --filter", EXIT_USAGE)
        guarded(api, args, "DELETE", api.sp("/hotspot/vouchers"), query={"filter": args.filter})
        return
    crud(api, args, "/hotspot/vouchers", COLUMNS["vouchers"])


def cmd_wans(api, args):
    emit(api.list_all(api.sp("/wans"), limit=args.limit), args.table, COLUMNS["wans"])


def cmd_vpn(api, args):
    path = "/vpn/site-to-site-tunnels" if args.verb == "tunnels" else "/vpn/servers"
    emit(api.list_all(api.sp(path), limit=args.limit), args.table)


def cmd_radius(api, args):
    emit(api.list_all(api.sp("/radius/profiles"), limit=args.limit), args.table)


def cmd_dpi(api, args):
    path = "/v1/dpi/categories" if args.verb == "categories" else "/v1/dpi/applications"
    emit(api.list_all(path, {"filter": args.filter}, args.limit), args.table)


def cmd_raw(api, args):
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else "/" + args.path
    if not path.startswith("/v1"):
        path = "/v1" + path
    path = path.replace("{siteId}", api.site_id) if "{siteId}" in path else path
    query = dict(kv.split("=", 1) for kv in (args.query or []))
    if method == "GET":
        emit(api.request("GET", path, query))
    else:
        guarded(api, args, method, path, read_body(args.body), query or None)


def cmd_report(api, args):
    """Aggregate a health snapshot: devices + latest stats, WANs, client counts."""
    devices = api.list_all(api.sp("/devices"))
    rows = []
    for d in devices:
        row = {k: d.get(k) for k in ("id", "name", "model", "ipAddress", "state",
                                      "firmwareVersion", "firmwareUpdatable")}
        row["uplinkDeviceName"] = ""  # filled below once all names are known
        if d.get("state") == "ONLINE" and not args.no_stats:
            try:
                st = api.request("GET", api.sp("/devices/%s/statistics/latest" % d["id"]))
            except ApiError:
                st = {}
            radios = get_path(st, "interfaces.radios", []) or []
            row.update({
                "uptimeSec": st.get("uptimeSec"),
                "cpuPct": st.get("cpuUtilizationPct"),
                "memPct": st.get("memoryUtilizationPct"),
                "lastHeartbeatAt": st.get("lastHeartbeatAt"),
                "uplinkTxBps": get_path(st, "uplink.txRateBps", None),
                "uplinkRxBps": get_path(st, "uplink.rxRateBps", None),
            })
            for r in radios:
                if r.get("txRetriesPct") is not None:
                    band = {2.4: "2g", 5: "5g", 6: "6g"}.get(r.get("frequencyGHz"), str(r.get("frequencyGHz")))
                    row["txRetriesPct_%s" % band] = r.get("txRetriesPct")
        rows.append(row)
    names = {d["id"]: d.get("name") or d.get("model") for d in devices}
    for d, row in zip(devices, rows):
        row["uplinkDeviceName"] = names.get(get_path(d, "uplink.deviceId", None), "")
    clients = api.list_all(api.sp("/clients"))
    by_type = {}
    for c in clients:
        by_type[c.get("type", "UNKNOWN")] = by_type.get(c.get("type", "UNKNOWN"), 0) + 1
    try:
        wans = api.list_all(api.sp("/wans"))
    except ApiError as e:
        wans = {"error": e.payload}
    summary = {
        "devicesTotal": len(devices),
        "devicesByState": {},
        "firmwareUpdatable": [d.get("name") for d in devices if d.get("firmwareUpdatable")],
        "clientsTotal": len(clients),
        "clientsByType": by_type,
    }
    for d in devices:
        s = d.get("state", "UNKNOWN")
        summary["devicesByState"][s] = summary["devicesByState"].get(s, 0) + 1
    out = {"summary": summary, "devices": rows, "wans": wans}
    if args.table:
        print("## Summary\n")
        print_table([summary], ["devicesTotal", "devicesByState", "firmwareUpdatable", "clientsTotal", "clientsByType"])
        print("\n## Devices\n")
        print_table(rows, ["name", "model", "ipAddress", "state", "firmwareVersion",
                           "firmwareUpdatable", "uplinkDeviceName", "uptimeSec", "cpuPct", "memPct",
                           "txRetriesPct_2g", "txRetriesPct_5g", "txRetriesPct_6g", "id"])
        print("\n## WANs\n")
        print_table(wans if isinstance(wans, list) else [wans], COLUMNS["wans"] if isinstance(wans, list) else None)
    else:
        emit(out)


def cmd_cloud(args):
    api = CloudApi()
    v = args.verb
    if v == "hosts":
        if args.id:
            emit(api.request("GET", "/v1/hosts/%s" % args.id))
        else:
            emit(api.list_all("/v1/hosts"), args.table, COLUMNS["cloud_hosts"])
    elif v == "sites":
        emit(api.list_all("/v1/sites"), args.table, COLUMNS["cloud_sites"])
    elif v == "devices":
        q = {"hostIds[]": args.host_ids} if args.host_ids else None
        emit(api.list_all("/v1/devices", q), args.table, COLUMNS["cloud_devices"])
    elif v == "isp-metrics":
        q = {"duration": args.duration, "beginTimestamp": args.begin, "endTimestamp": args.end}
        emit(api.request("GET", "/v1/isp-metrics/%s" % args.interval, q))
    elif v == "sdwan":
        if args.id:
            emit(api.request("GET", "/v1/sd-wan-configs/%s" % args.id))
            emit(api.request("GET", "/v1/sd-wan-configs/%s/status" % args.id))
        else:
            emit(api.list_all("/v1/sd-wan-configs"), args.table)


# --------------------------------------------------------------------------
# argparse wiring
# --------------------------------------------------------------------------


def add_common(p, write=False, listing=False):
    p.add_argument("--site", help="site name (default: $UNIFI_SITE or 'default')")
    p.add_argument("--table", action="store_true", help="markdown table instead of JSON")
    p.add_argument("--show-secrets", action="store_true",
                   help="print passphrases/secrets instead of redacting them (output then holds live credentials)")
    if listing:
        p.add_argument("--filter", help="server-side filter, e.g. name.like('guest*')")
        p.add_argument("--limit", type=int, help="stop after N items (default: all)")
    if write:
        p.add_argument("--yes", action="store_true", help="actually send the mutating request")
        p.add_argument("--dry-run", action="store_true", help="print the request and never send")
        p.add_argument("--body", help="JSON body: file path, '-' for stdin, or inline JSON")


def build_parser():
    ap = argparse.ArgumentParser(prog="unifi.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="group", required=True)

    p = sub.add_parser("info", help="application version / API info")
    add_common(p)
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("sites", help="list sites")
    p.add_argument("verb", choices=["list"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_sites)

    p = sub.add_parser("devices", help="adopted devices and actions")
    p.add_argument("verb", choices=["list", "get", "stats", "restart", "action",
                                    "port-cycle", "port-enable", "port-disable", "unadopt", "pending", "adopt"])
    p.add_argument("id", nargs="?", help="device id")
    p.add_argument("--port", type=int, help="port index for port-* verbs")
    p.add_argument("--action", help="raw action name for 'action'")
    p.add_argument("--macs", nargs="+", help="MACs for 'adopt'")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_devices)

    p = sub.add_parser("clients", help="connected clients and actions")
    p.add_argument("verb", choices=["list", "get", "find", "block", "unblock", "authorize", "action"])
    p.add_argument("id", nargs="?", help="client id (or search text for 'find')")
    p.add_argument("--minutes", type=int, help="time limit for 'authorize'")
    p.add_argument("--action", help="raw action name for 'action'")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_clients)

    crud_verbs = ["list", "get", "create", "update", "patch", "delete"]
    for name, fn, extra in (("networks", cmd_networks, ["references"]),
                            ("wifi", cmd_wifi, []),
                            ("acl", cmd_acl, ["ordering", "reorder"]),
                            ("dns", cmd_dns, []),
                            ("traffic", cmd_traffic, []),
                            ("vouchers", cmd_vouchers, [])):
        p = sub.add_parser(name, help="%s: %s" % (name, ", ".join(crud_verbs + extra)))
        p.add_argument("verb", choices=crud_verbs + extra)
        p.add_argument("id", nargs="?")
        add_common(p, write=True, listing=True)
        p.set_defaults(fn=fn)

    p = sub.add_parser("firewall", help="firewall zones and policies")
    p.add_argument("kind", choices=["zones", "policies"])
    p.add_argument("verb", choices=crud_verbs + ["ordering", "reorder"])
    p.add_argument("id", nargs="?")
    p.add_argument("--source-zone", help="zone id; required by the API for policies ordering/reorder")
    p.add_argument("--dest-zone", help="zone id; destination side of the ordering pair")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_firewall)

    p = sub.add_parser("wans", help="WAN interfaces")
    p.add_argument("verb", choices=["list"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_wans)

    p = sub.add_parser("vpn", help="VPN tunnels / servers")
    p.add_argument("verb", choices=["tunnels", "servers"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_vpn)

    p = sub.add_parser("radius", help="RADIUS profiles")
    p.add_argument("verb", choices=["list"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_radius)

    p = sub.add_parser("dpi", help="DPI categories / applications")
    p.add_argument("verb", choices=["categories", "applications"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_dpi)

    p = sub.add_parser("report", help="aggregated health snapshot")
    p.add_argument("verb", choices=["health"])
    p.add_argument("--no-stats", action="store_true", help="skip per-device statistics calls")
    add_common(p)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("raw", help="arbitrary Integration API call, e.g. raw GET /sites/{siteId}/wans")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--query", nargs="*", help="key=value pairs")
    add_common(p, write=True)
    p.set_defaults(fn=cmd_raw)

    p = sub.add_parser("cloud", help="Site Manager API (api.ui.com)")
    p.add_argument("verb", choices=["hosts", "sites", "devices", "isp-metrics", "sdwan"])
    p.add_argument("id", nargs="?", help="host id / sd-wan config id")
    p.add_argument("--host-ids", nargs="*", help="filter devices by host ids")
    p.add_argument("--interval", choices=["5m", "1h"], default="1h")
    p.add_argument("--duration", help="e.g. 24h or 7d (interval 1h) / 24h (interval 5m)")
    p.add_argument("--begin", help="RFC3339 begin timestamp")
    p.add_argument("--end", help="RFC3339 end timestamp")
    p.add_argument("--table", action="store_true")
    p.add_argument("--show-secrets", action="store_true")
    p.set_defaults(fn=None)
    return ap


def main(argv=None):
    global SHOW_SECRETS
    ap = build_parser()
    args = ap.parse_args(argv)
    SHOW_SECRETS = bool(getattr(args, "show_secrets", False))
    for attr, default in (("yes", False), ("dry_run", False), ("body", None), ("filter", None),
                          ("limit", None), ("table", False), ("site", None), ("id", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    if args.group == "clients" and args.verb == "find":
        if not args.id:
            die("clients find needs search text (name, hostname, IP or MAC)", EXIT_USAGE)
        args.query = args.id
    needs_id = {
        "devices": {"get", "stats", "restart", "action", "port-cycle", "port-enable", "port-disable", "unadopt"},
        "clients": {"get", "block", "unblock", "authorize", "action"},
        "networks": {"get", "update", "patch", "delete", "references"},
        "wifi": {"get", "update", "patch", "delete"}, "acl": {"get", "update", "patch", "delete"},
        "dns": {"get", "update", "patch", "delete"}, "traffic": {"get", "update", "patch", "delete"},
        "firewall": {"get", "update", "patch", "delete"},
    }
    if args.group in needs_id and getattr(args, "verb", None) in needs_id[args.group] and not args.id:
        die("%s %s needs an id" % (args.group, args.verb), EXIT_USAGE)
    if args.group == "devices" and args.verb.startswith("port-") and args.port is None:
        die("port-* verbs need --port <index>", EXIT_USAGE)
    if args.group == "firewall" and args.kind == "policies" and args.verb in ("ordering", "reorder") \
            and not getattr(args, "source_zone", None):
        die("firewall policies %s needs --source-zone <zone id> (ordering is kept per zone pair; add --dest-zone if the API asks for it)"
            % args.verb, EXIT_USAGE)
    try:
        if args.group == "cloud":
            cmd_cloud(args)
        else:
            args.fn(NetworkApi(args), args)
    except ApiError as e:
        hint = ""
        if e.status in (401, 403):
            hint = " (check the API key and its permissions)"
        elif e.status == 429:
            hint = " (rate limited; retryAfter=%s)" % e.payload.get("retryAfter")
        sys.stderr.write("API error %s%s: %s\n" % (e.status, hint, json.dumps(e.payload)))
        sys.exit(EXIT_API)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()

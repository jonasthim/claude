#!/usr/bin/env python3
"""pangolin.py - stdlib-only CLI for the Pangolin Integration API (/v1).

Output is JSON on stdout by default so the caller (usually Claude) can format
it. Pass --table for a markdown table of a list command.

Environment:
  PANGOLIN_HOST         address of the Integration API, e.g. https://api.example.com
                        ("/v1" is added when absent; the API listens on port 3003 by default)
  PANGOLIN_API_KEY      API key as shown once at creation ("<id>.<secret>")
  PANGOLIN_ORG          organization id; optional for a root key that can list exactly one
  PANGOLIN_CA_CERT      CA bundle for a privately signed certificate
  PANGOLIN_VERIFY_TLS   "0" skips certificate verification (the user's choice, never the caller's)
  PANGOLIN_TIMEOUT      HTTP timeout in seconds, default 20
  PANGOLIN_MOCK_DIR     serve fixture JSON from this directory instead of HTTP

Write safety: every mutating command prints the exact request and exits with
code 3 unless --yes is given. --dry-run always prints and never sends.

Exit codes: 0 ok, 1 usage or setup, 2 API or connection error, 3 write not confirmed.
"""
import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PREFIX = "PANGOLIN"
PRODUCT = "Pangolin"
KEY_VAR = "API_KEY"
# Keys whose values are credentials. Anchored at the end so that passwordId, pincodeId and
# accessTokenId (ids that only say "this is set") stay readable.
SECRET_KEY_RE = re.compile(r"(secret|passphrase|private_?key|hash$|password$|pincode$|token$|api_?key$)", re.I)

# --------------------------------------------------------------------------
# core: output, redaction, HTTP and the write gate. The same block is copied
# into each plugin's CLI (plugins never import from one another).
# --------------------------------------------------------------------------

EXIT_USAGE = 1
EXIT_API = 2
EXIT_NEEDS_CONFIRM = 3
SHOW_SECRETS = False
REDACTED = "<redacted; pass --show-secrets to print>"


def die(msg, code=EXIT_API, kind=None):
    """stderr carries a classified hint: error[auth], error[timeout], error[certificate], error[dns] ..."""
    if kind is None:
        kind = "usage" if code == EXIT_USAGE else "api"
    sys.stderr.write("error[%s]: %s\n" % (kind, msg))
    sys.exit(code)


def env(name, default=None):
    v = os.environ.get("%s_%s" % (PREFIX, name))
    return v if v not in (None, "") else default


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


HEADER_SECRET_RE = re.compile(r"(authorization|cookie|token|secret|api-?key|password)", re.I)


def redact(obj):
    """Replace the values of credential-shaped keys so they do not land in a transcript by default.
    A {"name": ..., "value": ...} pair (an HTTP header) is masked when its name looks like a credential."""
    if isinstance(obj, dict):
        if isinstance(obj.get("name"), str) and obj.get("value") not in (None, "") \
                and HEADER_SECRET_RE.search(obj["name"]):
            return dict(obj, value=REDACTED)
        return {k: (REDACTED if SECRET_KEY_RE.search(str(k)) and not isinstance(v, bool)
                    and v not in (None, "", [], {}) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def emit(obj, table=False, columns=None):
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
        columns = [k for k, v in rows[0].items() if not isinstance(v, (dict, list))][:8]

    def cell(r, c):
        v = get_path(r, c, "")
        return "" if v in (None, "", [], {}) else v

    columns = [c for c in columns if any(cell(r, c) != "" for r in rows)] or columns[:1]
    print("| " + " | ".join(columns) + " |")
    print("|" + "|".join(["---"] * len(columns)) + "|")
    for r in rows:
        cells = []
        for c in columns:
            v = cell(r, c)
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            cells.append(str(v).replace("|", "\\|").replace("\n", " "))
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


def parse_kv(pairs):
    out = {}
    for kv in pairs or []:
        if "=" not in kv:
            die("expected key=value, got %r" % kv, EXIT_USAGE)
        k, v = kv.split("=", 1)
        out[k] = v
    return out


class ApiError(Exception):
    def __init__(self, status, payload):
        super().__init__(status)
        self.status = status
        self.payload = payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the bearer token to wherever it points, so it is reported instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_TLS_WARNED = False


def tls_context():
    global _TLS_WARNED
    ca = env("CA_CERT")
    ctx = ssl.create_default_context(cafile=os.path.expanduser(ca)) if ca else ssl.create_default_context()
    if not truthy(env("VERIFY_TLS", "1")):
        if not _TLS_WARNED:
            sys.stderr.write("warning: %s_VERIFY_TLS=0, the server certificate is not verified\n" % PREFIX)
            _TLS_WARNED = True
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def transport_kind(reason):
    """Classify a connection failure so the caller can say what to fix."""
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "certificate"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, ssl.SSLError):
        return "tls"
    return "network"


TRANSPORT_HINTS = {
    "certificate": "the certificate is not trusted or does not match the name. Use the name on the "
                   "certificate or set %(p)s_CA_CERT; only the user may set %(p)s_VERIFY_TLS=0",
    "timeout": "no answer within %(t)ss. The host may be on a network this machine cannot reach",
    "dns": "the hostname does not resolve. Check %(p)s_HOST",
    "refused": "nothing listens on that port. Check %(p)s_HOST (scheme and port) and that the service is up",
    "tls": "TLS handshake failed. Is this an https endpoint?",
    "network": "check %(p)s_HOST and the network path",
}


def http_json(method, url, headers, body=None, timeout=20):
    data = None
    headers = dict(headers, Accept="application/json")
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls_context()), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        if 300 <= e.code < 400:
            die("%s answered %s with a redirect to %s; set %s_HOST to the final address"
                % (url, e.code, e.headers.get("Location"), PREFIX), kind="redirect")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"message": raw.decode(errors="replace")[:500]}
        if not isinstance(payload, dict):
            payload = {"message": payload}
        if e.code == 429:
            payload["retryAfter"] = e.headers.get("Retry-After")
        raise ApiError(e.code, payload)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError) as e:
        kind = transport_kind(getattr(e, "reason", e))
        die("cannot reach %s (%s): %s" % (url, getattr(e, "reason", e),
                                           TRANSPORT_HINTS[kind] % {"p": PREFIX, "t": timeout}), kind=kind)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        die("%s did not answer with JSON; is %s_HOST the API address? First bytes: %r"
            % (url, PREFIX, raw[:80].decode(errors="replace")), kind="not-json")


def normalize_host(host):
    host = host.strip().rstrip("/")
    if not re.match(r"^https?://", host):
        host = "https://" + host
    if host.startswith("http://"):
        sys.stderr.write("warning: %s_HOST is plain http, the API key travels unencrypted\n" % PREFIX)
    return host


def guarded(api, args, method, path, body=None, query=None):
    """The write gate: print the request; send it only with --yes and without --dry-run."""
    plan = {"dry_run": True, "method": method, "url": api.url(path), "body": body}
    if query:
        plan["query"] = query
    if args.dry_run or not args.yes:
        emit(plan)
        if not args.dry_run:
            sys.stderr.write("\nNot sent. This changes %s. Re-run with --yes after the user has confirmed "
                             "this exact request.\n" % PRODUCT)
            sys.exit(EXIT_NEEDS_CONFIRM)
        return None
    result = api.request(method, path, query, body)
    emit({"applied": True, "method": method, "url": api.url(path), "result": result})
    return result


def api_error_exit(e, kind=None):
    kind = kind or {400: "validation", 401: "auth", 403: "permission", 404: "not-found", 405: "method",
                    409: "conflict", 429: "rate-limit"}.get(e.status, "server" if e.status >= 500 else "api")
    hint = {"auth": " The credential was rejected: check %s_%s." % (PREFIX, KEY_VAR),
            "permission": " The credential lacks a permission for this call.",
            "rate-limit": " Retry after %s s." % e.payload.get("retryAfter")}.get(kind, "")
    sys.stderr.write("error[%s]: HTTP %s.%s %s\n" % (kind, e.status, hint, json.dumps(redact(e.payload))))
    sys.exit(EXIT_API)


# --------------------------------------------------------------------------
# mock backend
# --------------------------------------------------------------------------

# Lists that page with page/pageSize; every other list uses limit/offset.
PAGE_STYLE = ("sites", "resources", "roles", "users", "clients")
ID_FIELD = {"sites": "siteId", "resources": "resourceId", "targets": "targetId", "rules": "ruleId",
            "domains": "domainId", "idps": "idpId", "roles": "roleId", "clients": "clientId", "orgs": "orgId"}
SINGULAR = {"site": "sites", "resource": "resources", "target": "targets", "domain": "domains"}


class MockBackend:
    """Serves fixtures from PANGOLIN_MOCK_DIR and answers with the API's `data` payload.

    One <collection>.json per list (sites, resources, targets, rules, domains, idps, roles, users,
    clients, orgs), org.json for the organization, and resource_<sub>.json keyed by resource id
    (`_default` is the fallback) for roles, users and whitelist. Writes are echoed back and never stored.
    """

    def __init__(self, root):
        self.root = root
        if not os.path.isdir(root):
            die("PANGOLIN_MOCK_DIR %s is not a directory" % root, EXIT_USAGE, "setup")

    def _load(self, name):
        p = os.path.join(self.root, name + ".json")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    def _item(self, coll, ident, field=None):
        for it in self._load(coll) or []:
            if str(it.get(field or ID_FIELD[coll])) == str(ident):
                return it
        raise ApiError(404, {"message": "%s %s not found" % (coll, ident), "status": 404})

    def _page(self, coll, items, query):
        if coll in PAGE_STYLE:
            size, page = int(query.get("pageSize", 20)), int(query.get("page", 1))
            return {coll: items[(page - 1) * size:page * size],
                    "pagination": {"total": len(items), "pageSize": size, "page": page}}
        limit, offset = int(query.get("limit", 1000)), int(query.get("offset", 0))
        return {coll: items[offset:offset + limit],
                "pagination": {"total": len(items), "limit": limit, "offset": offset}}

    @staticmethod
    def _filter(coll, items, query):
        text = (query.get("query") or "").lower()
        if text:
            items = [i for i in items
                     if any(text in str(i.get(k) or "").lower() for k in ("name", "niceId", "fullDomain"))]
        for key in ("enabled", "online"):
            if key in query:
                items = [i for i in items if bool(i.get(key)) == (query[key] == "true")]
        if coll == "resources" and "healthStatus" in query:
            items = [i for i in items if i.get("health") == query["healthStatus"]]
        if coll == "resources" and "siteId" in query:
            items = [i for i in items if any(str(s.get("siteId")) == str(query["siteId"])
                                             for s in i.get("sites") or [])]
        return items

    def request(self, method, path, query=None, body=None):
        query = query or {}
        segs = [s for s in path.split("/") if s]
        if method != "GET":
            return self._write(method, segs, body)
        if not segs:
            return {"message": "Healthy"}
        if segs == ["orgs"] or segs == ["idp"]:
            coll = "orgs" if segs == ["orgs"] else "idps"
            return self._page(coll, self._load(coll) or [], query)
        if segs[0] == "org" and len(segs) == 2:
            return self._load("org") or {}
        if segs[0] == "org" and len(segs) == 3 and self._load(segs[2].replace("-", "_")) is not None:
            coll = segs[2].replace("-", "_")
            return self._page(coll, self._filter(coll, self._load(coll), query), query)
        if segs[0] == "org" and len(segs) == 4 and segs[2] == "site":
            return self._item("sites", segs[3], "niceId")
        if segs[0] == "org" and len(segs) == 4 and segs[2] == "domain":
            return self._item("domains", segs[3])
        if segs[0] in SINGULAR and len(segs) == 2:
            return self._item(SINGULAR[segs[0]], segs[1])
        if segs[0] == "resource" and len(segs) == 3:
            self._item("resources", segs[1])
            if segs[2] in ("targets", "rules"):
                items = [i for i in self._load(segs[2]) or [] if str(i.get("resourceId")) == segs[1]]
                return self._page(segs[2], items, query)
            sub = self._load("resource_" + segs[2])
            if sub is not None:
                return sub.get(segs[1], sub.get("_default", {}))
        raise ApiError(404, {"message": "no fixture for GET /%s" % "/".join(segs), "status": 404})

    def _write(self, method, segs, body):
        if method == "DELETE":
            return None
        if method == "POST" and len(segs) == 2 and segs[0] in SINGULAR:
            return dict(self._item(SINGULAR[segs[0]], segs[1]), **(body or {}))
        out = dict(body or {})
        if method == "PUT" and segs:
            out.setdefault(segs[-1] + "Id", int(time.time()) % 100000)
        return out


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------


def pick_list(data):
    """A list payload is {"<name>": [...], "pagination": {...}}; return the one list in it."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        lists = [v for k, v in data.items() if k != "pagination" and isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return []


class Api:
    def __init__(self, args):
        self.timeout = int(env("TIMEOUT", "20"))
        self._org = getattr(args, "org", None) or env("ORG")
        mock = env("MOCK_DIR")
        self.mock = MockBackend(mock) if mock else None
        if self.mock:
            self.base = "mock://pangolin/v1"
            return
        host, self.key = env("HOST"), env("API_KEY")
        if not host or not self.key:
            die("PANGOLIN_HOST and PANGOLIN_API_KEY must be set (or PANGOLIN_MOCK_DIR for offline use)",
                EXIT_USAGE, "setup")
        host = normalize_host(host)
        self.base = host if re.search(r"/v\d+$", host) else host + "/v1"

    def url(self, path):
        return self.base + path

    def request(self, method, path, query=None, body=None):
        """Send one call and return the envelope's `data`."""
        query = {k: v for k, v in (query or {}).items() if v is not None}
        if self.mock:
            return self.mock.request(method, path, query, body)
        url = self.url(path)
        if query:
            url += "?" + urllib.parse.urlencode(query)
        resp = http_json(method, url, {"Authorization": "Bearer " + self.key}, body, self.timeout)
        if isinstance(resp, dict) and "success" in resp and "data" in resp:
            if resp.get("success") is False:
                raise ApiError(int(resp.get("status") or 500), resp)
            return resp["data"]
        return resp

    def list_all(self, path, query=None, limit=None):
        """Follow either pagination style, read from the `pagination` object each answer carries."""
        query = dict(query or {})
        if path.rsplit("/", 1)[-1] in PAGE_STYLE:
            query.setdefault("pageSize", 100)
        out = []
        while True:
            data = self.request("GET", path, query)
            items = pick_list(data)
            out.extend(items)
            pg = data.get("pagination") if isinstance(data, dict) else None
            if limit and len(out) >= limit:
                return out[:limit]
            if not items or not pg or len(out) >= int(pg.get("total", 0)):
                return out
            if "page" in pg:
                query.update(page=int(pg["page"]) + 1, pageSize=pg.get("pageSize"))
            elif "limit" in pg:
                query.update(offset=int(pg.get("offset", 0)) + len(items), limit=pg["limit"])
            else:
                return out

    @property
    def org(self):
        if self._org:
            return self._org
        try:
            orgs = self.list_all("/orgs")
        except ApiError as e:
            die("PANGOLIN_ORG is not set and this key cannot list organizations (HTTP %s). Set PANGOLIN_ORG "
                "to the organization id, the slug in the dashboard address." % e.status, EXIT_USAGE, "setup")
        if len(orgs) == 1:
            self._org = orgs[0]["orgId"]
            return self._org
        die("PANGOLIN_ORG is not set; this key sees %d organizations: %s"
            % (len(orgs), ", ".join(str(o.get("orgId")) for o in orgs)), EXIT_USAGE, "setup")

    def op(self, suffix):
        return "/org/%s%s" % (urllib.parse.quote(str(self.org), safe=""), suffix)


def resolve_resource(api, ref):
    """A numeric id is used as is; anything else must match one resource's niceId, domain or name exactly.
    The API has no lookup by niceId, and its `query` parameter is a substring filter."""
    if str(ref).isdigit():
        return int(ref)
    hits = api.list_all(api.op("/resources"), {"query": ref})
    exact = [r for r in hits if str(ref).lower() in
             (str(r.get(k) or "").lower() for k in ("niceId", "fullDomain", "name"))]
    if len(exact) == 1:
        return exact[0]["resourceId"]
    names = ", ".join("%s (%s)" % (r.get("niceId"), r.get("resourceId")) for r in (exact or hits)[:10])
    die("%s resource matching %r%s" % ("more than one" if exact else "no", ref,
                                       ("; candidates: " + names) if names else ""), EXIT_USAGE)


def resolve_site(api, ref):
    if str(ref).isdigit():
        return int(ref)
    return api.request("GET", api.op("/site/%s" % urllib.parse.quote(str(ref), safe="")))["siteId"]


def qfilter(args):
    """--filter key=value pairs are passed to the server as query parameters."""
    return parse_kv(getattr(args, "filter", None))


# --------------------------------------------------------------------------
# column presets for --table
# --------------------------------------------------------------------------

COLUMNS = {
    "orgs": ["orgId", "name"],
    "sites": ["name", "niceId", "type", "online", "status", "address", "siteId"],
    "resources": ["name", "fullDomain", "proxyPort", "mode", "enabled", "auth", "health", "targetHealth",
                  "siteNames", "resourceId"],
    "targets": ["siteName", "ip", "port", "method", "enabled", "hcEnabled", "hcHealth", "hcHostname",
                "hcPath", "priority", "targetId"],
    "rules": ["priority", "action", "match", "value", "enabled", "ruleId"],
    "domains": ["baseDomain", "type", "verified", "failed", "errorMessage", "domainId"],
    # idps, roles, users, clients: the first scalar fields of whatever the server returns
}


# --------------------------------------------------------------------------
# command implementations
# --------------------------------------------------------------------------


def cmd_info(api, args):
    out = {"api": api.request("GET", "/"), "base": api.base}
    try:
        out["org"] = api.request("GET", api.op(""))
    except ApiError as e:
        if e.status == 401:
            raise
        out["org"] = {"error": "HTTP %s: %s" % (e.status, e.payload.get("message"))}
    emit(out)


PROBES = (("getOrg", "", None), ("listSites", "/sites", "pageSize"), ("listResources", "/resources", "pageSize"),
          ("listOrgDomains", "/domains", "limit"), ("listRoles", "/roles", "pageSize"),
          ("listUsers", "/users", "pageSize"), ("listClients", "/clients", "pageSize"))


def cmd_access(api, args):
    """Which read actions does this key hold? A key cannot read its own action list, so ask one row of each."""
    rows = []

    def probe(action, path, size):
        try:
            api.request("GET", path, {size: 1} if size else None)
            rows.append({"action": action, "result": "ok", "detail": ""})
        except ApiError as e:
            if e.status == 401:
                raise
            rows.append({"action": action, "result": "denied" if e.status == 403 else "error",
                         "detail": "HTTP %s: %s" % (e.status, e.payload.get("message"))})

    for action, suffix, size in PROBES:
        probe(action, api.op(suffix), size)
    probe("listIdps", "/idp", "limit")
    probe("listOrgs (root keys only)", "/orgs", "limit")
    emit(rows, args.table, ["action", "result", "detail"])


def cmd_orgs(api, args):
    emit(api.list_all("/orgs", limit=args.limit), args.table, COLUMNS["orgs"])


def cmd_sites(api, args):
    v = args.verb
    if v == "list":
        emit(api.list_all(api.op("/sites"), qfilter(args), args.limit), args.table, COLUMNS["sites"])
    elif v == "get":
        if str(args.id).isdigit():
            emit(api.request("GET", "/site/%s" % args.id))
        else:
            emit(api.request("GET", api.op("/site/%s" % urllib.parse.quote(args.id, safe=""))))
    elif v == "create":
        guarded(api, args, "PUT", api.op("/site"), read_body(args.body))
    elif v == "update":
        guarded(api, args, "POST", "/site/%s" % resolve_site(api, args.id), read_body(args.body))
    elif v == "delete":
        query = {"deleteResources": "true"} if args.delete_resources else None
        guarded(api, args, "DELETE", "/site/%s" % resolve_site(api, args.id), query=query)


def enrich_resource(r):
    """Fold the auth and target fields of a list item into columns a table can show."""
    methods = [name for name, key in (("sso", "sso"), ("password", "passwordId"), ("pincode", "pincodeId"),
                                      ("header", "headerAuthId"), ("whitelist", "whitelist")) if r.get(key)]
    r["auth"] = ", ".join(methods) or "none"
    counts = {}
    for t in r.get("targets") or []:
        s = t.get("healthStatus") or "unknown"
        counts[s] = counts.get(s, 0) + 1
    r["targetHealth"] = ", ".join("%d %s" % (n, s) for s, n in sorted(counts.items()))
    r["siteNames"] = ", ".join("%s%s" % (s.get("siteName"), "" if s.get("online") else " (offline)")
                               for s in r.get("sites") or [])
    return r


def cmd_resources(api, args):
    v = args.verb
    if v in ("list", "find"):
        query = qfilter(args)
        if v == "find":
            query["query"] = args.id
        rows = [enrich_resource(r) for r in api.list_all(api.op("/resources"), query, args.limit)]
        emit(rows, args.table, COLUMNS["resources"])
    elif v == "get":
        emit(api.request("GET", "/resource/%s" % resolve_resource(api, args.id)))
    elif v == "auth":
        rid = resolve_resource(api, args.id)
        res = api.request("GET", "/resource/%s" % rid)
        out = {k: res.get(k) for k in ("resourceId", "name", "fullDomain", "enabled", "sso", "blockAccess",
                                       "emailWhitelistEnabled", "applyRules", "skipToIdpId", "resourcePolicyId")}
        for sub in ("roles", "users", "whitelist"):
            try:
                out[sub] = pick_list(api.request("GET", "/resource/%s/%s" % (rid, sub)))
            except ApiError as e:
                out[sub] = {"error": "HTTP %s: %s" % (e.status, e.payload.get("message"))}
        emit(out)
    elif v == "create":
        guarded(api, args, "PUT", api.op("/resource"), read_body(args.body))
    elif v == "update":
        guarded(api, args, "POST", "/resource/%s" % resolve_resource(api, args.id), read_body(args.body))
    elif v in ("enable", "disable"):
        guarded(api, args, "POST", "/resource/%s" % resolve_resource(api, args.id), {"enabled": v == "enable"})
    elif v == "delete":
        guarded(api, args, "DELETE", "/resource/%s" % resolve_resource(api, args.id))


def check_health_fields(body, current=None):
    """The API answers 400 without this; say it before sending."""
    if body.get("hcEnabled") and not (body.get("hcHostname") or (current or {}).get("hcHostname")):
        die("hcHostname is required when hcEnabled is true", EXIT_USAGE)


def cmd_targets(api, args):
    v = args.verb
    if v == "list":
        rows = api.list_all("/resource/%s/targets" % resolve_resource(api, args.id), limit=args.limit)
        emit(rows, args.table, COLUMNS["targets"])
    elif v == "get":
        emit(api.request("GET", "/target/%s" % args.id))
    elif v == "create":
        body = read_body(args.body) or {}
        check_health_fields(body)
        guarded(api, args, "PUT", "/resource/%s/target" % resolve_resource(api, args.id), body)
    elif v == "update":
        # The update schema still requires siteId and ip; carry them over so a one-field change works.
        body = read_body(args.body) or {}
        current = api.request("GET", "/target/%s" % args.id)
        for key in ("siteId", "ip"):
            if key not in body and isinstance(current, dict) and current.get(key) is not None:
                body[key] = current[key]
        check_health_fields(body, current if isinstance(current, dict) else None)
        guarded(api, args, "POST", "/target/%s" % args.id, body)
    elif v == "delete":
        guarded(api, args, "DELETE", "/target/%s" % args.id)


def cmd_rules(api, args):
    v = args.verb
    rid = resolve_resource(api, args.id)
    if v == "list":
        rows = api.list_all("/resource/%s/rules" % rid, limit=args.limit)
        emit(sorted(rows, key=lambda r: r.get("priority") or 0), args.table, COLUMNS["rules"])
    elif v == "create":
        guarded(api, args, "PUT", "/resource/%s/rule" % rid, read_body(args.body))
    elif v == "update":
        # The update schema still requires priority; carry the current one over.
        body = read_body(args.body) or {}
        if "priority" not in body:
            for r in api.list_all("/resource/%s/rules" % rid):
                if str(r.get("ruleId")) == str(args.rule_id):
                    body["priority"] = r.get("priority")
        guarded(api, args, "POST", "/resource/%s/rule/%s" % (rid, args.rule_id), body)
    elif v == "delete":
        guarded(api, args, "DELETE", "/resource/%s/rule/%s" % (rid, args.rule_id))


def cmd_domains(api, args):
    v = args.verb
    if v == "list":
        emit(api.list_all(api.op("/domains"), limit=args.limit), args.table, COLUMNS["domains"])
    elif v == "get":
        emit(api.request("GET", api.op("/domain/%s" % args.id)))
    elif v == "dns-records":
        emit(api.request("GET", api.op("/domain/%s/dns-records" % args.id)))


def cmd_simple_list(api, args):
    path = "/idp" if args.group == "idps" else api.op("/" + args.group)
    emit(api.list_all(path, qfilter(args), args.limit), args.table, COLUMNS.get(args.group))


def cmd_report(api, args):
    """One health snapshot: sites, resources and the targets behind them."""
    sites = api.list_all(api.op("/sites"))
    resources = [enrich_resource(r) for r in api.list_all(api.op("/resources"))]

    def bad_target(r):
        return any(t.get("healthStatus") == "unhealthy" for t in r.get("targets") or [])

    summary = {
        "sitesTotal": len(sites),
        "sitesOffline": [s.get("name") for s in sites if not s.get("online")],
        "resourcesTotal": len(resources),
        "resourcesDisabled": [r.get("name") for r in resources if not r.get("enabled")],
        "resourcesUnhealthy": [r.get("name") for r in resources if r.get("enabled")
                               and (r.get("health") in ("unhealthy", "degraded") or bad_target(r))],
        "resourcesWithoutTargets": [r.get("name") for r in resources if r.get("enabled") and not r.get("targets")],
        "resourcesWithoutAuth": [r.get("name") for r in resources
                                 if r.get("enabled") and r["auth"] == "none" and not r.get("proxyPort")],
    }
    if args.table:
        print("## Summary\n")
        print(json.dumps(summary, indent=2))
        print("\n## Sites\n")
        print_table(sites, COLUMNS["sites"])
        print("\n## Resources\n")
        print_table(resources, COLUMNS["resources"])
    else:
        emit({"summary": summary, "sites": sites, "resources": resources})


def cmd_raw(api, args):
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else "/" + args.path
    if "?" in path:
        die("put query parameters in --query key=value, not in the path", EXIT_USAGE)
    path = re.sub(r"^/v\d+(?=/|$)", "", path)
    if "{orgId}" in path:
        path = path.replace("{orgId}", urllib.parse.quote(str(api.org), safe=""))
    query = parse_kv(args.query)
    if method == "GET":
        emit(api.list_all(path, query, args.limit) if args.all else api.request("GET", path, query))
    else:
        guarded(api, args, method, path, read_body(args.body), query or None)


# --------------------------------------------------------------------------
# argparse wiring
# --------------------------------------------------------------------------


def add_common(p, write=False, listing=False):
    p.add_argument("--org", help="organization id (default: $PANGOLIN_ORG)")
    p.add_argument("--table", action="store_true", help="markdown table instead of JSON")
    p.add_argument("--show-secrets", action="store_true",
                   help="print secrets instead of redacting them (the output then holds live credentials)")
    if listing:
        p.add_argument("--filter", action="append", metavar="KEY=VALUE",
                       help="server-side query parameter, repeatable, e.g. --filter enabled=false")
        p.add_argument("--limit", type=int, help="stop after N items (default: all)")
    if write:
        p.add_argument("--yes", action="store_true", help="actually send the mutating request")
        p.add_argument("--dry-run", action="store_true", help="print the request and never send")
        p.add_argument("--body", help="JSON body: file path, '-' for stdin, or inline JSON")


NEEDS_ID = {
    "sites": {"get", "update", "delete"},
    "resources": {"get", "find", "auth", "update", "enable", "disable", "delete"},
    "targets": {"list", "get", "create", "update", "delete"},
    "rules": {"list", "create", "update", "delete"},
    "domains": {"get", "dns-records"},
}
NEEDS_BODY = {"sites": {"create", "update"}, "resources": {"create", "update"},
              "targets": {"create", "update"}, "rules": {"create", "update"}}


def build_parser():
    ap = argparse.ArgumentParser(prog="pangolin.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="group", required=True)

    p = sub.add_parser("info", help="API health and the organization this key works in")
    add_common(p)
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("access", help="which read actions the key holds (one small GET per action)")
    add_common(p)
    p.set_defaults(fn=cmd_access)

    p = sub.add_parser("orgs", help="organizations (root keys only)")
    p.add_argument("verb", choices=["list"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_orgs)

    p = sub.add_parser("sites", help="sites: the tunnel endpoints targets live behind")
    p.add_argument("verb", choices=["list", "get", "create", "update", "delete"])
    p.add_argument("id", nargs="?", help="site id or niceId")
    p.add_argument("--delete-resources", action="store_true", help="with delete: also delete the site's resources")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_sites)

    p = sub.add_parser("resources", help="public resources: what is published, and how it is protected")
    p.add_argument("verb", choices=["list", "get", "find", "auth", "create", "update", "enable", "disable", "delete"])
    p.add_argument("id", nargs="?", help="resource id, niceId or full domain (search text for 'find')")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_resources)

    p = sub.add_parser("targets", help="targets of a resource, including their health checks")
    p.add_argument("verb", choices=["list", "get", "create", "update", "delete"])
    p.add_argument("id", nargs="?", help="resource (list, create) or target id (get, update, delete)")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_targets)

    p = sub.add_parser("rules", help="access rules of an HTTP resource")
    p.add_argument("verb", choices=["list", "create", "update", "delete"])
    p.add_argument("id", nargs="?", help="resource id, niceId or full domain")
    p.add_argument("rule_id", nargs="?", help="rule id (update, delete)")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_rules)

    p = sub.add_parser("domains", help="domains and their verification state")
    p.add_argument("verb", choices=["list", "get", "dns-records"])
    p.add_argument("id", nargs="?", help="domain id")
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_domains)

    for name in ("idps", "roles", "users", "clients"):
        p = sub.add_parser(name, help="list %s" % name)
        p.add_argument("verb", choices=["list"])
        add_common(p, listing=True)
        p.set_defaults(fn=cmd_simple_list)

    p = sub.add_parser("report", help="aggregated health snapshot")
    p.add_argument("verb", choices=["health"])
    add_common(p)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("raw", help="any Integration API call, e.g. raw GET /org/{orgId}/domains")
    p.add_argument("method")
    p.add_argument("path", help="path under /v1; {orgId} is replaced")
    p.add_argument("--query", nargs="*", metavar="KEY=VALUE", help="query parameters")
    p.add_argument("--all", action="store_true", help="GET only: follow pagination and print the items")
    p.add_argument("--limit", type=int, help="with --all: stop after N items")
    add_common(p, write=True)
    p.set_defaults(fn=cmd_raw)
    return ap


def main(argv=None):
    global SHOW_SECRETS
    args = build_parser().parse_args(argv)
    SHOW_SECRETS = bool(getattr(args, "show_secrets", False))
    for attr, default in (("yes", False), ("dry_run", False), ("body", None), ("filter", None), ("limit", None),
                          ("table", False), ("id", None), ("verb", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    if args.verb in NEEDS_ID.get(args.group, ()) and not args.id:
        die("%s %s needs an id" % (args.group, args.verb), EXIT_USAGE)
    if args.group == "rules" and args.verb in ("update", "delete") and not args.rule_id:
        die("rules %s needs <resource> <rule id>" % args.verb, EXIT_USAGE)
    if args.verb in NEEDS_BODY.get(args.group, ()) and not args.body:
        die("%s %s needs --body <json>" % (args.group, args.verb), EXIT_USAGE)
    try:
        args.fn(Api(args), args)
    except ApiError as e:
        api_error_exit(e)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()

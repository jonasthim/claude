#!/usr/bin/env python3
"""authentik.py - stdlib-only CLI for the authentik REST API (/api/v3).

Output is JSON on stdout by default so the caller (usually Claude) can format
it. Pass --table for a markdown table of a list command.

Environment:
  AUTHENTIK_HOST         address of the authentik server, e.g. https://auth.example.com
                         ("/api/v3" is added when absent)
  AUTHENTIK_TOKEN        an API token (a token with intent "api"; an app password does not work)
  AUTHENTIK_CA_CERT      CA bundle for a privately signed certificate
  AUTHENTIK_VERIFY_TLS   "0" skips certificate verification (the user's choice, never the caller's)
  AUTHENTIK_TIMEOUT      HTTP timeout in seconds, default 20
  AUTHENTIK_MOCK_DIR     serve fixture JSON from this directory instead of HTTP

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

PREFIX = "AUTHENTIK"
PRODUCT = "authentik"
KEY_VAR = "TOKEN"
# Keys whose values are credentials. authentik returns OAuth2 client secrets in every provider
# list. /admin/system/ echoes the request environment under http_headers (HTTP_AUTHORIZATION,
# HTTP_COOKIE and more), so that object is masked whole. token_identifier, signing_key and
# authorization_flow are names or references, not secrets: "token", "key" and "authorization"
# only match as a whole key, as a suffix, or in the forms known to hold a credential.
SECRET_KEY_RE = re.compile(r"(secret|password|passphrase|private_?key|kubeconfig|credentials|^http_headers$"
                           r"|authorization$|cookie$|sentry_dsn|webhook_url|_token$|^token$|^key$|^link$"
                           r"|api_?key$|access_key|integration_key|^auth$|^dsn$)", re.I)

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


HEADER_SECRET_RE = re.compile(r"(auth|cookie|token|secret|key|password|credential|session|signature)", re.I)
# Lists of headers that are sent to a backend. Operators put credentials there under any name,
# so every value is masked and the names stay readable. Response headers are not matched.
REQUEST_HEADERS_RE = re.compile(r"^(hc_?|request_?)?headers$", re.I)


def mask_header(h):
    return dict(h, value=REDACTED) if isinstance(h, dict) and h.get("value") not in (None, "") else h


def redact(obj):
    """Replace the values of credential-shaped keys so they do not land in a transcript by default.
    A {"name": ..., "value": ...} pair (an HTTP header) is masked when its name looks like a credential,
    and always inside a list of request headers."""
    if isinstance(obj, dict):
        if isinstance(obj.get("name"), str) and HEADER_SECRET_RE.search(obj["name"]):
            return mask_header(obj)
        out = {}
        for k, v in obj.items():
            if SECRET_KEY_RE.search(str(k)) and not isinstance(v, bool) and v not in (None, "", [], {}):
                out[k] = REDACTED
            elif REQUEST_HEADERS_RE.match(str(k)) and isinstance(v, list):
                out[k] = [mask_header(h) for h in v]
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


class Scrubbed:
    """Wraps stdout and stderr: the credential this tool authenticates with is never printed,
    whatever key it turns up under and even with --show-secrets (servers echo request headers,
    and error pages quote them)."""

    def __init__(self, stream, secrets):
        self.stream = stream
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def write(self, text):
        for s in self.secrets:
            text = text.replace(s, "<this tool's own credential>")
        return self.stream.write(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def scrub_own_credential():
    key = env(KEY_VAR) or ""
    parts = [key] + [p for p in key.split(".") if len(p) >= 12]  # "<id>.<secret>" keys: each half too
    sys.stdout, sys.stderr = Scrubbed(sys.stdout, parts), Scrubbed(sys.stderr, parts)


def emit(obj, table=False, columns=None):
    if not SHOW_SECRETS:
        obj = redact(obj)
    if table and isinstance(obj, list):
        print_table(obj, columns)
    else:
        print(json.dumps(obj, indent=2, sort_keys=False))


def emit_table(rows, columns=None):
    """A table printed outside emit() goes through the same redaction."""
    print_table(rows if SHOW_SECRETS else redact(rows), columns)


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
            # Not the API's own error format (a proxy's page, say): keep it short, it is not redacted by key.
            payload = {"message": " ".join(raw.decode(errors="replace").split())[:200]}
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
        die("%s answered %d bytes that are not JSON; is %s_HOST the API address?"
            % (url, len(raw), PREFIX), kind="not-json")


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

# API path -> (fixture, field that addresses one item)
COLLECTIONS = {
    "core/applications": ("applications", "slug"),
    "providers/all": ("providers", "pk"),
    "core/groups": ("groups", "pk"),
    "core/users": ("users", "pk"),
    "core/tokens": ("tokens", "identifier"),
    "policies/bindings": ("bindings", "pk"),
    "policies/all": ("policies", "pk"),
    "flows/instances": ("flows", "slug"),
    "outposts/instances": ("outposts", "pk"),
    "events/events": ("events", "pk"),
    "propertymappings/provider/scope": ("scope_mappings", "pk"),
}


class MockBackend:
    """Serves fixtures from AUTHENTIK_MOCK_DIR in the API's own shapes (paginated lists included).

    One <collection>.json per list, me.json and version.json for the connection check, and
    check_access.json / outpost_health.json keyed by "<slug>:<user pk>" and outpost uuid
    (`_default` is the fallback). Writes are echoed back and never stored.
    """

    def __init__(self, root):
        self.root = root
        if not os.path.isdir(root):
            die("AUTHENTIK_MOCK_DIR %s is not a directory" % root, EXIT_USAGE, "setup")

    def _load(self, name):
        p = os.path.join(self.root, name + ".json")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    def _route(self, segs):
        """Longest collection prefix; providers/<type> reads the shared providers fixture."""
        for n in (3, 2):
            key = "/".join(segs[:n])
            if key in COLLECTIONS:
                return COLLECTIONS[key] + (segs[n:], None)
        if len(segs) >= 2 and segs[0] == "providers":
            return ("providers", "pk", segs[2:], segs[1])
        return None

    @staticmethod
    def _filter(items, query, ptype):
        if ptype:
            items = [i for i in items if ptype in str(i.get("meta_model_name", ""))]
        for key, want in query.items():
            if key in ("page", "page_size", "superuser_full_list", "include_users"):
                continue
            if key == "ordering":
                field = want.lstrip("-")
                items = sorted(items, key=lambda i: str(i.get(field) or ""), reverse=want.startswith("-"))
            elif key == "search":
                items = [i for i in items if want.lower() in json.dumps(i).lower()]
            elif key == "application__isnull":
                items = [i for i in items if (not i.get("assigned_application_slug")) == (want == "true")]
            elif key == "username" and items and "username" not in items[0]:
                items = [i for i in items if (i.get("user") or {}).get("username") == want]
            elif key in ("name__iexact", "managed__icontains"):
                field = key.split("__")[0]
                items = [i for i in items if want.lower() in str(i.get(field) or "").lower()]
            elif key.startswith("context_"):
                items = [i for i in items if want.lower() in json.dumps(i.get("context") or {}).lower()]
            else:
                items = [i for i in items if str(i.get(key)).lower() == str(want).lower()]
        return items

    @staticmethod
    def _page(items, query):
        size = min(int(query.get("page_size", 20)), 100)
        page = int(query.get("page", 1))
        pages = max(1, -(-len(items) // size))
        chunk = items[(page - 1) * size:page * size]
        return {"pagination": {"next": page + 1 if page < pages else 0, "previous": page - 1, "count": len(items),
                               "current": page, "total_pages": pages,
                               "start_index": (page - 1) * size + 1 if chunk else 0,
                               "end_index": (page - 1) * size + len(chunk)},
                "results": chunk, "autocomplete": {}}

    def request(self, method, path, query=None, body=None):
        query = query or {}
        segs = [s for s in path.split("/") if s]
        if segs == ["core", "users", "me"]:
            return self._load("me")
        if segs == ["admin", "version"]:
            return self._load("version")
        if segs == ["admin", "system"]:
            return self._load("system")
        if segs == ["core", "transactional", "applications"] and method == "PUT":
            return {"applied": True, "logs": []}
        route = self._route(segs)
        if route is None or self._load(route[0]) is None:
            raise ApiError(404, {"detail": "Not found."})
        fixture, id_field, rest, ptype = route
        items = self._load(fixture)
        if not rest:
            if method == "GET":
                return self._page(self._filter(items, query, ptype), query)
            return dict(body or {}, pk="mock-%d" % (int(time.time()) % 100000))
        item = next((i for i in items if str(i.get(id_field)) == rest[0]), None)
        if item is None:
            raise ApiError(404, {"detail": "Not found."})
        sub = rest[1:]
        if method == "GET" and not sub:
            return item
        if method == "GET" and sub == ["check_access"]:
            table = self._load("check_access") or {}
            return table.get("%s:%s" % (rest[0], query.get("for_user", "")), table.get("_default"))
        if method == "GET" and sub == ["health"]:
            table = self._load("outpost_health") or {}
            return table.get(rest[0], table.get("_default", []))
        if method == "GET" and sub == ["used_by"]:
            return []
        if method == "GET" and sub == ["setup_urls"]:
            base = "https://auth.example.com/application/o/%s/" % (item.get("assigned_application_slug") or "app")
            return {"issuer": base, "provider_info": base + ".well-known/openid-configuration",
                    "authorize": "https://auth.example.com/application/o/authorize/",
                    "token": "https://auth.example.com/application/o/token/",
                    "user_info": "https://auth.example.com/application/o/userinfo/", "jwks": base + "jwks/"}
        if method in ("PUT", "PATCH") and not sub:
            return dict(item, **(body or {}))
        if method in ("DELETE", "POST"):
            return None
        raise ApiError(404, {"detail": "Not found."})


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------

AUTH_DETAILS = ("token invalid/expired", "malformed header", "credentials were not provided")


def error_kind(e):
    """authentik answers 403 for a bad token as well as for a missing permission; the detail tells them apart."""
    detail = str(e.payload.get("detail", "")).lower()
    if e.status == 403 and any(d in detail for d in AUTH_DETAILS):
        return "auth"
    return None


class Api:
    def __init__(self, args):
        self.timeout = int(env("TIMEOUT", "20"))
        mock = env("MOCK_DIR")
        self.mock = MockBackend(mock) if mock else None
        self._me = None
        if self.mock:
            self.base = "mock://authentik/api/v3"
            return
        host, self.key = env("HOST"), env("TOKEN")
        if not host or not self.key:
            die("AUTHENTIK_HOST and AUTHENTIK_TOKEN must be set (or AUTHENTIK_MOCK_DIR for offline use)",
                EXIT_USAGE, "setup")
        host = normalize_host(host)
        self.base = host if re.search(r"/api/v\d+$", host) else host + "/api/v3"

    def url(self, path):
        return self.base + path

    def request(self, method, path, query=None, body=None):
        query = {k: v for k, v in (query or {}).items() if v is not None}
        if self.mock:
            return self.mock.request(method, path, query, body)
        url = self.url(path)
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        return http_json(method, url, {"Authorization": "Bearer " + self.key}, body, self.timeout)

    def list_all(self, path, query=None, limit=None):
        """Follow page/page_size pagination. The server clamps page_size (100 by default)."""
        query = dict(query or {})
        query.setdefault("page_size", min(limit, 100) if limit else 100)
        out = []
        while True:
            resp = self.request("GET", path, query)
            if isinstance(resp, list):
                return resp[:limit] if limit else resp
            items = resp.get("results", [])
            out.extend(items)
            pg = resp.get("pagination") or {}
            if limit and len(out) >= limit:
                return out[:limit]
            if not items or int(pg.get("current", 1)) >= int(pg.get("total_pages", 1)):
                return out
            query["page"] = int(pg.get("current", 1)) + 1

    def me(self):
        if self._me is None:
            self._me = self.request("GET", "/core/users/me/").get("user") or {}
        return self._me

    def one(self, path, query, what, ref):
        """Exactly one row of a filtered list. Lists are permission-filtered, so say who is asking."""
        hits = self.list_all(path, query)
        if len(hits) == 1:
            return hits[0]
        if not hits:
            die("no %s %r visible to %s (lists only show what the token's user may view)"
                % (what, ref, self.me().get("username", "this token")), EXIT_API, "not-found")
        die("%d %ss match %r" % (len(hits), what, ref), EXIT_USAGE)


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def seg(value):
    return urllib.parse.quote(str(value), safe="")


def user_pk(api, ref):
    return int(ref) if str(ref).isdigit() else api.one("/core/users/", {"username": ref}, "user", ref)["pk"]


def group_pk(api, ref):
    return ref if UUID_RE.match(str(ref)) else api.one("/core/groups/", {"name": ref}, "group", ref)["pk"]


def outpost_pk(api, ref):
    if UUID_RE.match(str(ref)):
        return ref
    return api.one("/outposts/instances/", {"name__iexact": ref}, "outpost", ref)["pk"]


def qfilter(args, **defaults):
    """--filter key=value pairs are passed to the server as query parameters."""
    out = dict(defaults)
    out.update(parse_kv(getattr(args, "filter", None)))
    return out


# --------------------------------------------------------------------------
# column presets for --table
# --------------------------------------------------------------------------

COLUMNS = {
    "apps": ["name", "slug", "provider_obj.name", "policy_engine_mode", "group", "meta_launch_url"],
    "providers": ["name", "verbose_name", "assigned_application_slug", "assigned_backchannel_application_slug", "pk"],
    "groups": ["name", "is_superuser", "pk"],
    "users": ["username", "name", "email", "is_active", "is_superuser", "type", "last_login", "pk"],
    "bindings": ["order", "enabled", "negate", "kind", "name", "timeout", "failure_result", "pk"],
    "policies": ["name", "verbose_name", "bound_to", "execution_logging", "pk"],
    "flows": ["name", "slug", "designation", "policy_engine_mode", "pk"],
    "outposts": ["name", "type", "providers", "managed", "pk"],
    "events": ["created", "action", "username", "client_ip", "app", "summary"],
    "mappings": ["name", "scope_name", "managed", "pk"],
    "tokens": ["identifier", "intent", "user_obj.username", "expiring", "expires", "managed"],
}


# --------------------------------------------------------------------------
# command implementations
# --------------------------------------------------------------------------


def cmd_info(api, args):
    me = api.me()
    out = {"base": api.base,
           "user": {k: me.get(k) for k in ("username", "pk", "type", "is_superuser", "is_active")}}
    out["user"]["groups"] = [g.get("name") for g in me.get("groups") or []]
    out["user"]["system_permissions"] = me.get("system_permissions")
    try:
        out["version"] = api.request("GET", "/admin/version/")
    except ApiError as e:
        out["version"] = {"error": "HTTP %s: %s" % (e.status, e.payload.get("detail"))}
    emit(out)


def crud(api, args, base, cols, key="id"):
    """list / get / create / update / patch / delete / used-by for one collection."""
    v = args.verb
    item = lambda suffix="": "%s%s/%s" % (base, seg(getattr(args, key)), suffix)  # noqa: E731
    if v == "list":
        emit(api.list_all(base, qfilter(args), args.limit), args.table, cols)
    elif v == "get":
        emit(api.request("GET", item()))
    elif v == "used-by":
        emit(api.request("GET", item("used_by/")), args.table)
    elif v == "create":
        guarded(api, args, "POST", base, read_body(args.body))
    elif v == "update":
        guarded(api, args, "PUT", item(), read_body(args.body))
    elif v == "patch":
        guarded(api, args, "PATCH", item(), read_body(args.body))
    elif v == "delete":
        guarded(api, args, "DELETE", item())
    else:
        return False
    return True


def binding_row(b):
    """Say in one row what a binding checks: a policy, a group or a user."""
    for kind in ("policy", "group", "user"):
        obj = b.get(kind + "_obj")
        if b.get(kind) is not None or obj:
            b["kind"] = kind
            b["name"] = (obj or {}).get("name") or (obj or {}).get("username") or b.get(kind)
            break
    return b


def app_bindings(api, slug):
    app = api.request("GET", "/core/applications/%s/" % seg(slug))
    rows = api.list_all("/policies/bindings/", {"target": app.get("pbm_uuid"), "ordering": "order"})
    rows = [binding_row(b) for b in rows]
    enabled = [b for b in rows if b.get("enabled", True)]
    mode = app.get("policy_engine_mode")
    if not enabled:
        meaning = "no enabled bindings: every user who can log in may open it"
    elif mode == "all":
        meaning = "all: a user must pass every enabled binding"
    else:
        meaning = "any: a user must pass at least one enabled binding"
    return {"application": app.get("slug"), "pbm_uuid": app.get("pbm_uuid"), "policy_engine_mode": mode,
            "meaning": meaning, "bindings": rows}


def cmd_apps(api, args):
    v = args.verb
    if v == "list":
        query = qfilter(args)
        if "for_user" not in query:  # for_user asks for the policy-filtered list of one user
            query.setdefault("superuser_full_list", "true")
        emit(api.list_all("/core/applications/", query, args.limit), args.table, COLUMNS["apps"])
    elif v == "bindings":
        out = app_bindings(api, args.id)
        if args.table:
            print("policy_engine_mode: %s (%s)\n" % (out["policy_engine_mode"], out["meaning"]))
            emit_table(out["bindings"], COLUMNS["bindings"])
        else:
            emit(out)
    elif v == "access":
        query = None
        if args.user:
            # The API silently ignores for_user for a non-superuser and checks the token's own user.
            if not api.me().get("is_superuser"):
                die("checking access for another user needs a superuser token; %s is not one, and the API "
                    "would answer for %s itself without saying so" % ((api.me().get("username"),) * 2), EXIT_USAGE)
            query = {"for_user": user_pk(api, args.user)}
        out = api.request("GET", "/core/applications/%s/check_access/" % seg(args.id), query)
        emit(dict(out or {}, checked_user=args.user or api.me().get("username")))
    elif v == "create-with-provider":
        result = guarded(api, args, "PUT", "/core/transactional/applications/", read_body(args.body))
        if result is not None and not result.get("applied"):
            logs = result.get("logs") if SHOW_SECRETS else redact(result.get("logs"))
            die("authentik answered 200 but applied nothing: %s" % json.dumps(logs), kind="not-applied")
    else:
        crud(api, args, "/core/applications/", COLUMNS["apps"])


def cmd_providers(api, args):
    v = args.verb
    typed = "/providers/%s/" % seg(args.type) if args.type else None
    if v == "list":
        emit(api.list_all(typed or "/providers/all/", qfilter(args), args.limit), args.table, COLUMNS["providers"])
    elif v == "setup-urls":
        emit(api.request("GET", "/providers/oauth2/%s/setup_urls/" % seg(args.id)))
    elif v in ("get", "used-by", "delete"):
        crud(api, args, typed or "/providers/all/", COLUMNS["providers"])
    else:
        if not typed:
            die("providers %s needs --type (oauth2, proxy, ldap, saml, ...): /providers/all/ is read and "
                "delete only" % v, EXIT_USAGE)
        crud(api, args, typed, COLUMNS["providers"])


def cmd_groups(api, args):
    v = args.verb
    if v in ("add-user", "remove-user"):
        if not args.user:
            die("groups %s needs <group> <user>" % v, EXIT_USAGE)
        path = "/core/groups/%s/%s/" % (seg(group_pk(api, args.id)), v.replace("-", "_"))
        guarded(api, args, "POST", path, {"pk": user_pk(api, args.user)})
        return
    if v == "members":
        group = api.request("GET", "/core/groups/%s/" % seg(group_pk(api, args.id)), {"include_users": "true"})
        emit(group.get("users_obj") or [], args.table, ["username", "name", "email", "is_active", "pk"])
        return
    if args.id and v != "create":
        args.id = group_pk(api, args.id)
    crud(api, args, "/core/groups/", COLUMNS["groups"])


def cmd_users(api, args):
    v = args.verb
    if v == "list":
        emit(api.list_all("/core/users/", qfilter(args), args.limit), args.table, COLUMNS["users"])
    elif v == "find":
        emit(api.list_all("/core/users/", {"search": args.id}, args.limit), args.table, COLUMNS["users"])
    elif v == "get":
        emit(api.request("GET", "/core/users/%s/" % user_pk(api, args.id)))


def cmd_bindings(api, args):
    if args.verb == "list":
        query = qfilter(args, ordering="order")
        if args.target:
            query["target"] = args.target
        rows = [binding_row(b) for b in api.list_all("/policies/bindings/", query, args.limit)]
        emit(rows, args.table, COLUMNS["bindings"])
        return
    crud(api, args, "/policies/bindings/", COLUMNS["bindings"])


def cmd_policies(api, args):
    crud(api, args, "/policies/all/", COLUMNS["policies"])


def cmd_flows(api, args):
    crud(api, args, "/flows/instances/", COLUMNS["flows"])


def outpost_health(api, pk):
    try:
        return api.request("GET", "/outposts/instances/%s/health/" % seg(pk))
    except ApiError as e:
        return {"error": "HTTP %s: %s" % (e.status, e.payload.get("detail"))}


def cmd_outposts(api, args):
    v = args.verb
    if v == "list":
        emit(api.list_all("/outposts/instances/", qfilter(args), args.limit), args.table, COLUMNS["outposts"])
    elif v == "get":
        emit(api.request("GET", "/outposts/instances/%s/" % seg(outpost_pk(api, args.id))))
    elif v == "health":
        emit(outpost_health(api, outpost_pk(api, args.id)), args.table,
             ["hostname", "version", "version_should", "version_outdated", "last_seen"])


def event_row(e):
    e["username"] = (e.get("user") or {}).get("username", "")
    ctx = e.get("context") or {}
    app = ctx.get("authorized_application") or ctx.get("application") or {}
    e["summary"] = ctx.get("message") or (app.get("name") if isinstance(app, dict) else "") or ""
    return e


def cmd_events(api, args):
    """Newest first; 50 unless --limit says otherwise, since an event log has no natural end."""
    query = qfilter(args, ordering="-created")
    rows = [event_row(e) for e in api.list_all("/events/events/", query, args.limit or 50)]
    emit(rows, args.table, COLUMNS["events"])


def cmd_simple_list(api, args):
    path = {"mappings": "/propertymappings/provider/scope/", "tokens": "/core/tokens/"}[args.group]
    emit(api.list_all(path, qfilter(args), args.limit), args.table, COLUMNS[args.group])


WATCHED_ACTIONS = ("login_failed", "configuration_error", "policy_exception", "property_mapping_exception",
                   "system_exception", "system_task_exception", "suspicious_request")


def cmd_report(api, args):
    """One health snapshot: version, outposts, wiring between applications and providers, recent trouble."""
    out = {"user": api.me().get("username"), "superuser": api.me().get("is_superuser")}
    try:
        out["version"] = api.request("GET", "/admin/version/")
    except ApiError as e:
        out["version"] = {"error": "HTTP %s" % e.status}
    apps = api.list_all("/core/applications/", {"superuser_full_list": "true"})
    providers = api.list_all("/providers/all/")
    bound = {}
    for b in api.list_all("/policies/bindings/"):
        if b.get("enabled", True):
            bound[b.get("target")] = bound.get(b.get("target"), 0) + 1
    outposts = []
    assigned = set()
    for o in api.list_all("/outposts/instances/"):
        health = outpost_health(api, o.get("pk"))
        assigned.update(o.get("providers") or [])
        outposts.append({"name": o.get("name"), "type": o.get("type"), "providers": len(o.get("providers") or []),
                         "instances": len(health) if isinstance(health, list) else health,
                         "outdated": [h.get("hostname") for h in health if h.get("version_outdated")]
                         if isinstance(health, list) else []})
    recent = api.list_all("/events/events/", {"ordering": "-created"}, 200)
    counts = {}
    for e in recent:
        if e.get("action") in WATCHED_ACTIONS:
            counts[e["action"]] = counts.get(e["action"], 0) + 1
    needs_outpost = ("proxy", "ldap", "radius", "rac")
    out.update({
        "applications": len(apps),
        "applicationsWithoutProvider": [a.get("slug") for a in apps if not a.get("provider")],
        "applicationsOpenToAllUsers": [a.get("slug") for a in apps if not bound.get(a.get("pbm_uuid"))],
        "applicationsRequiringEveryBinding": [a.get("slug") for a in apps if a.get("policy_engine_mode") == "all"
                                              and bound.get(a.get("pbm_uuid"), 0) > 1],
        "providers": len(providers),
        "providersWithoutApplication": [p.get("name") for p in providers
                                        if not p.get("assigned_application_slug")
                                        and not p.get("assigned_backchannel_application_slug")],
        "providersNotOnAnOutpost": [p.get("name") for p in providers if p.get("pk") not in assigned
                                    and any(t in str(p.get("meta_model_name", "")) for t in needs_outpost)],
        "outposts": outposts,
        "recentEvents": {"sampled": len(recent), "oldest": recent[-1].get("created") if recent else None,
                         "counts": counts},
    })
    emit(out)


def cmd_raw(api, args):
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else "/" + args.path
    if "?" in path:
        die("put query parameters in --query key=value, not in the path", EXIT_USAGE)
    path = re.sub(r"^/api/v\d+(?=/|$)", "", path)
    if not path.split("?")[0].endswith("/"):
        path += "/"  # every authentik path ends with a slash; without it a write is redirected and lost
    if "view_private_key" in path or "export" in path:
        global SECRET_KEY_RE
        SECRET_KEY_RE = re.compile(SECRET_KEY_RE.pattern + r"|^data$", re.I)
    query = parse_kv(args.query)
    if method == "GET":
        emit(api.list_all(path, query, args.limit) if args.all else api.request("GET", path, query), args.table)
    else:
        guarded(api, args, method, path, read_body(args.body), query or None)


# --------------------------------------------------------------------------
# argparse wiring
# --------------------------------------------------------------------------


def add_common(p, write=False, listing=False):
    p.add_argument("--table", action="store_true", help="markdown table instead of JSON")
    p.add_argument("--show-secrets", action="store_true",
                   help="print secrets instead of redacting them (the output then holds live credentials)")
    if listing:
        p.add_argument("--filter", action="append", metavar="KEY=VALUE",
                       help="server-side query parameter, repeatable, e.g. --filter name=wiki-users")
        p.add_argument("--limit", type=int, help="stop after N items (default: all; events: 50)")
    if write:
        p.add_argument("--yes", action="store_true", help="actually send the mutating request")
        p.add_argument("--dry-run", action="store_true", help="print the request and never send")
        p.add_argument("--body", help="JSON body: file path, '-' for stdin, or inline JSON")


CRUD = ["list", "get", "create", "update", "patch", "delete", "used-by"]
NEEDS_ID = {
    "apps": {"get", "bindings", "access", "update", "patch", "delete", "used-by"},
    "providers": {"get", "setup-urls", "update", "patch", "delete", "used-by"},
    "groups": {"get", "members", "update", "patch", "delete", "used-by", "add-user", "remove-user"},
    "users": {"get", "find"},
    "bindings": {"get", "update", "patch", "delete", "used-by"},
    "policies": {"get", "delete", "used-by"}, "flows": {"get", "used-by"},
    "outposts": {"get", "health"},
}
NEEDS_BODY = {"create", "update", "patch", "create-with-provider"}


def build_parser():
    ap = argparse.ArgumentParser(prog="authentik.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="group", required=True)

    p = sub.add_parser("info", help="who this token is, and the server version")
    add_common(p)
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("apps", help="applications, who may open them, and why")
    p.add_argument("verb", choices=CRUD + ["bindings", "access", "create-with-provider"])
    p.add_argument("id", nargs="?", help="application slug")
    p.add_argument("--user", help="with access: check for this user (pk or username; superuser tokens only)")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_apps)

    p = sub.add_parser("providers", help="providers of every type; writes need --type")
    p.add_argument("verb", choices=CRUD + ["setup-urls"])
    p.add_argument("id", nargs="?", help="provider id (integer)")
    p.add_argument("--type", help="oauth2, proxy, ldap, saml, radius, scim, rac ...")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_providers)

    p = sub.add_parser("groups", help="groups and their members")
    p.add_argument("verb", choices=CRUD + ["members", "add-user", "remove-user"])
    p.add_argument("id", nargs="?", help="group uuid or exact name")
    p.add_argument("user", nargs="?", help="user pk or username (add-user, remove-user)")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_groups)

    p = sub.add_parser("users", help="users (read only; change them in authentik or with raw)")
    p.add_argument("verb", choices=["list", "get", "find"])
    p.add_argument("id", nargs="?", help="user pk or username (search text for 'find')")
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_users)

    p = sub.add_parser("bindings", help="policy bindings: which policy, group or user gates a target")
    p.add_argument("verb", choices=CRUD)
    p.add_argument("id", nargs="?", help="binding uuid")
    p.add_argument("--target", help="with list: the pbm_uuid of an application, flow or stage binding")
    add_common(p, write=True, listing=True)
    p.set_defaults(fn=cmd_bindings)

    for name, fn, verbs in (("policies", cmd_policies, ["list", "get", "delete", "used-by"]),
                            ("flows", cmd_flows, ["list", "get", "used-by"])):
        p = sub.add_parser(name, help="%s: %s" % (name, ", ".join(verbs)))
        p.add_argument("verb", choices=verbs)
        p.add_argument("id", nargs="?", help="policy uuid / flow slug")
        add_common(p, write=name == "policies", listing=True)
        p.set_defaults(fn=fn)

    p = sub.add_parser("outposts", help="outposts and whether an instance is connected")
    p.add_argument("verb", choices=["list", "get", "health"])
    p.add_argument("id", nargs="?", help="outpost uuid or name")
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_outposts)

    p = sub.add_parser("events", help="the event log, newest first")
    p.add_argument("verb", choices=["list"])
    add_common(p, listing=True)
    p.set_defaults(fn=cmd_events)

    for name in ("mappings", "tokens"):
        p = sub.add_parser(name, help="list %s" % ("OAuth2 scope mappings" if name == "mappings"
                                                  else "tokens (never their keys)"))
        p.add_argument("verb", choices=["list"])
        add_common(p, listing=True)
        p.set_defaults(fn=cmd_simple_list)

    p = sub.add_parser("report", help="aggregated health snapshot")
    p.add_argument("verb", choices=["health"])
    add_common(p)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("raw", help="any API call, e.g. raw GET /core/brands/")
    p.add_argument("method")
    p.add_argument("path", help="path under /api/v3")
    p.add_argument("--query", nargs="*", metavar="KEY=VALUE", help="query parameters")
    p.add_argument("--all", action="store_true", help="GET only: follow pagination and print the results")
    p.add_argument("--limit", type=int, help="with --all: stop after N items")
    add_common(p, write=True)
    p.set_defaults(fn=cmd_raw)
    return ap


def main(argv=None):
    global SHOW_SECRETS
    args = build_parser().parse_args(argv)
    scrub_own_credential()
    SHOW_SECRETS = bool(getattr(args, "show_secrets", False))
    for attr, default in (("yes", False), ("dry_run", False), ("body", None), ("filter", None), ("limit", None),
                          ("table", False), ("id", None), ("verb", None), ("user", None), ("type", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    if args.verb in NEEDS_ID.get(args.group, ()) and not args.id:
        die("%s %s needs an id" % (args.group, args.verb), EXIT_USAGE)
    if args.group != "raw" and args.verb in NEEDS_BODY and not args.body:
        die("%s %s needs --body <json>" % (args.group, args.verb), EXIT_USAGE)
    try:
        args.fn(Api(args), args)
    except ApiError as e:
        if e.status == 404:
            sys.stderr.write("note: an object the token's user may not view also answers not found\n")
        api_error_exit(e, error_kind(e))
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()

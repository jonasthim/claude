#!/usr/bin/env python3
"""tn.py - small CLI around the TrueNAS SCALE 25.x JSON-RPC (websocket) API.

Environment:
  TRUENAS_HOST        hostname[:port] of the NAS, or a full ws:// / wss:// URI   (required)
  TRUENAS_API_KEY     API key created in the TrueNAS UI (user-linked key)       (required)
  TRUENAS_USER        username the key belongs to; optional, enables auth.login_ex
  TRUENAS_VERIFY_SSL  "0" to skip certificate verification (default "1")
  TRUENAS_SCHEME      "ws" or "wss" (default "wss") when TRUENAS_HOST has no scheme
  TRUENAS_VENV        virtualenv holding truenas_api_client (default ~/.cache/truenas-skill/venv)

Subcommands:
  info                      connectivity check: version, hostname, uptime, alert summary
  host                      print the bare hostname from TRUENAS_HOST (for building an SSH target)
  call METHOD [ARG ...]     call any API method; ARGs are JSON (fallback: plain string)
  methods [PREFIX]          list methods and their argument schema (core.get_methods)
  jobs [--running|--id N]   inspect background jobs (core.get_jobs)
  query NAMESPACE [...]     sugar over NAMESPACE.query with --filter / --select / --limit

Exit codes: 0 ok, 1 API or connection error, 2 blocked by the destructive-method gate,
3 setup problem (missing env vars or client library).
"""
from __future__ import annotations

import argparse
import fnmatch
import inspect
import json
import os
import re
import socket
import ssl
import sys
import urllib.request

# Methods that destroy data, take storage offline, or restart the system. Calling one of
# these through `call` requires --confirm, which Claude only adds after the user has agreed
# to that specific operation. Keep patterns broad: a false positive costs one extra prompt,
# a false negative can cost a dataset.
DESTRUCTIVE = [
    "*.delete",
    "*.rollback",
    "*.destroy",
    "*.wipe",
    "*.lock",
    "pool.export",
    "pool.detach",
    "pool.remove",
    "pool.replace",
    "pool.offline",
    "pool.create",
    "pool.update",
    "pool.upgrade",
    "pool.dataset.permission",
    "pool.dataset.set_quota",
    "pool.dataset.encryption*",
    "pool.dataset.change_key",
    "pool.dataset.inherit_parent_encryption_properties",
    "disk.update",
    "disk.format",
    "disk.sed_*",
    "system.reboot",
    "system.shutdown",
    "update.update",
    "update.manual",
    "update.download",
    "update.file",
    "app.stop",
    "app.convert_to_custom",
    "replication.run",
    "replication.run_onetime",
    "cloudsync.sync",
    "rsynctask.run",
    "filesystem.setacl",
    "filesystem.setperm",
    "filesystem.chown",
    "boot.*",
    "bootenv.*",
    "config.*",
    "failover.*",
    "truecommand.*",
    "kmip.*",
    "vm.*",
    "virt.*",
    "service.stop",
    "user.*",
    "group.*",
    "auth.*",
    "api_key.*",
    "certificate*",
    "network.*",
    "interface.*",
    "staticroute.*",
    "ipmi.*",
    "smb.update",
    "nfs.update",
    "iscsi.global.update",
    "system.general.update",
    "system.advanced.update",
    "docker.update",
    "docker.*unset*",
]

# Read-only shapes that are never gated, even inside a namespace listed above.
SAFE = [
    "*.query", "*.get_instance", "*.config", "*.choices", "*_choices", "*.get_*", "*.list*",
    "*.status", "*.summary", "*.check*", "*.has_*", "*.is_*", "*.validate*", "*.details",
    "*.info", "*.version*", "*.available*", "*.presets", "*.temperature*", "*.stats",
    "*.sessions", "auth.me", "*.rollback_versions", "*.upgrade_summary", "*.count*",
    "*.unmatched*", "*.mountpoint", "*.processes", "*.attachments", "*.recommended*",
    "core.ping", "core.get_methods", "core.get_jobs", "core.get_services",
]

DEFAULT_VENV = os.path.expanduser("~/.cache/truenas-skill/venv")
EXIT_OK, EXIT_ERROR, EXIT_GATED, EXIT_SETUP = 0, 1, 2, 3


# --------------------------------------------------------------------------- helpers


def is_destructive(method: str) -> bool:
    """True when METHOD matches any pattern in DESTRUCTIVE (case-insensitive glob)."""
    m = method.lower()
    if any(fnmatch.fnmatchcase(m, pat.lower()) for pat in SAFE):
        return False
    return any(fnmatch.fnmatchcase(m, pat.lower()) for pat in DESTRUCTIVE)


def parse_arg(raw: str):
    """Parse one CLI argument: JSON when it is valid JSON, otherwise the literal string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def parse_filter(expr: str) -> list:
    """'key=value' -> ['key', '=', value]. Operators: =, !=, ~ (regex), >, >=, <, <=, in, nin."""
    m = re.match(r"^([A-Za-z0-9_.]+)\s*(!=|>=|<=|=|~|>|<| in | nin )\s*(.*)$", expr)
    if not m:
        raise argparse.ArgumentTypeError(
            f"bad filter {expr!r}; expected key=value, key!=value, key~regex, key>n, key<n"
        )
    key, op, value = m.group(1), m.group(2).strip(), parse_arg(m.group(3))
    return [key, op, value]


def build_uri(host: str, scheme: str) -> str:
    if host.startswith(("ws://", "wss://")):
        uri = host.rstrip("/")
    else:
        uri = f"{scheme}://{host.rstrip('/')}"
    if not uri.endswith("/api/current"):
        uri += "/api/current"
    return uri


def http_base(uri: str) -> str:
    return uri.replace("wss://", "https://").replace("ws://", "http://").removesuffix("/api/current")


def bare_host(host: str) -> str:
    """Strip scheme, path and port from TRUENAS_HOST: 'wss://nas:8443/api/current' -> 'nas'."""
    h = host.strip()
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0]
    if h.startswith("["):  # bracketed IPv6 literal
        return h[1 : h.index("]")] if "]" in h else h[1:]
    if h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    return h


def classify_connection_error(exc: BaseException) -> tuple[str, str]:
    """Map a connection failure to (cause, hint) so the user hears a diagnosis, not a stack."""
    text = str(exc).lower()
    if isinstance(exc, ssl.SSLError) or "certificate" in text or "ssl" in text or "tls" in text:
        return ("certificate", "the NAS presented a certificate this machine does not trust (self-signed, "
                "or the name you connect to is not on the cert). Only the user may decide to set "
                "TRUENAS_VERIFY_SSL=0 or to trust the NAS CA; report this and ask")
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in text or "timeout" in text:
        return ("timeout", "nothing answered on that address: the NAS is probably on a network this machine "
                "cannot reach (another VLAN, firewalled web ports). See 'Reaching a NAS on another VLAN' "
                "in references/api-basics.md for the SSH port-forward recipe")
    if isinstance(exc, ConnectionRefusedError) or "refused" in text or "errno 111" in text:
        return ("refused", "the host answered but nothing listens on that port: check the port in "
                "TRUENAS_HOST (web UI port, default 443) or whether the middleware is down "
                "(ssh in and run: midclt call system.info)")
    if isinstance(exc, socket.gaierror) or "name or service not known" in text or "nodename" in text \
            or "getaddrinfo" in text:
        return ("dns", "the hostname in TRUENAS_HOST does not resolve from this machine")
    return ("unknown", "check TRUENAS_HOST, that the web UI is reachable on that address from this machine, "
            "and the error text above")


def emit(obj, compact: bool = False):
    if compact:
        print(json.dumps(obj, default=str))
    else:
        print(json.dumps(obj, indent=2, default=str))


def fail(message: str, code: int = EXIT_ERROR, **extra):
    payload = {"error": message, **extra}
    print(json.dumps(payload, indent=2, default=str), file=sys.stderr)
    sys.exit(code)


def reexec_in_venv_if_needed():
    """If truenas_api_client is missing but a skill venv exists, restart under that venv."""
    try:
        import truenas_api_client  # noqa: F401
        return
    except ImportError:
        pass
    venv = os.environ.get("TRUENAS_VENV", DEFAULT_VENV)
    py = os.path.join(venv, "bin", "python")
    if os.path.exists(py) and not os.environ.get("TRUENAS_VENV_REEXEC"):
        # The venv's python is usually a symlink to the system interpreter, so compare by
        # intent (an env guard) rather than by resolved path to avoid an exec loop.
        os.environ["TRUENAS_VENV_REEXEC"] = "1"
        os.execv(py, [py, os.path.abspath(__file__), *sys.argv[1:]])
    here = os.path.dirname(os.path.abspath(__file__))
    fail(
        "truenas_api_client is not installed. Run the setup script once:\n"
        f"  bash {os.path.join(here, 'setup.sh')}\n"
        "It creates a virtualenv (default ~/.cache/truenas-skill/venv) and installs the client.",
        EXIT_SETUP,
    )


# --------------------------------------------------------------------------- connection


class Session:
    def __init__(self, insecure: bool = False, timeout: float | None = None):
        host = os.environ.get("TRUENAS_HOST")
        key = os.environ.get("TRUENAS_API_KEY")
        if not host or not key:
            fail(
                "TRUENAS_HOST and TRUENAS_API_KEY must be set (see README: creating an API key).",
                EXIT_SETUP,
            )
        self.user = os.environ.get("TRUENAS_USER") or None
        self.verify_ssl = not insecure and os.environ.get("TRUENAS_VERIFY_SSL", "1") not in ("0", "false", "no")
        self.uri = build_uri(host, os.environ.get("TRUENAS_SCHEME", "wss"))
        self.key = key
        self.timeout = timeout
        self.client = None

    def __enter__(self):
        reexec_in_venv_if_needed()
        from truenas_api_client import Client

        kwargs = {"uri": self.uri}
        sig = inspect.signature(Client.__init__).parameters
        if "verify_ssl" in sig:
            kwargs["verify_ssl"] = self.verify_ssl
        elif not self.verify_ssl:
            print("warning: this client version cannot disable SSL verification", file=sys.stderr)
        if self.timeout and "call_timeout" in sig:
            kwargs["call_timeout"] = self.timeout
        try:
            self.client = Client(**kwargs)
            self.client.__enter__()
        except Exception as exc:  # connection refused, TLS failure, DNS, timeout
            cause, hint = classify_connection_error(exc)
            fail(f"could not connect to {self.uri}: {exc}", EXIT_ERROR, cause=cause, hint=hint)
        self._login()
        return self.client

    def __exit__(self, *exc):
        if self.client is not None:
            try:
                self.client.__exit__(*exc)
            except Exception:
                pass

    def _login(self):
        # 25.x servers accept the raw user-linked key via auth.login_with_api_key. When the
        # username is known we use auth.login_ex with API_KEY_PLAIN, which is what newer
        # clients do and what 26.x expects for legacy keys.
        c = self.client
        try:
            if self.user:
                resp = c.call("auth.login_ex", {
                    "mechanism": "API_KEY_PLAIN", "username": self.user, "api_key": self.key,
                })
                ok = isinstance(resp, dict) and resp.get("response_type") == "SUCCESS"
                detail = resp if not ok else None
            else:
                ok = bool(c.call("auth.login_with_api_key", self.key))
                detail = None
        except Exception as exc:
            fail(f"authentication call failed: {exc}", EXIT_ERROR)
        if not ok:
            fail("authentication failed: API key rejected", EXIT_ERROR, response=detail, hint=(
                "regenerate the key in the TrueNAS UI (user menu > API Keys) and make sure the "
                "user it belongs to is allowed API access"))


def describe_exception(exc) -> dict:
    """Turn client exceptions into a JSON-friendly dict with the useful bits only."""
    info = {"error_type": type(exc).__name__, "detail": str(getattr(exc, "error", None) or exc)}
    errno = getattr(exc, "errno", None)
    if errno is not None:
        info["errno"] = errno
    errors = getattr(exc, "errors", None)
    if errors:
        info["validation_errors"] = [
            {"attribute": getattr(e, "attribute", None), "message": getattr(e, "errmsg", str(e))}
            for e in errors
        ]
    trace = getattr(exc, "trace", None)
    if isinstance(trace, dict):
        if trace.get("repr"):
            info["server_exception"] = trace["repr"]
        if os.environ.get("TRUENAS_DEBUG") and trace.get("formatted"):
            info["traceback"] = trace["formatted"]
    return info


def run_call(client, method: str, params: list, job: bool, timeout: float | None):
    kwargs = {}
    if timeout:
        kwargs["timeout"] = timeout
    if job:
        last = {"percent": None}

        def progress(j):
            p = (j or {}).get("progress") or {}
            pct, desc = p.get("percent"), p.get("description")
            if pct != last["percent"]:
                last["percent"] = pct
                print(f"job {j.get('id')}: {pct}% {desc or ''}".rstrip(), file=sys.stderr)

        kwargs["job"] = True
        kwargs["callback"] = progress
    return client.call(method, *params, **kwargs)


# --------------------------------------------------------------------------- subcommands


def cmd_info(args):
    sess = Session(args.insecure, args.timeout)
    with sess as c:
        info = c.call("system.info")
        out = {
            "uri": sess.uri,
            "hostname": info.get("hostname"),
            "version": info.get("version"),
            "product": info.get("system_product"),
            "uptime": info.get("uptime"),
            "cores": info.get("cores"),
            "physmem_gib": round((info.get("physmem") or 0) / 2**30, 1),
            "loadavg": info.get("loadavg"),
        }
        try:
            alerts = c.call("alert.list")
            active = [a for a in alerts if not a.get("dismissed")]
            levels = {}
            for a in active:
                levels[a.get("level")] = levels.get(a.get("level"), 0) + 1
            out["alerts"] = {"active": len(active), "by_level": levels,
                             "top": [a.get("formatted") for a in active[:5]]}
        except Exception as exc:
            out["alerts"] = {"error": str(exc)}
        try:
            running = c.call("core.get_jobs", [["state", "=", "RUNNING"]])
            out["running_jobs"] = [{"id": j["id"], "method": j["method"]} for j in running]
        except Exception as exc:
            out["running_jobs"] = {"error": str(exc)}
    try:
        ctx = ssl.create_default_context()
        if not sess.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(http_base(out["uri"]) + "/api/versions", context=ctx, timeout=10) as r:
            out["api_versions"] = json.loads(r.read().decode())
    except Exception as exc:
        out["api_versions"] = {"error": str(exc)}
    emit(out, args.compact)


def cmd_host(args):
    host = os.environ.get("TRUENAS_HOST")
    if not host:
        fail("TRUENAS_HOST is not set", EXIT_SETUP)
    print(bare_host(host))


def cmd_call(args):
    if args.args == ["-"]:
        params = json.load(sys.stdin)
        if not isinstance(params, list):
            fail("stdin must contain a JSON array of positional arguments", EXIT_ERROR)
    else:
        params = [parse_arg(a) for a in args.args]
    if is_destructive(args.method) and not args.confirm:
        fail(
            f"{args.method} is classed as destructive or system-changing. Ask the user to confirm "
            "this exact operation (method and arguments), then re-run with --confirm.",
            EXIT_GATED, method=args.method, params=params,
        )
    with Session(args.insecure, args.timeout) as c:
        try:
            result = run_call(c, args.method, params, args.job, args.timeout)
        except Exception as exc:
            fail(f"{args.method} failed", EXIT_ERROR, **describe_exception(exc))
    emit(result, args.compact)


def cmd_methods(args):
    with Session(args.insecure, args.timeout) as c:
        try:
            methods = c.call("core.get_methods")
        except Exception as exc:
            fail("core.get_methods failed", EXIT_ERROR, **describe_exception(exc))
    needle = (args.prefix or "").lower()
    selected = {k: v for k, v in methods.items() if needle in k.lower()}
    if not selected:
        fail(f"no methods match {args.prefix!r}", EXIT_ERROR)
    if args.full:
        emit(selected, args.compact)
        return
    out = {}
    for name, meta in sorted(selected.items()):
        meta = meta or {}
        entry = {}
        desc = (meta.get("description") or "").strip()
        if desc:
            entry["description"] = desc if args.schema else desc.splitlines()[0][:160]
        if meta.get("job"):
            entry["job"] = True
        if is_destructive(name):
            entry["requires_confirm"] = True
        if args.schema:
            for key in ("accepts", "returns", "filterable", "item_method", "examples"):
                if key in meta and meta[key] not in (None, [], False, ""):
                    entry[key] = meta[key]
        out[name] = entry
    emit(out, args.compact)


def cmd_jobs(args):
    with Session(args.insecure, args.timeout) as c:
        try:
            if args.id is not None:
                jobs = c.call("core.get_jobs", [["id", "=", args.id]])
                if not jobs:
                    fail(f"no job with id {args.id}", EXIT_ERROR)
                emit(jobs[0], args.compact)
                return
            filters = [["state", "=", "RUNNING"]] if args.running else []
            jobs = c.call("core.get_jobs", filters, {"order_by": ["-id"], "limit": args.limit})
        except Exception as exc:
            fail("core.get_jobs failed", EXIT_ERROR, **describe_exception(exc))
    summary = []
    for j in jobs:
        prog = j.get("progress") or {}
        summary.append({
            "id": j.get("id"), "method": j.get("method"), "state": j.get("state"),
            "percent": prog.get("percent"), "description": prog.get("description"),
            "started": j.get("time_started"), "finished": j.get("time_finished"),
            "error": j.get("error"),
        })
    emit(summary, args.compact)


def cmd_query(args):
    method = args.namespace if args.namespace.endswith(".query") else args.namespace + ".query"
    filters = [parse_filter(f) for f in args.filter]
    options = {}
    if args.select:
        options["select"] = [s.strip() for s in args.select.split(",") if s.strip()]
    if args.limit:
        options["limit"] = args.limit
    if args.order_by:
        options["order_by"] = [args.order_by]
    if args.extra:
        options["extra"] = parse_arg(args.extra)
    with Session(args.insecure, args.timeout) as c:
        try:
            result = c.call(method, filters, options)
        except Exception as exc:
            fail(f"{method} failed", EXIT_ERROR, **describe_exception(exc))
    emit(result, args.compact)


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tn.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--insecure", action="store_true", help="skip TLS certificate verification")
    p.add_argument("--timeout", type=float, default=None, help="seconds to wait for a call")
    p.add_argument("--compact", action="store_true", help="single-line JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("info", help="connectivity and health summary")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("host", help="print the bare hostname from TRUENAS_HOST")
    s.set_defaults(func=cmd_host)

    s = sub.add_parser("call", help="call an API method")
    s.add_argument("method")
    s.add_argument("args", nargs="*", help="positional arguments as JSON; '-' reads a JSON array from stdin")
    s.add_argument("--job", action="store_true", help="wait for the returned job and print its result")
    s.add_argument("--confirm", action="store_true", help="allow a destructive method (after the user agreed)")
    s.set_defaults(func=cmd_call)

    s = sub.add_parser("methods", help="discover methods and their schemas")
    s.add_argument("prefix", nargs="?", help="substring to match, e.g. pool.dataset")
    s.add_argument("--schema", action="store_true", help="include accepts/returns schema")
    s.add_argument("--full", action="store_true", help="dump the raw core.get_methods entries")
    s.set_defaults(func=cmd_methods)

    s = sub.add_parser("jobs", help="list or inspect background jobs")
    s.add_argument("--running", action="store_true")
    s.add_argument("--id", type=int, default=None, help="dump one job in full")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_jobs)

    s = sub.add_parser("query", help="run NAMESPACE.query with simple filters")
    s.add_argument("namespace", help="e.g. pool.dataset, sharing.smb, app, alert")
    s.add_argument("--filter", action="append", default=[], help="key=value (repeatable); ops: = != ~ > >= < <=")
    s.add_argument("--select", help="comma-separated fields to return")
    s.add_argument("--limit", type=int)
    s.add_argument("--order-by", dest="order_by", help="field name; for descending write --order-by=-name")
    s.add_argument("--extra", help='JSON for query-options.extra, e.g. \'{"flat": false}\'')
    s.set_defaults(func=cmd_query)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        fail("interrupted", EXIT_ERROR)
    except Exception as exc:  # anything the subcommands did not translate themselves
        fail("unexpected error", EXIT_ERROR, **describe_exception(exc))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

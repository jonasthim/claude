#!/usr/bin/env python3
"""tn.py - standard-library-only CLI for the TrueNAS SCALE 25.x middleware API.

Two transports, same commands:
  ws   JSON-RPC 2.0 over a websocket to wss://<host>/api/current, authenticated with an API key
       (a minimal RFC 6455 client is built in; nothing to install)
  ssh  `ssh <target> midclt call ...`, i.e. the middleware's own CLI on the NAS. No API key, no
       TLS, no open web port needed; only SSH access. Good when the NAS sits behind a firewall.

Environment:
  TRUENAS_TRANSPORT   "ws" or "ssh". Default: ws when TRUENAS_API_KEY is set, else ssh.
  TRUENAS_HOST        hostname[:port] of the NAS, or a full ws:// / wss:// URI
  TRUENAS_API_KEY     API key created in the TrueNAS UI (user-linked key)       [ws]
  TRUENAS_USER        username the key belongs to; optional, uses auth.login_ex [ws]
  TRUENAS_VERIFY_SSL  "0" to skip certificate verification (default "1")        [ws]
  TRUENAS_SCHEME      "ws" or "wss" (default "wss") when TRUENAS_HOST has no scheme
  TRUENAS_SSH_HOST    [user@]host for ssh (default root@<bare TRUENAS_HOST>)    [ssh]
  TRUENAS_SSH_SUDO    "1" to prefix midclt with sudo -n (default: only when user is not root)
  TRUENAS_SSH_OPTS    extra ssh options, e.g. "-J jump.example.lan"              [ssh]

Subcommands:
  info                      connectivity check: version, hostname, uptime, alert summary
  host                      print the bare hostname from TRUENAS_HOST (for building an SSH target)
  call METHOD [ARG ...]     call any API method; ARGs are JSON (fallback: plain string)
  methods [PREFIX]          list methods and their argument schema (core.get_methods); --exact to
                            prove a single name exists before calling it
  jobs [--running|--id N]   inspect background jobs (core.get_jobs)
  query NAMESPACE [...]     sugar over NAMESPACE.query with --filter / --select / --limit

Results are redacted by default: values under credential-looking keys (password, secret,
token, private key, API key, passphrase, hashes) and whole results of key-export methods are
replaced with "[REDACTED]". Pass --show-secrets (or TRUENAS_SHOW_SECRETS=1) when the user has
explicitly asked for a credential.

Exit codes: 0 ok, 1 API or connection error, 2 blocked by the destructive-method gate,
3 setup problem (missing env vars).
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import re
import shlex
import socket
import ssl
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid

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
    "update.run",
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

EXIT_OK, EXIT_ERROR, EXIT_GATED, EXIT_SETUP = 0, 1, 2, 3
DEFAULT_CALL_TIMEOUT = 120.0
DEFAULT_JOB_TIMEOUT = 3600.0
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


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
                "TRUENAS_VERIFY_SSL=0 or to trust the NAS CA; report this and ask. The ssh transport "
                "(TRUENAS_TRANSPORT=ssh) avoids TLS entirely")
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in text or "timeout" in text:
        return ("timeout", "nothing answered on that address: the NAS is probably on a network this machine "
                "cannot reach (another VLAN, firewalled web ports). If SSH to the NAS works, use "
                "TRUENAS_TRANSPORT=ssh; see 'Transports' in references/api-basics.md")
    if isinstance(exc, ConnectionRefusedError) or "refused" in text or "errno 111" in text:
        return ("refused", "the host answered but nothing listens on that port: check the port in "
                "TRUENAS_HOST (web UI port, default 443) or whether the middleware is down "
                "(ssh in and run: midclt call system.info)")
    if isinstance(exc, socket.gaierror) or "name or service not known" in text or "nodename" in text \
            or "getaddrinfo" in text:
        return ("dns", "the hostname in TRUENAS_HOST does not resolve from this machine")
    if "permission denied" in text and ("publickey" in text or "ssh" in text):
        return ("ssh-auth", "ssh reached the NAS but no key is authorized there for this user. The API "
                "transport (TRUENAS_HOST + TRUENAS_API_KEY) is the primary path and may be the only one on "
                "this system; the ssh transport only works once a public key is added to the NAS user")
    if "handshake" in text or "http " in text:
        return ("handshake", "the server answered but did not upgrade to a websocket: TRUENAS_HOST probably "
                "points at the wrong port or a non-TrueNAS web server")
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


class ApiError(Exception):
    """A failed call, in the shape both transports produce."""

    def __init__(self, detail: str, errno: int | None = None, validation_errors: list | None = None,
                 server_exception: str | None = None, traceback: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.errno = errno
        self.validation_errors = validation_errors or []
        self.server_exception = server_exception
        self.traceback = traceback

    def as_dict(self) -> dict:
        info = {"error_type": "ValidationErrors" if self.validation_errors else "CallError", "detail": self.detail}
        if self.errno is not None:
            info["errno"] = self.errno
        if self.validation_errors:
            info["validation_errors"] = self.validation_errors
        if self.server_exception:
            info["server_exception"] = self.server_exception
        if self.traceback and os.environ.get("TRUENAS_DEBUG"):
            info["traceback"] = self.traceback
        return info


def describe_exception(exc) -> dict:
    if isinstance(exc, ApiError):
        return exc.as_dict()
    return {"error_type": type(exc).__name__, "detail": str(exc)}


# --------------------------------------------------------------------------- redaction

REDACTED = "[REDACTED]"
# Key names whose values are credentials wherever they appear (compared lower-case, exact).
SECRET_KEYS = {
    "password", "passwd", "pass", "passphrase", "secret", "peersecret", "token", "access_token",
    "refresh_token", "id_token", "api_key", "apikey", "bindpw", "monpwd", "unixhash", "smbhash",
    "privatekey", "private_key", "client_secret", "secret_key", "secret_access_key",
    "v3_password", "v3_privpassphrase", "encryption_key", "key_data", "webhook_url",
    "service_account_credentials", "credentials_json", "oauth_client_secret", "cert_key",
}
# Substrings that mark a key as secret-bearing (e.g. db_password, admin_passphrase, api_token).
SECRET_KEY_PARTS = ("password", "passphrase", "secret", "private_key", "privkey", "_token", "api_key", "apikey")
# Methods whose entire result is key material.
SECRET_RESULT_METHODS = ("pool.dataset.export_key", "pool.dataset.export_keys", "api_key.create", "kmip.*key*")
# In credential objects everything is secret except a few descriptive fields.
CREDENTIAL_ATTRIBUTE_PARENTS = {"attributes"}
CREDENTIAL_METHOD_PREFIXES = ("cloudsync", "cloud_backup", "keychaincredential", "alertservice", "acme.dns",
                              "truecommand", "mail", "vmware", "activedirectory", "ldap", "ipa", "idmap")
CREDENTIAL_ATTRIBUTE_ALLOW = {"type", "provider", "username", "user", "endpoint", "region", "url", "host",
                             "hostname", "port", "remote_host_key", "bucket", "account", "level", "cert",
                             "certificate", "email", "from", "fromemail", "fromname", "security", "smtp",
                             "outgoingserver", "oauth"}


def is_secret_key(key: str) -> bool:
    k = str(key).lower()
    return k in SECRET_KEYS or any(part in k for part in SECRET_KEY_PARTS)


def redact(obj, method: str | None = None, counter: list | None = None):
    """Return a copy of OBJ with credential-looking values replaced. counter[0] counts hits."""
    counter = counter if counter is not None else [0]
    if method and any(fnmatch.fnmatchcase(method, pat) for pat in SECRET_RESULT_METHODS):
        counter[0] += 1
        return REDACTED
    in_credentials = bool(method) and method.startswith(CREDENTIAL_METHOD_PREFIXES)

    def walk(node, parent_key=None):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if is_secret_key(k) and v not in (None, "", [], {}):
                    counter[0] += 1
                    out[k] = REDACTED
                elif in_credentials and parent_key in CREDENTIAL_ATTRIBUTE_PARENTS \
                        and str(k).lower() not in CREDENTIAL_ATTRIBUTE_ALLOW and isinstance(v, (str, int)) \
                        and not isinstance(v, bool) and v != "":
                    counter[0] += 1
                    out[k] = REDACTED
                else:
                    out[k] = walk(v, k)
            return out
        if isinstance(node, list):
            return [walk(v, parent_key) for v in node]
        return node

    return walk(obj)


def emit_result(obj, args, method: str | None = None):
    """Print a call result, redacting unless the user asked to see secrets."""
    show = args.show_secrets or os.environ.get("TRUENAS_SHOW_SECRETS", "") not in ("", "0", "false", "no")
    if not show:
        counter = [0]
        obj = redact(obj, method, counter)
        if counter[0]:
            print(f"note: {counter[0]} credential-looking value(s) redacted; pass --show-secrets only if the "
                  "user explicitly asked for them", file=sys.stderr)
    emit(obj, args.compact)


# --------------------------------------------------------------------------- websocket transport


def ws_accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_encode_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """Build one masked client frame (clients must mask, RFC 6455 §5.3)."""
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack("!Q", n)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return header + mask + masked


def ws_decode_frame(read_exact) -> tuple[bool, int, bytes]:
    """Read one frame via read_exact(n) -> bytes. Returns (fin, opcode, payload)."""
    b0, b1 = read_exact(2)
    fin, opcode = bool(b0 & 0x80), b0 & 0x0F
    masked, n = bool(b1 & 0x80), b1 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", read_exact(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", read_exact(8))
    mask = read_exact(4) if masked else None
    payload = read_exact(n) if n else b""
    if mask:
        payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return fin, opcode, payload


class WSClient:
    """Just enough RFC 6455 + JSON-RPC 2.0 to drive the TrueNAS middleware."""

    def __init__(self, uri: str, verify_ssl: bool = True, connect_timeout: float = 15.0):
        self.uri = uri
        self.verify_ssl = verify_ssl
        self.connect_timeout = connect_timeout
        self.sock = None
        self._buf = b""
        self._fragments: list[bytes] = []
        self._pending_events: list[dict] = []

    # -- connection
    def connect(self):
        u = urllib.parse.urlsplit(self.uri)
        secure = u.scheme == "wss"
        host, port = u.hostname, u.port or (443 if secure else 80)
        path = u.path or "/"
        sock = socket.create_connection((host, port), timeout=self.connect_timeout)
        if secure:
            ctx = ssl.create_default_context()
            if not self.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        hostport = host if port in (80, 443) else f"{host}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {http_base(self.uri)}\r\n\r\n"
        )
        sock.sendall(request.encode())
        self.sock = sock
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed during websocket handshake")
            head += chunk
        head, self._buf = head.split(b"\r\n\r\n", 1)
        status_line, *header_lines = head.decode(errors="replace").split("\r\n")
        if " 101 " not in status_line:
            raise ConnectionError(f"websocket handshake failed: {status_line}")
        headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines)}
        if headers.get("sec-websocket-accept") != ws_accept_key(key):
            raise ConnectionError("websocket handshake failed: bad Sec-WebSocket-Accept")

    def close(self):
        if self.sock is not None:
            try:
                self.sock.settimeout(2)
                self.sock.sendall(ws_encode_frame(struct.pack("!H", 1000), opcode=0x8))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # -- framing
    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("connection closed by server")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv_text(self) -> str:
        """Return the next complete text message, answering pings and handling fragments."""
        while True:
            fin, opcode, payload = ws_decode_frame(self._read_exact)
            if opcode in (0x1, 0x2, 0x0):
                self._fragments.append(payload)
                if fin:
                    data, self._fragments = b"".join(self._fragments), []
                    return data.decode()
            elif opcode == 0x9:  # ping
                self.sock.sendall(ws_encode_frame(payload, opcode=0xA))
            elif opcode == 0x8:  # close
                reason = payload[2:].decode(errors="replace") if len(payload) > 2 else ""
                raise ConnectionError(f"server closed the connection {reason}".strip())
            # 0xA pong: ignore

    def _send_json(self, obj: dict):
        self.sock.sendall(ws_encode_frame(json.dumps(obj).encode()))

    # -- JSON-RPC
    def _recv_response(self, call_id: str, timeout: float | None) -> dict:
        """Wait for the response to call_id, stashing notifications that arrive meanwhile."""
        self.sock.settimeout(timeout)
        while True:
            msg = json.loads(self._recv_text())
            if msg.get("id") == call_id:
                return msg
            if "method" in msg:  # notification (collection_update etc.)
                self._pending_events.append(msg)

    def _next_event(self, timeout: float | None) -> dict:
        if self._pending_events:
            return self._pending_events.pop(0)
        self.sock.settimeout(timeout)
        while True:
            msg = json.loads(self._recv_text())
            if "method" in msg:
                return msg

    @staticmethod
    def _raise_for_error(err: dict):
        data = err.get("data") or {}
        if err.get("code") == -32602 and isinstance(data, dict) and data.get("extra"):
            raise ApiError(
                "validation failed: " + "; ".join(f"{e[0]}: {e[1]}" for e in data["extra"]),
                errno=data.get("error"),
                validation_errors=[{"attribute": e[0], "message": e[1], "errno": e[2] if len(e) > 2 else None}
                                   for e in data["extra"]],
            )
        if isinstance(data, dict) and data:
            trace = data.get("trace") or {}
            raise ApiError(
                data.get("reason") or err.get("message") or "call failed",
                errno=data.get("error"),
                server_exception=trace.get("repr") if isinstance(trace, dict) else None,
                traceback=trace.get("formatted") if isinstance(trace, dict) else None,
            )
        raise ApiError(err.get("message") or "call failed", errno=err.get("code"))

    def call(self, method: str, *params, job: bool = False, progress=None, timeout: float | None = None):
        if job:
            # Legacy-style jobs (the server default): the call returns a job id at once and
            # progress arrives as core.get_jobs collection_update events we subscribed to.
            self._rpc("core.subscribe", "core.get_jobs", timeout=timeout or DEFAULT_CALL_TIMEOUT)
        result = self._rpc(method, *params, timeout=timeout or DEFAULT_CALL_TIMEOUT)
        if not job:
            return result
        if not isinstance(result, int):
            return result  # the server answered directly; nothing to wait for
        return self._wait_job(result, progress, timeout or DEFAULT_JOB_TIMEOUT)

    def _rpc(self, method: str, *params, timeout: float | None):
        call_id = str(uuid.uuid4())
        self._send_json({"jsonrpc": "2.0", "id": call_id, "method": method, "params": list(params)})
        try:
            msg = self._recv_response(call_id, timeout)
        except socket.timeout:
            raise ApiError(f"{method} did not answer within {timeout}s")
        if "error" in msg:
            self._raise_for_error(msg["error"])
        return msg.get("result")

    def _wait_job(self, job_id: int, progress, timeout: float | None):
        last = {}
        try:
            while True:
                ev = self._next_event(timeout)
                p = ev.get("params") or {}
                if p.get("collection") != "core.get_jobs":
                    continue
                fields = p.get("fields") or {}
                if fields.get("id") != job_id:
                    continue
                last.update(fields)
                if progress:
                    progress(last)
                state = last.get("state")
                if state == "SUCCESS":
                    return last.get("result")
                if state in ("FAILED", "ABORTED"):
                    exc_info = last.get("exc_info") or {}
                    if exc_info.get("type") == "VALIDATION":
                        raise ApiError(last.get("error") or "job failed validation",
                                       validation_errors=[{"attribute": e[0], "message": e[1]} for e in exc_info.get("extra") or []])
                    tb_lines = (last.get("exception") or "").strip().splitlines()
                    raise ApiError(last.get("error") or f"job {job_id} {state.lower()}",
                                   errno=exc_info.get("errno"),
                                   server_exception=tb_lines[-1] if tb_lines else None,
                                   traceback=last.get("exception"))
        except socket.timeout:
            raise ApiError(f"job {job_id} still running after {timeout}s; check later with: tn.py jobs --id {job_id}")


# --------------------------------------------------------------------------- ssh transport


def ssh_target() -> str | None:
    t = os.environ.get("TRUENAS_SSH_HOST")
    if t:
        return t
    host = os.environ.get("TRUENAS_HOST")
    return f"root@{bare_host(host)}" if host else None


def ssh_command(target: str, method: str, params: list, job: bool, extra_opts: str = "",
                sudo: bool | None = None) -> list[str]:
    """Build the local ssh argv that runs midclt on the NAS."""
    user = target.split("@", 1)[0] if "@" in target else None
    if sudo is None:
        env_sudo = os.environ.get("TRUENAS_SSH_SUDO")
        sudo = env_sudo not in (None, "", "0", "false", "no") if env_sudo is not None else (user not in (None, "root"))
    remote = ["midclt", "call"]
    if job:
        remote.append("-j")
    remote.append(method)
    remote += [json.dumps(p) for p in params]
    if sudo:
        remote = ["sudo", "-n"] + remote
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if extra_opts:
        argv += shlex.split(extra_opts)
    argv += [target, " ".join(shlex.quote(a) for a in remote)]
    return argv


def parse_midclt_stderr(text: str) -> ApiError:
    """midclt prints the error, then the traceback, then the validation list (pprint)."""
    text = text.strip()
    lines = text.splitlines()
    validation = []
    for line in lines:
        m = re.match(r"^\[(E[A-Z0-9_]+)\] (\S+): (.*)$", line.strip())
        if m:
            validation.append({"attribute": m.group(2), "message": m.group(3), "errname": m.group(1)})
    detail = lines[0] if lines else "midclt failed"
    server_exception = next((l.strip() for l in reversed(lines) if re.match(r"^\w*(Error|Exception)\b", l.strip())), None)
    if re.search(r"Daemon not running|Connection refused", text):
        detail = "middleware not reachable on the NAS: " + detail
    return ApiError(detail, validation_errors=validation, server_exception=server_exception,
                    traceback=text if "Traceback" in text else None)


class SSHClient:
    def __init__(self, target: str, extra_opts: str = ""):
        self.target = target
        self.extra_opts = extra_opts

    def connect(self):
        pass

    def close(self):
        pass

    def call(self, method: str, *params, job: bool = False, progress=None, timeout: float | None = None):
        argv = ssh_command(self.target, method, list(params), job, self.extra_opts)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout or (DEFAULT_JOB_TIMEOUT if job else DEFAULT_CALL_TIMEOUT))
        except FileNotFoundError:
            raise ApiError("ssh is not installed on this machine")
        except subprocess.TimeoutExpired:
            raise ApiError(f"{method} over ssh did not finish within the timeout")
        if proc.returncode == 255:
            raise ConnectionError(f"ssh to {self.target} failed: {proc.stderr.strip()}")
        if proc.returncode != 0:
            if "sudo" in proc.stderr and ("password" in proc.stderr or "a terminal is required" in proc.stderr):
                raise ApiError(f"sudo on the NAS needs a password for {self.target}; use root or passwordless sudo")
            if "command not found" in proc.stderr:
                raise ApiError("midclt not found on the remote host; is TRUENAS_SSH_HOST really the NAS?")
            raise parse_midclt_stderr(proc.stderr or proc.stdout)
        if job and progress:
            for line in proc.stderr.splitlines():
                if line.strip():
                    progress({"progress": {"description": line.strip(), "percent": None}})
        out = proc.stdout.strip()
        if out == "":
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out  # midclt prints bare strings unquoted


# --------------------------------------------------------------------------- session


def choose_transport(override: str | None = None) -> str:
    t = override or os.environ.get("TRUENAS_TRANSPORT")
    if t:
        if t not in ("ws", "ssh"):
            fail(f"TRUENAS_TRANSPORT must be 'ws' or 'ssh', not {t!r}", EXIT_SETUP)
        return t
    if os.environ.get("TRUENAS_API_KEY") and os.environ.get("TRUENAS_HOST"):
        return "ws"
    if os.environ.get("TRUENAS_SSH_HOST") or os.environ.get("TRUENAS_HOST"):
        return "ssh"
    fail("no connection configured: set TRUENAS_HOST plus TRUENAS_API_KEY for the API, or "
         "TRUENAS_SSH_HOST (or TRUENAS_HOST) for ssh+midclt. See the README.", EXIT_SETUP)


class Session:
    def __init__(self, insecure: bool = False, timeout: float | None = None, transport: str | None = None):
        self.transport = choose_transport(transport)
        self.timeout = timeout
        self.client = None
        self.verify_ssl = not insecure and os.environ.get("TRUENAS_VERIFY_SSL", "1") not in ("0", "false", "no")
        if self.transport == "ws":
            host, key = os.environ.get("TRUENAS_HOST"), os.environ.get("TRUENAS_API_KEY")
            if not host or not key:
                fail("the ws transport needs TRUENAS_HOST and TRUENAS_API_KEY (see README: creating an API key).",
                     EXIT_SETUP)
            self.uri = build_uri(host, os.environ.get("TRUENAS_SCHEME", "wss"))
            self.key = key
            self.user = os.environ.get("TRUENAS_USER") or None
            self.where = self.uri
        else:
            self.target = ssh_target()
            if not self.target:
                fail("the ssh transport needs TRUENAS_SSH_HOST (or TRUENAS_HOST).", EXIT_SETUP)
            self.where = f"ssh://{self.target}"

    def __enter__(self):
        try:
            if self.transport == "ws":
                self.client = WSClient(self.uri, verify_ssl=self.verify_ssl,
                                       connect_timeout=min(self.timeout or 15.0, 60.0))
                self.client.connect()
                self._login()
            else:
                self.client = SSHClient(self.target, os.environ.get("TRUENAS_SSH_OPTS", ""))
                self.client.call("core.ping", timeout=self.timeout or 30.0)
        except ApiError as exc:
            fail(f"could not talk to the middleware via {self.where}", EXIT_ERROR, **exc.as_dict())
        except SystemExit:
            raise
        except Exception as exc:  # connection refused, TLS failure, DNS, timeout, ssh 255
            cause, hint = classify_connection_error(exc)
            if self.transport == "ssh" and cause != "ssh-auth":
                cause, hint = "ssh", ("ssh could not reach the NAS: check TRUENAS_SSH_HOST, your key, and any "
                                      "TRUENAS_SSH_OPTS jump host. Error above is ssh's own message")
            fail(f"could not connect to {self.where}: {exc}", EXIT_ERROR, cause=cause, hint=hint)
        return self.client

    def __exit__(self, *exc):
        if self.client is not None:
            self.client.close()

    def _login(self):
        # 25.x servers accept the raw user-linked key via auth.login_with_api_key. When the
        # username is known we use auth.login_ex with API_KEY_PLAIN, which newer clients do
        # and which 26.x expects for legacy keys.
        c = self.client
        try:
            if self.user:
                resp = c.call("auth.login_ex", {"mechanism": "API_KEY_PLAIN", "username": self.user,
                                                "api_key": self.key})
                ok = isinstance(resp, dict) and resp.get("response_type") == "SUCCESS"
                detail = resp if not ok else None
            else:
                ok = bool(c.call("auth.login_with_api_key", self.key))
                detail = None
        except ApiError as exc:
            fail(f"authentication call failed: {exc.detail}", EXIT_ERROR)
        if not ok:
            fail("authentication failed: API key rejected", EXIT_ERROR, response=detail, hint=(
                "regenerate the key in the TrueNAS UI (user menu > API Keys) and make sure the "
                "user it belongs to is allowed API access"))


def run_call(client, method: str, params: list, job: bool, timeout: float | None):
    if not job:
        return client.call(method, *params, timeout=timeout)
    last = {"percent": None, "description": None}

    def progress(j):
        p = (j or {}).get("progress") or {}
        pct, desc = p.get("percent"), p.get("description")
        if (pct, desc) != (last["percent"], last["description"]):
            last.update(percent=pct, description=desc)
            pct_s = f"{pct}% " if pct is not None else ""
            print(f"job {j.get('id', '')}: {pct_s}{desc or ''}".replace("job : ", "job: ").rstrip(), file=sys.stderr)

    return client.call(method, *params, job=True, progress=progress, timeout=timeout)


# --------------------------------------------------------------------------- subcommands


def cmd_info(args):
    sess = Session(args.insecure, args.timeout, args.transport)
    with sess as c:
        info = c.call("system.info")
        out = {
            "transport": sess.transport,
            "target": sess.where,
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
    if sess.transport == "ws":
        try:
            ctx = ssl.create_default_context()
            if not sess.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(http_base(sess.uri) + "/api/versions", context=ctx, timeout=10) as r:
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
    with Session(args.insecure, args.timeout, args.transport) as c:
        try:
            result = run_call(c, args.method, params, args.job, args.timeout)
        except ApiError as exc:
            fail(f"{args.method} failed", EXIT_ERROR, **exc.as_dict())
    emit_result(result, args, args.method)


def cmd_methods(args):
    with Session(args.insecure, args.timeout, args.transport) as c:
        try:
            methods = c.call("core.get_methods")
        except ApiError as exc:
            fail("core.get_methods failed", EXIT_ERROR, **exc.as_dict())
    needle = (args.prefix or "").lower()
    if args.exact:
        selected = {k: v for k, v in methods.items() if k.lower() == needle}
        if not selected:
            close = sorted(k for k in methods if needle.rsplit(".", 1)[0] in k.lower())[:10]
            fail(f"no such method {args.prefix!r} on this TrueNAS version", EXIT_ERROR, similar=close)
    else:
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
            entry["gated_by_tn"] = True  # tn.py's own --confirm gate, not server data
        if args.schema:
            for key in ("accepts", "returns", "filterable", "item_method", "examples"):
                if key in meta and meta[key] not in (None, [], False, ""):
                    entry[key] = meta[key]
        out[name] = entry
    emit(out, args.compact)


def cmd_jobs(args):
    with Session(args.insecure, args.timeout, args.transport) as c:
        try:
            if args.id is not None:
                jobs = c.call("core.get_jobs", [["id", "=", args.id]])
                if not jobs:
                    fail(f"no job with id {args.id}", EXIT_ERROR)
                emit_result(jobs[0], args, "core.get_jobs")  # job arguments can carry passwords
                return
            filters = [["state", "=", "RUNNING"]] if args.running else []
            jobs = c.call("core.get_jobs", filters, {"order_by": ["-id"], "limit": args.limit})
        except ApiError as exc:
            fail("core.get_jobs failed", EXIT_ERROR, **exc.as_dict())
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
    with Session(args.insecure, args.timeout, args.transport) as c:
        try:
            result = c.call(method, filters, options)
        except ApiError as exc:
            fail(f"{method} failed", EXIT_ERROR, **exc.as_dict())
    emit_result(result, args, method)


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tn.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--transport", choices=["ws", "ssh"], default=None,
                   help="override TRUENAS_TRANSPORT for this invocation")
    p.add_argument("--insecure", action="store_true", help="skip TLS certificate verification (ws)")
    p.add_argument("--timeout", type=float, default=None, help="seconds to wait for a call or job")
    p.add_argument("--compact", action="store_true", help="single-line JSON output")
    p.add_argument("--show-secrets", action="store_true",
                   help="do not redact credential-looking values (only when the user asked for them)")
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

    s = sub.add_parser("methods", help="discover methods and their schemas",
                       description="Lists methods from core.get_methods. 'job' comes from the server; "
                                   "'gated_by_tn' is this tool's own --confirm gate, not server data.")
    s.add_argument("prefix", nargs="?", help="substring to match, e.g. pool.dataset")
    s.add_argument("--exact", action="store_true",
                   help="match the full method name only; exit 1 if it does not exist on this version")
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

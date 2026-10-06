"""Offline tests for skills/truenas/scripts/tn.py. No NAS needed: python -m unittest discover tests"""
import importlib.util
import json
import pathlib
import unittest

TN = pathlib.Path(__file__).resolve().parents[1] / "skills" / "truenas" / "scripts" / "tn.py"
spec = importlib.util.spec_from_file_location("tn", TN)
tn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tn)


class GateTests(unittest.TestCase):
    def test_destructive_methods_are_gated(self):
        for m in [
            "pool.dataset.delete", "pool.snapshot.delete", "pool.snapshot.rollback", "pool.export",
            "pool.create", "pool.detach", "disk.wipe", "system.reboot", "system.shutdown",
            "update.update", "app.delete", "app.rollback", "app.stop", "replication.run",
            "filesystem.setacl", "sharing.smb.delete", "iscsi.target.delete", "user.delete",
            "user.update", "pool.dataset.lock", "zfs.snapshot.rollback", "service.stop",
            "boot.environment.activate", "interface.commit", "certificate.delete",
        ]:
            self.assertTrue(tn.is_destructive(m), m)

    def test_reads_and_safe_writes_pass(self):
        for m in [
            "system.info", "pool.query", "pool.dataset.query", "pool.dataset.create",
            "pool.dataset.update", "pool.snapshot.create", "pool.snapshot.query",
            "sharing.smb.create", "sharing.smb.update", "sharing.nfs.query", "app.query",
            "app.start", "app.redeploy", "app.upgrade", "app.config", "app.rollback_versions",
            "app.upgrade_summary", "alert.list", "alert.dismiss", "service.start",
            "service.restart", "service.query", "update.check_available", "replication.query",
            "core.get_jobs", "core.get_methods", "user.query", "disk.query", "disk.temperatures",
            "boot.environment.query", "interface.query", "auth.me", "pool.scrub.run",
            "pool.dataset.details", "iscsi.target.query", "filesystem.getacl", "filesystem.stat",
        ]:
            self.assertFalse(tn.is_destructive(m), m)

    def test_gate_is_case_insensitive(self):
        self.assertTrue(tn.is_destructive("Pool.Dataset.DELETE"))


class ParsingTests(unittest.TestCase):
    def test_parse_arg_json_or_string(self):
        self.assertEqual(tn.parse_arg('{"a": 1}'), {"a": 1})
        self.assertEqual(tn.parse_arg("[1, 2]"), [1, 2])
        self.assertEqual(tn.parse_arg("42"), 42)
        self.assertEqual(tn.parse_arg("true"), True)
        self.assertEqual(tn.parse_arg("tank/photos"), "tank/photos")
        self.assertEqual(tn.parse_arg("tank/photos@snap-1"), "tank/photos@snap-1")

    def test_parse_filter(self):
        self.assertEqual(tn.parse_filter("name=tank/photos"), ["name", "=", "tank/photos"])
        self.assertEqual(tn.parse_filter("type!=VOLUME"), ["type", "!=", "VOLUME"])
        self.assertEqual(tn.parse_filter("name~^tank/"), ["name", "~", "^tank/"])
        self.assertEqual(tn.parse_filter("used.parsed>1000"), ["used.parsed", ">", 1000])
        self.assertEqual(tn.parse_filter("level in [\"CRITICAL\",\"ERROR\"]"),
                         ["level", "in", ["CRITICAL", "ERROR"]])
        with self.assertRaises(Exception):
            tn.parse_filter("garbage")

    def test_build_uri(self):
        self.assertEqual(tn.build_uri("nas.local", "wss"), "wss://nas.local/api/current")
        self.assertEqual(tn.build_uri("10.0.0.5:8443", "wss"), "wss://10.0.0.5:8443/api/current")
        self.assertEqual(tn.build_uri("ws://nas.local", "wss"), "ws://nas.local/api/current")
        self.assertEqual(tn.build_uri("wss://nas.local/api/current/", "ws"), "wss://nas.local/api/current")
        self.assertEqual(tn.http_base("wss://nas.local/api/current"), "https://nas.local")

    def test_bare_host(self):
        for raw, want in [
            ("nas.local", "nas.local"),
            ("nas.local:8443", "nas.local"),
            ("10.0.0.5:443", "10.0.0.5"),
            ("wss://nas.local/api/current", "nas.local"),
            ("ws://nas.local:8080/api/current", "nas.local"),
            ("https://nas.local", "nas.local"),
            ("[fd00::10]:443", "fd00::10"),
            (" nas.local ", "nas.local"),
        ]:
            self.assertEqual(tn.bare_host(raw), want, raw)

    def test_classify_connection_error(self):
        import socket
        import ssl
        self.assertEqual(tn.classify_connection_error(ConnectionRefusedError(111, "Connection refused"))[0], "refused")
        self.assertEqual(tn.classify_connection_error(socket.timeout("timed out"))[0], "timeout")
        self.assertEqual(tn.classify_connection_error(TimeoutError())[0], "timeout")
        self.assertEqual(tn.classify_connection_error(ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))[0], "certificate")
        self.assertEqual(tn.classify_connection_error(Exception("Handshake status 200"))[0], "unknown")
        self.assertEqual(tn.classify_connection_error(socket.gaierror(-2, "Name or service not known"))[0], "dns")
        # websocket-client wraps errors in its own types; classification must work on the text too
        self.assertEqual(tn.classify_connection_error(Exception("[Errno 111] Connection refused"))[0], "refused")
        self.assertEqual(tn.classify_connection_error(Exception("certificate verify failed: self signed"))[0], "certificate")
        for cause in ("refused", "timeout", "certificate", "dns", "unknown"):
            self.assertTrue(tn.classify_connection_error(Exception(cause if cause != "unknown" else "x"))[1])

    def test_describe_exception_plain(self):
        info = tn.describe_exception(RuntimeError("boom"))
        self.assertEqual(info["detail"], "boom")
        json.dumps(info)


class CliTests(unittest.TestCase):
    def test_gated_call_exits_2_before_connecting(self):
        with self.assertRaises(SystemExit) as cm:
            tn.main(["call", "pool.dataset.delete", "tank/photos"])
        self.assertEqual(cm.exception.code, tn.EXIT_GATED)

    def test_host_subcommand(self):
        import contextlib
        import io
        import os
        os.environ["TRUENAS_HOST"] = "wss://nas.lan:8443/api/current"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tn.main(["host"])
        self.assertEqual(buf.getvalue().strip(), "nas.lan")
        os.environ.pop("TRUENAS_HOST")
        with self.assertRaises(SystemExit) as cm:
            tn.main(["host"])
        self.assertEqual(cm.exception.code, tn.EXIT_SETUP)

    def test_every_subcommand_has_help(self):
        p = tn.build_parser()
        for cmd in ["info", "host", "call", "methods", "jobs", "query"]:
            with self.assertRaises(SystemExit) as cm:
                p.parse_args([cmd, "--help"])
            self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

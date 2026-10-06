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
        self.assertEqual(tn.classify_connection_error(Exception("Handshake status 200"))[0], "handshake")
        self.assertEqual(tn.classify_connection_error(Exception("something odd"))[0], "unknown")
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


class WebsocketCodecTests(unittest.TestCase):
    @staticmethod
    def reader(data: bytes):
        buf = bytearray(data)

        def read_exact(n):
            out = bytes(buf[:n]); del buf[:n]
            if len(out) != n:
                raise ConnectionError("short read")
            return out
        return read_exact

    def test_frame_roundtrip_all_length_encodings(self):
        for n in (0, 1, 125, 126, 65535, 65536, 200000):
            payload = bytes(i % 251 for i in range(n))
            frame = tn.ws_encode_frame(payload)
            fin, opcode, back = tn.ws_decode_frame(self.reader(frame))
            self.assertTrue(fin); self.assertEqual(opcode, 1); self.assertEqual(back, payload, n)
            self.assertTrue(frame[1] & 0x80, "client frames must be masked")

    def test_unmasked_server_frame_decodes(self):
        payload = b'{"jsonrpc":"2.0"}'
        frame = bytes([0x81, len(payload)]) + payload
        self.assertEqual(tn.ws_decode_frame(self.reader(frame)), (True, 1, payload))

    def test_accept_key_matches_rfc_example(self):
        self.assertEqual(tn.ws_accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_error_mapping(self):
        with self.assertRaises(tn.ApiError) as cm:
            tn.WSClient._raise_for_error({"code": -32602, "message": "Invalid params",
                                          "data": {"error": 22, "extra": [["a.b", "bad", 17]]}})
        self.assertEqual(cm.exception.validation_errors[0]["attribute"], "a.b")
        self.assertEqual(cm.exception.as_dict()["error_type"], "ValidationErrors")
        with self.assertRaises(tn.ApiError) as cm:
            tn.WSClient._raise_for_error({"code": -32001, "message": "x", "data": {
                "error": 16, "reason": "busy", "trace": {"repr": "CallError(EBUSY)", "formatted": "tb"}}})
        self.assertEqual(cm.exception.errno, 16)
        self.assertEqual(cm.exception.server_exception, "CallError(EBUSY)")
        with self.assertRaises(tn.ApiError) as cm:
            tn.WSClient._raise_for_error({"code": -32601, "message": "Method not found"})
        self.assertEqual(cm.exception.detail, "Method not found")


class SshTransportUnitTests(unittest.TestCase):
    def test_ssh_command_root_no_sudo(self):
        import os
        os.environ.pop("TRUENAS_SSH_SUDO", None)
        argv = tn.ssh_command("root@nas", "pool.dataset.query", [[["name", "=", "tank"]], {"limit": 1}], False)
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[-2], "root@nas")
        self.assertEqual(argv[-1], """midclt call pool.dataset.query '[["name", "=", "tank"]]' '{"limit": 1}'""")

    def test_ssh_command_sudo_for_other_user_and_job(self):
        import os
        os.environ.pop("TRUENAS_SSH_SUDO", None)
        argv = tn.ssh_command("admin@nas", "pool.scrub.run", ["tank"], True)
        self.assertTrue(argv[-1].startswith("sudo -n midclt call -j pool.scrub.run "), argv[-1])
        self.assertIn("'\"tank\"'", argv[-1])  # strings are JSON-quoted so midclt parses them back
        os.environ["TRUENAS_SSH_SUDO"] = "0"
        self.assertTrue(tn.ssh_command("admin@nas", "x.y", [], False)[-1].startswith("midclt"))
        os.environ["TRUENAS_SSH_SUDO"] = "1"
        self.assertTrue(tn.ssh_command("root@nas", "x.y", [], False)[-1].startswith("sudo -n"))
        os.environ.pop("TRUENAS_SSH_SUDO")

    def test_ssh_command_extra_opts(self):
        argv = tn.ssh_command("root@nas", "x.y", [], False, extra_opts="-J jump -p 2222")
        self.assertEqual(argv[argv.index("-J") + 1], "jump")
        self.assertEqual(argv[argv.index("-p") + 1], "2222")

    def test_parse_midclt_stderr(self):
        err = tn.parse_midclt_stderr("[EEXIST] pool_dataset_create.name: Dataset 'bad' already exists\n")
        self.assertEqual(err.validation_errors, [{"attribute": "pool_dataset_create.name",
                                                  "message": "Dataset 'bad' already exists", "errname": "EEXIST"}])
        err = tn.parse_midclt_stderr("Dataset is busy\nTraceback (most recent call last):\n  x\nCallError: [EBUSY] Dataset is busy\n")
        self.assertEqual(err.detail, "Dataset is busy")
        self.assertEqual(err.server_exception, "CallError: [EBUSY] Dataset is busy")
        self.assertIn("Traceback", err.traceback)
        err = tn.parse_midclt_stderr("Failed to run middleware call. Daemon not running?")
        self.assertIn("not reachable", err.detail)

    def test_ssh_target_derivation(self):
        import os
        for k in ("TRUENAS_SSH_HOST", "TRUENAS_HOST"):
            os.environ.pop(k, None)
        self.assertIsNone(tn.ssh_target())
        os.environ["TRUENAS_HOST"] = "wss://nas.lan:8443/api/current"
        self.assertEqual(tn.ssh_target(), "root@nas.lan")
        os.environ["TRUENAS_SSH_HOST"] = "admin@10.0.0.2"
        self.assertEqual(tn.ssh_target(), "admin@10.0.0.2")
        for k in ("TRUENAS_SSH_HOST", "TRUENAS_HOST"):
            os.environ.pop(k, None)

    def test_choose_transport(self):
        import contextlib
        import io
        import os
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        for k in ("TRUENAS_TRANSPORT", "TRUENAS_SSH_HOST", "TRUENAS_HOST", "TRUENAS_API_KEY"):
            os.environ.pop(k, None)
        with self.assertRaises(SystemExit) as cm:
            tn.choose_transport()
        self.assertEqual(cm.exception.code, tn.EXIT_SETUP)
        os.environ["TRUENAS_HOST"] = "nas"
        self.assertEqual(tn.choose_transport(), "ssh")
        os.environ["TRUENAS_API_KEY"] = "k"
        self.assertEqual(tn.choose_transport(), "ws")
        os.environ["TRUENAS_TRANSPORT"] = "ssh"
        self.assertEqual(tn.choose_transport(), "ssh")
        self.assertEqual(tn.choose_transport("ws"), "ws")
        os.environ["TRUENAS_TRANSPORT"] = "carrier-pigeon"
        with self.assertRaises(SystemExit):
            tn.choose_transport()
        for k in ("TRUENAS_TRANSPORT", "TRUENAS_HOST", "TRUENAS_API_KEY"):
            os.environ.pop(k, None)


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

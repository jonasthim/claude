"""End-to-end tests for tn.py over both transports, fully offline.

ws:  tn.py (standard library only, run with this interpreter) against tests/fake_middleware.py,
     which needs the `websockets` package in some interpreter: this one, or the one named by
     TRUENAS_TEST_PYTHON. Skipped when neither has it.
ssh: tn.py against tests/fake_ssh.py installed as `ssh` on a private PATH. Always runs.
"""
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TN = ROOT / "skills" / "truenas" / "scripts" / "tn.py"
SERVER = ROOT / "tests" / "fake_middleware.py"
FAKE_SSH = ROOT / "tests" / "fake_ssh.py"


def server_python():
    for candidate in (sys.executable, os.environ.get("TRUENAS_TEST_PYTHON")):
        if candidate and os.path.exists(candidate):
            if subprocess.run([candidate, "-c", "import websockets"], capture_output=True).returncode == 0:
                return candidate
    return None


def run_tn(args, env, stdin=None, timeout=60):
    p = subprocess.run([sys.executable, str(TN), *args], capture_output=True, text=True, env=env,
                       input=stdin, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


SERVER_PY = server_python()


@unittest.skipIf(SERVER_PY is None, "websockets package not available for the fake middleware")
class WebsocketTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.server = subprocess.Popen([SERVER_PY, str(SERVER), str(cls.port)], stdout=subprocess.PIPE, text=True)
        if "ready" not in cls.server.stdout.readline():
            raise RuntimeError("fake middleware did not start")
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("TRUENAS_")}
        cls.env.update(TRUENAS_HOST=f"ws://127.0.0.1:{cls.port}", TRUENAS_API_KEY="good-key")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    def tn(self, *args, env=None):
        return run_tn(["--timeout", "10", *args], env or self.env)

    def test_info(self):
        code, out, err = self.tn("info")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["transport"], "ws")
        self.assertEqual(data["hostname"], "fakenas")
        self.assertEqual(data["alerts"]["active"], 1)
        self.assertEqual(data["alerts"]["by_level"], {"WARNING": 1})
        self.assertEqual(data["api_versions"], ["v25.04.0", "v25.10.0"])
        self.assertEqual(data["running_jobs"], [])

    def test_call_plain(self):
        code, out, _ = self.tn("call", "system.info")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hostname"], "fakenas")

    def test_large_response_is_reassembled(self):
        code, out, err = self.tn("call", "test.big", "200000")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)), 200000)

    def test_query_builds_filters_and_options(self):
        code, out, err = self.tn("query", "pool.dataset", "--filter", "name=tank/photos", "--filter", "used.parsed>10",
                                 "--select", "name,used.parsed", "--limit", "5", "--order-by=-name",
                                 "--extra", '{"flat": false}')
        self.assertEqual(code, 0, err)
        echo = json.loads(out)["echo"]
        self.assertEqual(echo["filters"], [["name", "=", "tank/photos"], ["used.parsed", ">", 10]])
        self.assertEqual(echo["options"], {"select": ["name", "used.parsed"], "limit": 5,
                                           "order_by": ["-name"], "extra": {"flat": False}})

    def test_job_waits_and_streams_progress(self):
        code, out, err = self.tn("call", "pool.scrub.run", "tank", "--job")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), "scrub started")
        self.assertIn("50%", err)
        self.assertIn("scrubbing", err)

    def test_job_without_flag_returns_id(self):
        code, out, _ = self.tn("call", "pool.scrub.run", "tank")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), 7)

    def test_failed_job_reports_error(self):
        code, out, err = self.tn("call", "test.failing_job", "--job")
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("job 8: 10% x"), err)   # progress line, then the error JSON
        info = json.loads(err[err.index("{"):])
        self.assertIn("disk on fire", info["detail"])
        self.assertEqual(info["server_exception"], "CallError: [EIO] disk on fire")

    def test_validation_error(self):
        code, out, err = self.tn("call", "pool.dataset.create", '{"name": "bad"}')
        self.assertEqual(code, 1)
        info = json.loads(err)
        self.assertEqual(info["error_type"], "ValidationErrors")
        self.assertEqual(info["validation_errors"][0]["attribute"], "pool_dataset_create.name")
        self.assertIn("already exists", info["validation_errors"][0]["message"])

    def test_call_error_with_confirm(self):
        code, out, err = self.tn("call", "pool.dataset.delete", "tank/x", "--confirm")
        self.assertEqual(code, 1)
        info = json.loads(err)
        self.assertEqual(info["errno"], 16)
        self.assertIn("EBUSY", info["server_exception"])
        self.assertNotIn("traceback", info)

    def test_methods_listing_flags(self):
        code, out, err = self.tn("methods", "pool.dataset")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["pool.dataset.delete"]["requires_confirm"])
        self.assertTrue(data["pool.dataset.delete"]["job"])
        self.assertNotIn("requires_confirm", data["pool.dataset.create"])
        self.assertEqual(data["pool.dataset.create"]["description"], "Create a dataset.")
        code, out, _ = self.tn("methods", "pool.dataset.create", "--schema")
        self.assertIn("accepts", json.loads(out)["pool.dataset.create"])

    def test_jobs_by_id(self):
        code, out, err = self.tn("jobs", "--id", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["state"], "SUCCESS")

    def test_bad_key(self):
        env = dict(self.env, TRUENAS_API_KEY="nope")
        code, _, err = self.tn("info", env=env)
        self.assertEqual(code, 1)
        self.assertIn("authentication failed", json.loads(err)["error"])

    def test_login_ex_with_username(self):
        env = dict(self.env, TRUENAS_USER="admin")
        code, out, err = self.tn("call", "system.info", env=env)
        self.assertEqual(code, 0, err)
        env = dict(self.env, TRUENAS_USER="someone-else")
        code, _, err = self.tn("call", "system.info", env=env)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["response"], {"response_type": "AUTH_ERR"})

    def test_stdin_args(self):
        code, out, err = run_tn(["call", "pool.dataset.query", "-"], self.env,
                                stdin='[[["name","=","x"]], {"limit": 1}]')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["echo"]["options"], {"limit": 1})

    def test_host_subcommand(self):
        code, out, _ = self.tn("host")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "127.0.0.1")

    def test_refused_port_is_classified(self):
        env = dict(self.env, TRUENAS_HOST="ws://127.0.0.1:9")
        code, _, err = self.tn("info", env=env)
        self.assertEqual(code, 1)
        info = json.loads(err)
        self.assertEqual(info["cause"], "refused")
        self.assertIn("port", info["hint"])

    def test_gated_call_exits_2_before_connecting(self):
        env = dict(self.env, TRUENAS_HOST="ws://127.0.0.1:9")  # would fail to connect if it tried
        code, _, err = self.tn("call", "pool.dataset.delete", "tank/photos", env=env)
        self.assertEqual(code, 2)


class SshTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        fake = pathlib.Path(cls.tmp.name) / "ssh"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SSH}" "$@"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        cls.log = pathlib.Path(cls.tmp.name) / "ssh.log"
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("TRUENAS_")}
        cls.env.update(PATH=f"{cls.tmp.name}:{os.environ.get('PATH', '')}", FAKE_SSH_LOG=str(cls.log),
                       TRUENAS_SSH_HOST="root@fakenas")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tn(self, *args, env=None):
        return run_tn(["--timeout", "10", *args], env or self.env)

    def last_argv(self):
        return json.loads(self.log.read_text().strip().splitlines()[-1])

    def test_ssh_chosen_without_api_key(self):
        code, out, err = self.tn("info")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["transport"], "ssh")
        self.assertEqual(data["target"], "ssh://root@fakenas")
        self.assertEqual(data["hostname"], "fakenas-ssh")
        self.assertEqual(data["alerts"]["by_level"], {"CRITICAL": 1})
        self.assertNotIn("api_versions", data)

    def test_ssh_derived_from_host_when_only_host_set(self):
        env = dict(self.env, TRUENAS_HOST="wss://nas.lan:8443/api/current")
        env.pop("TRUENAS_SSH_HOST")
        code, out, err = self.tn("call", "system.info", env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last_argv()[-2], "root@nas.lan")

    def test_transport_flag_overrides_auto(self):
        env = dict(self.env, TRUENAS_HOST="ws://127.0.0.1:9", TRUENAS_API_KEY="k")  # would pick ws
        code, out, err = self.tn("--transport", "ssh", "call", "system.info", env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["hostname"], "fakenas-ssh")

    def test_query_args_are_json_quoted(self):
        code, out, err = self.tn("query", "pool.dataset", "--filter", "name=tank/photos", "--select", "name,used")
        self.assertEqual(code, 0, err)
        echo = json.loads(out)["echo"]
        self.assertEqual(echo["filters"], [["name", "=", "tank/photos"]])
        self.assertEqual(echo["options"], {"select": ["name", "used"]})
        self.assertFalse(echo["sudo"])
        remote = self.last_argv()[-1]
        self.assertTrue(remote.startswith("midclt call pool.dataset.query "), remote)

    def test_sudo_added_for_non_root_user(self):
        env = dict(self.env, TRUENAS_SSH_HOST="admin@fakenas")
        code, out, err = self.tn("query", "pool.dataset", env=env)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["echo"]["sudo"])
        env["TRUENAS_SSH_SUDO"] = "0"
        code, out, err = self.tn("query", "pool.dataset", env=env)
        self.assertFalse(json.loads(out)["echo"]["sudo"])

    def test_sudo_password_prompt_is_explained(self):
        env = dict(self.env, TRUENAS_SSH_HOST="nosudo@fakenas")
        code, _, err = self.tn("call", "system.info", env=env)
        self.assertEqual(code, 1)
        self.assertIn("passwordless sudo", json.loads(err)["detail"])

    def test_job_over_ssh(self):
        code, out, err = self.tn("call", "pool.scrub.run", "tank", "--job")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), "scrub started")
        self.assertIn("scrubbing", err)
        self.assertIn("-j", self.last_argv()[-1].split())
        code, out, _ = self.tn("call", "pool.scrub.run", "tank")
        self.assertEqual(json.loads(out), 7)

    def test_validation_error_over_ssh(self):
        code, _, err = self.tn("call", "pool.dataset.create", '{"name": "bad"}')
        self.assertEqual(code, 1)
        info = json.loads(err)
        self.assertEqual(info["error_type"], "ValidationErrors")
        self.assertEqual(info["validation_errors"][0]["attribute"], "pool_dataset_create.name")
        self.assertEqual(info["validation_errors"][0]["errname"], "EEXIST")

    def test_call_error_over_ssh(self):
        code, _, err = self.tn("call", "pool.dataset.delete", "tank/x", "--confirm")
        self.assertEqual(code, 1)
        info = json.loads(err)
        self.assertEqual(info["detail"], "Dataset is busy")
        self.assertEqual(info["server_exception"], "CallError: [EBUSY] Dataset is busy")
        self.assertNotIn("traceback", info)

    def test_unreachable_host_is_reported(self):
        env = dict(self.env, TRUENAS_SSH_HOST="root@unreachable")
        code, _, err = self.tn("info", env=env)
        self.assertEqual(code, 1)
        info = json.loads(err)
        self.assertEqual(info["cause"], "ssh")
        self.assertIn("No route to host", info["error"])

    def test_ssh_opts_are_passed(self):
        env = dict(self.env, TRUENAS_SSH_OPTS="-J jump.lan")
        code, _, err = self.tn("call", "system.info", env=env)
        self.assertEqual(code, 0, err)
        argv = self.last_argv()
        self.assertIn("-J", argv)
        self.assertEqual(argv[argv.index("-J") + 1], "jump.lan")

    def test_methods_and_jobs_over_ssh(self):
        code, out, err = self.tn("methods", "pool.scrub")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["pool.scrub.run"]["job"])
        code, out, err = self.tn("jobs", "--id", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["state"], "SUCCESS")

    def test_nothing_configured_exits_3(self):
        env = {k: v for k, v in self.env.items() if not k.startswith("TRUENAS_")}
        code, _, err = self.tn("info", env=env)
        self.assertEqual(code, 3)
        self.assertIn("no connection configured", json.loads(err)["error"])


if __name__ == "__main__":
    unittest.main()

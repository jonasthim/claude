"""End-to-end tests: tn.py against tests/fake_middleware.py over a real websocket.

Needs truenas_api_client and websockets importable, either in the current interpreter or in
the venv named by TRUENAS_VENV (default ~/.cache/truenas-skill/venv). Skipped otherwise.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TN = ROOT / "skills" / "truenas" / "scripts" / "tn.py"
SERVER = ROOT / "tests" / "fake_middleware.py"


def venv_python():
    venv = os.environ.get("TRUENAS_VENV", os.path.expanduser("~/.cache/truenas-skill/venv"))
    py = os.path.join(venv, "bin", "python")
    for candidate in (sys.executable, py):
        if os.path.exists(candidate):
            ok = subprocess.run([candidate, "-c", "import truenas_api_client, websockets"],
                                capture_output=True).returncode == 0
            if ok:
                return candidate
    return None


PY = venv_python()


@unittest.skipIf(PY is None, "truenas_api_client and websockets not available")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.server = subprocess.Popen([PY, str(SERVER), str(cls.port)], stdout=subprocess.PIPE, text=True)
        line = cls.server.stdout.readline()
        if "ready" not in line:
            raise RuntimeError("fake middleware did not start")
        cls.env = dict(os.environ, TRUENAS_HOST=f"ws://127.0.0.1:{cls.port}", TRUENAS_API_KEY="good-key",
                       TRUENAS_VENV_REEXEC="1")
        cls.env.pop("TRUENAS_USER", None)

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    def tn(self, *args, env=None, timeout=30):
        p = subprocess.run([PY, str(TN), "--timeout", "10", *args], capture_output=True, text=True,
                           env=env or self.env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def test_info(self):
        code, out, err = self.tn("info")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["hostname"], "fakenas")
        self.assertEqual(data["alerts"]["active"], 1)
        self.assertEqual(data["alerts"]["by_level"], {"WARNING": 1})
        self.assertEqual(data["api_versions"], ["v25.04.0", "v25.10.0"])
        self.assertEqual(data["running_jobs"], [])

    def test_call_plain(self):
        code, out, _ = self.tn("call", "system.info")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hostname"], "fakenas")

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
        p = subprocess.run([PY, str(TN), "call", "pool.dataset.query", "-"], input='[[["name","=","x"]], {"limit": 1}]',
                           capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["echo"]["options"], {"limit": 1})


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline tests for pangolin.py: the fixture backend, and the real HTTP path against the mock server.

    python3 -m unittest discover -v tests        (from plugins/pangolin)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "pangolin")
CLI = os.path.join(SKILL, "scripts", "pangolin.py")
FIXTURES = os.path.join(SKILL, "evals", "fixtures")
sys.path.insert(0, os.path.join(SKILL, "evals"))
import mock_server  # noqa: E402


def run(args, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("PANGOLIN_")}
    base.update(env)
    p = subprocess.run([sys.executable, "-I", CLI] + list(args), capture_output=True, text=True, env=base)
    return p.returncode, p.stdout, p.stderr


class FixtureBackend(unittest.TestCase):
    def cli(self, *args):
        return run(args, PANGOLIN_MOCK_DIR=FIXTURES, PANGOLIN_ORG="acme")

    def test_reads(self):
        for cmd in (["info"], ["access"], ["sites", "list"], ["sites", "get", "hq"], ["resources", "list"],
                    ["resources", "get", "wiki"], ["resources", "auth", "102"], ["targets", "list", "wiki"],
                    ["rules", "list", "wiki"], ["domains", "list"], ["idps", "list"], ["report", "health"],
                    ["report", "health", "--table"], ["raw", "GET", "/org/{orgId}/sites", "--all"]):
            code, out, err = self.cli(*cmd)
            self.assertEqual(code, 0, "%s: %s" % (cmd, err))
            self.assertTrue(out.strip())

    def test_write_is_refused_without_yes(self):
        code, out, err = self.cli("resources", "disable", "wiki")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["body"], {"enabled": False})
        self.assertIn("Not sent", err)

    def test_dry_run_never_sends_even_with_yes(self):
        code, out, _ = self.cli("resources", "delete", "wiki", "--dry-run", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["dry_run"])

    def test_resource_resolves_by_nice_id_domain_and_id(self):
        for ref in ("dashboards", "dashboards.example.com", "102"):
            code, out, _ = self.cli("resources", "get", ref)
            self.assertEqual((code, json.loads(out)["resourceId"]), (0, 102))

    def test_unknown_and_ambiguous_resource(self):
        self.assertEqual(self.cli("resources", "get", "nope")[0], 1)

    def test_target_update_carries_site_and_ip(self):
        code, out, _ = self.cli("targets", "update", "22", "--body", '{"hcHostname": "198.51.100.141"}', "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["body"],
                         {"hcHostname": "198.51.100.141", "siteId": 2, "ip": "198.51.100.141"})

    def test_rule_update_carries_priority(self):
        code, out, _ = self.cli("rules", "update", "wiki", "2", "--body", '{"enabled": false}', "--dry-run")
        self.assertEqual(json.loads(out)["body"], {"enabled": False, "priority": 2})

    def test_health_check_needs_hostname(self):
        body = '{"siteId": 1, "ip": "198.51.100.13", "port": 8080, "hcEnabled": true}'
        code, _, err = self.cli("targets", "create", "wiki", "--body", body, "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("hcHostname", err)

    def test_secrets_are_redacted(self):
        code, out, _ = self.cli("targets", "get", "61")
        self.assertNotIn("mock-probe-token", out)
        code, out, _ = self.cli("targets", "get", "61", "--show-secrets")
        self.assertIn("mock-probe-token", out)
        code, out, _ = self.cli("raw", "POST", "/resource/101/password", "--body", '{"password": "hunter22"}', "--dry-run")
        self.assertNotIn("hunter22", out)

    def test_ids_that_only_flag_a_secret_stay_visible(self):
        code, out, _ = self.cli("resources", "find", "status")
        self.assertEqual(json.loads(out)[0]["passwordId"], 7)

    def test_report_flags_the_story(self):
        code, out, _ = self.cli("report", "health")
        s = json.loads(out)["summary"]
        self.assertEqual(s["sitesOffline"], ["Lab"])
        self.assertEqual(s["resourcesUnhealthy"], ["Dashboards", "Status page"])
        self.assertEqual(s["resourcesWithoutAuth"], ["Files"])
        self.assertEqual(s["resourcesDisabled"], ["Old blog"])

    def test_missing_arguments(self):
        self.assertEqual(self.cli("targets", "list")[0], 1)
        self.assertEqual(self.cli("rules", "delete", "wiki")[0], 1)
        self.assertEqual(self.cli("resources", "create")[0], 1)

    def test_setup_error_without_environment(self):
        code, _, err = run(["info"])
        self.assertEqual(code, 1)
        self.assertIn("error[setup]", err)


class HttpPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.log = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        cls.srv = mock_server.serve(FIXTURES, key="mock.key", log_path=cls.log, max_page=2)
        cls.host = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.remove(cls.log)

    def cli(self, *args, **env):
        base = {"PANGOLIN_HOST": self.host, "PANGOLIN_API_KEY": "mock.key", "PANGOLIN_ORG": "acme"}
        base.update(env)
        return run(args, **base)

    def requests(self):
        with open(self.log) as f:
            return [line.split()[1:] for line in f]

    def test_envelope_is_unwrapped(self):
        code, out, err = self.cli("sites", "get", "hq")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["siteId"], 1)

    def test_both_pagination_styles_are_followed(self):
        # the server caps pages at 2 items: 6 resources (page style) and 8 targets (offset style)
        code, out, _ = self.cli("resources", "list")
        self.assertEqual(len(json.loads(out)), 6)
        code, out, _ = self.cli("raw", "GET", "/resource/101/targets", "--all")
        self.assertEqual(len(json.loads(out)), 2)
        code, out, _ = self.cli("raw", "GET", "/org/{orgId}/domains", "--all", "--query", "limit=1")
        self.assertEqual(len(json.loads(out)), 2)

    def test_bad_key_is_an_auth_error(self):
        code, _, err = self.cli("sites", "list", PANGOLIN_API_KEY="wrong.key")
        self.assertEqual(code, 2)
        self.assertIn("error[auth]", err)
        self.assertNotIn("wrong.key", err)

    def test_missing_root_access_is_a_permission_error(self):
        code, _, err = self.cli("orgs", "list")
        self.assertEqual(code, 2)
        self.assertIn("error[permission]", err)

    def test_org_is_required_for_a_non_root_key(self):
        code, _, err = self.cli("sites", "list", PANGOLIN_ORG="")
        self.assertEqual(code, 1)
        self.assertIn("PANGOLIN_ORG", err)

    def test_not_found(self):
        code, _, err = self.cli("targets", "get", "999")
        self.assertEqual(code, 2)
        self.assertIn("error[not-found]", err)

    def test_refused_write_sends_nothing_and_confirmed_write_is_sent(self):
        before = len(self.requests())
        self.assertEqual(self.cli("resources", "disable", "101")[0], 3)
        self.assertEqual(self.cli("resources", "disable", "101", "--dry-run")[0], 0)
        self.assertEqual(len(self.requests()), before)
        code, out, err = self.cli("resources", "disable", "101", "--yes")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"])
        self.assertEqual(self.requests()[-1], ["POST", "/v1/resource/101", "200"])

    def test_create_uses_put(self):
        body = '{"siteId": 1, "ip": "198.51.100.13", "port": 8080}'
        self.assertEqual(self.cli("targets", "create", "101", "--body", body, "--yes")[0], 0)
        self.assertEqual(self.requests()[-1], ["PUT", "/v1/resource/101/target", "201"])

    def test_redirect_is_not_followed(self):
        code, _, err = self.cli("raw", "GET", "/redirect")
        self.assertEqual(code, 2)
        self.assertIn("error[redirect]", err)

    def test_connection_failures_are_classified(self):
        code, _, err = self.cli("info", PANGOLIN_HOST="http://127.0.0.1:1")
        self.assertEqual(code, 2)
        self.assertIn("error[refused]", err)
        code, _, err = self.cli("info", PANGOLIN_HOST="https://pangolin.invalid")
        self.assertIn("error[dns]", err)

    def test_tls_failure_is_reported_not_bypassed(self):
        # the mock server speaks plain http, so an https request to it fails the handshake
        code, _, err = self.cli("info", PANGOLIN_HOST=self.host.replace("http://", "https://"))
        self.assertEqual(code, 2)
        self.assertRegex(err, r"error\[(tls|certificate)\]")


if __name__ == "__main__":
    unittest.main()

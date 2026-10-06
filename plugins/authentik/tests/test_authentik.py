#!/usr/bin/env python3
"""Offline tests for authentik.py: the fixture backend, and the real HTTP path against the mock server.

    python3 -m unittest discover -v tests        (from plugins/authentik)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "authentik")
CLI = os.path.join(SKILL, "scripts", "authentik.py")
FIXTURES = os.path.join(SKILL, "evals", "fixtures")
sys.path.insert(0, os.path.join(SKILL, "evals"))
import mock_server  # noqa: E402


def run(args, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("AUTHENTIK_")}
    base.update(env)
    p = subprocess.run([sys.executable, "-I", CLI] + list(args), capture_output=True, text=True, env=base)
    return p.returncode, p.stdout, p.stderr


class FixtureBackend(unittest.TestCase):
    def cli(self, *args, **env):
        return run(args, **dict({"AUTHENTIK_MOCK_DIR": FIXTURES}, **env))

    def test_reads(self):
        for cmd in (["info"], ["apps", "list"], ["apps", "get", "wiki"], ["apps", "bindings", "wiki"],
                    ["apps", "bindings", "wiki", "--table"], ["apps", "access", "wiki"],
                    ["providers", "list"], ["providers", "list", "--type", "oauth2"], ["providers", "get", "3"],
                    ["providers", "setup-urls", "1"], ["groups", "list"], ["groups", "get", "wiki-users"],
                    ["groups", "members", "wiki-users"], ["users", "list"], ["users", "get", "alice"],
                    ["users", "find", "bob"], ["bindings", "list"], ["policies", "list"], ["flows", "list"],
                    ["outposts", "list"], ["outposts", "health", "ldap-outpost"], ["events", "list"],
                    ["mappings", "list"], ["tokens", "list"], ["report", "health"],
                    ["raw", "GET", "core/groups", "--all"]):
            code, out, err = self.cli(*cmd)
            self.assertEqual(code, 0, "%s: %s" % (cmd, err))
            self.assertTrue(out.strip())

    def test_write_is_refused_without_yes(self):
        code, out, err = self.cli("groups", "add-user", "dashboards-admins", "bob")
        self.assertEqual(code, 3)
        plan = json.loads(out)
        self.assertEqual(plan["body"], {"pk": 6})
        self.assertTrue(plan["url"].endswith("/core/groups/00000000-0000-4000-8000-000000000104/add_user/"))
        self.assertIn("Not sent", err)

    def test_dry_run_never_sends_even_with_yes(self):
        code, out, _ = self.cli("apps", "delete", "wiki", "--dry-run", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["dry_run"])

    def test_bindings_explain_the_engine_mode(self):
        out = json.loads(self.cli("apps", "bindings", "dashboards")[1])
        self.assertEqual(out["policy_engine_mode"], "all")
        self.assertIn("every enabled binding", out["meaning"])
        self.assertEqual([b["name"] for b in out["bindings"]], ["dashboards-users", "dashboards-admins"])
        out = json.loads(self.cli("apps", "bindings", "files")[1])
        self.assertIn("every user", out["meaning"])
        # a disabled binding does not gate anything
        self.assertIn("every user", json.loads(self.cli("apps", "bindings", "directory")[1])["meaning"])

    def test_access_check_for_another_user(self):
        code, out, _ = self.cli("apps", "access", "dashboards", "--user", "bob")
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["passing"])
        self.assertTrue(json.loads(self.cli("apps", "access", "dashboards", "--user", "alice")[1])["passing"])

    def test_access_check_for_another_user_needs_a_superuser(self):
        tmp = tempfile.mkdtemp()
        try:
            fixtures = os.path.join(tmp, "f")
            shutil.copytree(FIXTURES, fixtures)
            with open(os.path.join(fixtures, "me.json")) as f:
                me = json.load(f)
            me["user"]["is_superuser"] = False
            with open(os.path.join(fixtures, "me.json"), "w") as f:
                json.dump(me, f)
            code, _, err = self.cli("apps", "access", "dashboards", "--user", "bob", AUTHENTIK_MOCK_DIR=fixtures)
            self.assertEqual(code, 1)
            self.assertIn("superuser", err)
        finally:
            shutil.rmtree(tmp)

    def test_provider_writes_need_a_type(self):
        self.assertEqual(self.cli("providers", "patch", "1", "--body", "{}", "--dry-run")[0], 1)
        code, out, _ = self.cli("providers", "patch", "1", "--type", "oauth2", "--body", '{"name": "x"}', "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["url"].endswith("/providers/oauth2/1/"))

    def test_secrets_are_redacted(self):
        code, out, _ = self.cli("providers", "list", "--type", "oauth2")
        self.assertNotIn("do-not-print", out)
        self.assertIn("mock-client-id-1", out)
        self.assertNotIn("do-not-print", self.cli("providers", "get", "3")[1])
        self.assertNotIn("mock-leaked-context-token", self.cli("events", "list")[1])
        self.assertIn("do-not-print", self.cli("providers", "get", "1", "--show-secrets")[1])
        body = '{"name": "x", "client_secret": "s3cret-value"}'
        out = self.cli("providers", "create", "--type", "oauth2", "--body", body, "--dry-run")[1]
        self.assertNotIn("s3cret-value", out)

    def test_echoed_request_headers_are_masked(self):
        out = self.cli("raw", "GET", "/admin/system/")[1]
        self.assertNotIn("mock-echoed-bearer", out)
        self.assertNotIn("mock-echoed-cookie", out)
        self.assertIn("server_time", out)

    def test_credential_shaped_keys(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("authentik_cli", CLI)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        secret = {"client_secret": "x", "admin_integration_key": "x", "api_key": "x", "HTTP_AUTHORIZATION": "x",
                  "Cookie": "x", "auth": "x", "key": "x", "plex_token": "x", "bind_password": "x",
                  "kubeconfig": {"users": [1]}, "credentials": {"private_key": "x"}, "webhook_url": "x"}
        plain = {"signing_key": "uuid", "encryption_key": "uuid", "token_identifier": "name",
                 "authorization_flow": "uuid", "client_id": "id", "private_key_available": True}
        out = cli.redact(dict(secret, **plain))
        for k in secret:
            self.assertEqual(out[k], cli.REDACTED, k)
        for k, v in plain.items():
            self.assertEqual(out[k], v, k)

    def test_names_that_only_look_secret_stay_visible(self):
        out = self.cli("providers", "get", "1")[1]
        self.assertEqual(json.loads(out)["authorization_flow"], "00000000-0000-4000-8000-000000000301")
        self.assertIn("ak-outpost-", self.cli("outposts", "list")[1])

    def test_events_are_newest_first_and_bounded(self):
        rows = json.loads(self.cli("events", "list", "--limit", "3")[1])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["action"], "configuration_error")
        rows = json.loads(self.cli("events", "list", "--filter", "action=login_failed")[1])
        self.assertEqual({r["username"] for r in rows}, {"bob"})

    def test_report_flags_the_story(self):
        r = json.loads(self.cli("report", "health")[1])
        self.assertEqual(r["providersNotOnAnOutpost"], ["Files"])
        self.assertEqual(r["providersWithoutApplication"], ["Old cloud"])
        self.assertEqual(r["applicationsWithoutProvider"], ["legacy-portal"])
        self.assertEqual(r["applicationsRequiringEveryBinding"], ["dashboards"])
        self.assertIn("files", r["applicationsOpenToAllUsers"])
        self.assertNotIn("wiki", r["applicationsOpenToAllUsers"])
        self.assertEqual(r["outposts"][1]["outdated"], ["ldap-outpost-1"])
        self.assertEqual(r["recentEvents"]["counts"]["login_failed"], 2)

    def test_unknown_names(self):
        code, _, err = self.cli("groups", "get", "no-such-group")
        self.assertEqual(code, 2)
        self.assertIn("svc-automation", err)

    def test_missing_arguments(self):
        self.assertEqual(self.cli("apps", "get")[0], 1)
        self.assertEqual(self.cli("apps", "create")[0], 1)
        self.assertEqual(self.cli("groups", "add-user", "wiki-users")[0], 1)

    def test_setup_error_without_environment(self):
        code, _, err = run(["info"])
        self.assertEqual(code, 1)
        self.assertIn("error[setup]", err)


class HttpPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.log = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        cls.srv = mock_server.serve(FIXTURES, token="mock-token", log_path=cls.log, max_page=2)
        cls.host = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.remove(cls.log)

    def cli(self, *args, **env):
        base = {"AUTHENTIK_HOST": self.host, "AUTHENTIK_TOKEN": "mock-token"}
        base.update(env)
        return run(args, **base)

    def requests(self):
        with open(self.log) as f:
            return [line.split()[1:] for line in f]

    def test_info(self):
        code, out, err = self.cli("info")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["version"]["version_current"], "2026.8.3")

    def test_the_tools_own_token_is_never_printed(self):
        # /admin/system/ echoes the request's headers; the mock also repeats the token under a plain key
        for extra in ([], ["--show-secrets"]):
            code, out, err = self.cli("raw", "GET", "/admin/system/", *extra)
            self.assertEqual(code, 0, err)
            self.assertIn("seen", out)
            self.assertNotIn("mock-token", out + err)

    def test_pagination_is_followed(self):
        # the server caps pages at 2 items; there are 5 users and 10 events
        self.assertEqual(len(json.loads(self.cli("users", "list")[1])), 5)
        self.assertEqual(len(json.loads(self.cli("events", "list", "--limit", "7")[1])), 7)

    def test_a_bad_token_is_an_auth_error_although_the_status_is_403(self):
        code, _, err = self.cli("apps", "list", AUTHENTIK_TOKEN="wrong-token")
        self.assertEqual(code, 2)
        self.assertIn("error[auth]", err)
        self.assertNotIn("wrong-token", err)

    def test_a_missing_permission_is_a_permission_error(self):
        code, _, err = self.cli("groups", "add-user", "wiki-users", "carol", "--yes", AUTHENTIK_TOKEN="mock-token-ro")
        self.assertEqual(code, 2)
        self.assertIn("error[permission]", err)

    def test_refused_write_sends_nothing_and_confirmed_write_is_sent(self):
        before = [r for r in self.requests() if r[0] != "GET"]
        self.assertEqual(self.cli("apps", "patch", "dashboards", "--body", '{"policy_engine_mode": "any"}')[0], 3)
        self.assertEqual([r for r in self.requests() if r[0] != "GET"], before)
        code, out, err = self.cli("apps", "patch", "dashboards", "--body", '{"policy_engine_mode": "any"}', "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["result"]["policy_engine_mode"], "any")
        self.assertEqual(self.requests()[-1], ["PATCH", "/api/v3/core/applications/dashboards/", "200"])

    def test_an_empty_204_answer_is_success(self):
        code, out, err = self.cli("groups", "add-user", "wiki-users", "carol", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.requests()[-1][2], "204")

    def test_raw_adds_the_trailing_slash(self):
        code, out, err = self.cli("raw", "GET", "/core/users/me")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["user"]["username"], "svc-automation")

    def test_transactional_create_checks_the_applied_flag(self):
        body = '{"app": {"name": "N", "slug": "n"}, "provider_model": "authentik_providers_oauth2.oauth2provider", "provider": {"name": "N"}}'
        code, out, err = self.cli("apps", "create-with-provider", "--body", body, "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.requests()[-1][:2], ["PUT", "/api/v3/core/transactional/applications/"])

    def test_connection_failures_are_classified(self):
        code, _, err = self.cli("info", AUTHENTIK_HOST="http://127.0.0.1:1")
        self.assertEqual(code, 2)
        self.assertIn("error[refused]", err)
        self.assertIn("error[dns]", self.cli("info", AUTHENTIK_HOST="https://authentik.invalid")[2])

    def test_tls_failure_is_reported_not_bypassed(self):
        code, _, err = self.cli("info", AUTHENTIK_HOST=self.host.replace("http://", "https://"))
        self.assertEqual(code, 2)
        self.assertRegex(err, r"error\[(tls|certificate)\]")


if __name__ == "__main__":
    unittest.main()

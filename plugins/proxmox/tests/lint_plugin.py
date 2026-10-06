#!/usr/bin/env python3
"""Static checks for the proxmox Claude Code plugin (stdlib + optional PyYAML).

Usage: python3 -I tests/lint_plugin.py [--root DIR] [--allow-missing]

Prints PASS/FAIL/SKIP lines and a summary; exits 1 when any check fails.
With --allow-missing, files that do not exist yet produce SKIP instead of FAIL
(used while other parts of the plugin are still being written).
"""

import argparse
import glob
import json
import os
import re
import stat
import sys

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - PyYAML is optional
    yaml = None

PLUGIN_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
ACTION_SKILLS = ("status", "doctor", "vm", "ct", "snapshot", "backup")
# Action skills that take no arguments: they must not carry an argument-hint.
ARGLESS_ACTION_SKILLS = ("doctor",)
# Line budget for the prose doctor skill (four API calls plus tables).
DOCTOR_MAX_LINES = 70
# Only read-only pve-api.sh GET calls may be pre-approved via allowed-tools.
ALLOWED_TOOLS_RE = re.compile(r"^Bash\((\$\{CLAUDE_PLUGIN_ROOT\}|\*)/scripts/pve-api\.sh GET \*\)$")
# References longer than this need a "## Contents" section.
CONTENTS_MIN_LINES = 100
EXPECTED_SCRIPTS = ("pve-api.sh", "pve-task.sh", "pve-ssh.sh", "guard.sh")
REMOVED_SCRIPTS = ("pve-doctor.sh",)
TRIGGER_EVALS = "skills/pve/evals/trigger-evals.json"
TRIGGER_EVALS_COUNT = 20
TRIGGER_EVALS_MIN_EACH = 8
# The dropped doctor script must not be mentioned anywhere the agent or a user reads.
NO_DOCTOR_GLOBS = ("skills/**/*", "agents/*", "README.md", "scripts/*.sh",
                   "tests/run.sh", "tests/guard_cases.txt")
MAIN_SKILL = "pve"
PLUGIN = "proxmox"


class Lint:
    def __init__(self, root, allow_missing):
        self.root = root
        self.allow_missing = allow_missing
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    # -- reporting -------------------------------------------------------
    def ok(self, name):
        self.passed += 1
        print("PASS " + name)

    def fail(self, name, detail=""):
        self.failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))

    def skip(self, name, detail=""):
        self.skipped += 1
        print("SKIP " + name + (": " + detail if detail else ""))

    def check(self, name, cond, detail=""):
        if cond:
            self.ok(name)
        else:
            self.fail(name, detail)
        return bool(cond)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def missing(self, name, rel):
        """Report a missing file; return True when the caller should stop."""
        if self.allow_missing:
            self.skip(name, rel + " not present yet")
        else:
            self.fail(name, rel + " is missing")
        return True

    def read(self, rel):
        with open(self.path(rel), "r", encoding="utf-8") as fh:
            return fh.read()

    def load_json(self, name, rel):
        if not os.path.isfile(self.path(rel)):
            self.missing(name, rel)
            return None
        try:
            data = json.loads(self.read(rel))
        except ValueError as exc:
            self.fail(name, "%s is not valid JSON: %s" % (rel, exc))
            return None
        self.ok(name + ": " + rel + " parses")
        return data

    # -- frontmatter -------------------------------------------------------
    @staticmethod
    def parse_frontmatter(text):
        """Return (dict or None, body). Uses PyYAML when present."""
        if not text.startswith("---"):
            return None, text
        lines = text.split("\n")
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return None, text
        block = "\n".join(lines[1:end])
        body = "\n".join(lines[end + 1:])
        if yaml is not None:
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError:
                return None, body
            return (data if isinstance(data, dict) else None), body
        return Lint.simple_yaml(block), body

    @staticmethod
    def simple_yaml(block):
        """Parse 'key: value' lines, '- item' lists and quoted scalars."""
        data = {}
        key = None
        for raw in block.split("\n"):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            stripped = raw.strip()
            if stripped.startswith("- ") and key is not None:
                if not isinstance(data.get(key), list):
                    data[key] = []
                data[key].append(Lint.scalar(stripped[2:]))
                continue
            if raw.startswith((" ", "\t")) and key is not None:
                data[key] = (str(data.get(key) or "") + " " + stripped).strip()
                continue
            if ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                data[key] = [Lint.scalar(v) for v in value[1:-1].split(",") if v.strip()]
            elif value == "":
                data[key] = None
            else:
                data[key] = Lint.scalar(value)
        return data

    @staticmethod
    def scalar(value):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        return value

    # -- checks -----------------------------------------------------------------
    def check_manifests(self):
        plugin = self.load_json("plugin.json", ".claude-plugin/plugin.json")
        plugin_name = None
        if plugin is not None:
            plugin_name = plugin.get("name")
            self.check("plugin.json: name is kebab-case",
                       isinstance(plugin_name, str) and PLUGIN_NAME_RE.match(plugin_name),
                       repr(plugin_name))
            self.check("plugin.json: name is '%s'" % PLUGIN, plugin_name == PLUGIN, repr(plugin_name))
            self.check("plugin.json: version is a string",
                       isinstance(plugin.get("version"), str), repr(plugin.get("version")))
            self.check("plugin.json: description present",
                       bool(plugin.get("description")))
            for key in ("skills", "agents", "hooks", "commands"):
                self.check("plugin.json: no explicit '%s' key" % key, key not in plugin)
        # The marketplace lives at the repository root, two levels above this plugin.
        market = self.load_json("marketplace.json", "../../.claude-plugin/marketplace.json")
        if market is not None:
            self.check("marketplace.json: owner.name present",
                       isinstance(market.get("owner"), dict) and bool(market["owner"].get("name")))
            self.check("marketplace.json: name present", bool(market.get("name")))
            plugins = market.get("plugins")
            entries = [p for p in plugins if p.get("name") == (plugin_name or PLUGIN)] \
                if isinstance(plugins, list) else []
            if self.check("marketplace.json: exactly one entry named %r" % (plugin_name or PLUGIN),
                          len(entries) == 1):
                entry = entries[0]
                self.check("marketplace.json: source is './plugins/%s'" % PLUGIN,
                           entry.get("source") == "./plugins/" + PLUGIN, repr(entry.get("source")))
                self.check("marketplace.json: entry has no version", "version" not in entry)

    def check_hooks(self):
        name = "hooks.json"
        hooks = self.load_json(name, "hooks/hooks.json")
        if hooks is None:
            return
        pre = (hooks.get("hooks") or {}).get("PreToolUse")
        if not self.check("hooks.json: hooks.PreToolUse is a non-empty list",
                          isinstance(pre, list) and len(pre) > 0):
            return
        self.check("hooks.json: PreToolUse[0].matcher == 'Bash'", pre[0].get("matcher") == "Bash",
                   repr(pre[0].get("matcher")))
        handlers = pre[0].get("hooks") or []
        if not self.check("hooks.json: PreToolUse[0].hooks non-empty", len(handlers) > 0):
            return
        cmd = handlers[0].get("command", "")
        self.check("hooks.json: handler type is command", handlers[0].get("type") == "command")
        self.check("hooks.json: command quotes ${CLAUDE_PLUGIN_ROOT}", '"${CLAUDE_PLUGIN_ROOT}"' in cmd, cmd)
        self.check("hooks.json: command references guard.sh", "scripts/guard.sh" in cmd, cmd)
        rel = cmd.replace('"${CLAUDE_PLUGIN_ROOT}"', "").replace("${CLAUDE_PLUGIN_ROOT}", "").strip('" /')
        script = self.path(rel)
        if os.path.isfile(script):
            self.check("hooks.json: hook script is executable", os.access(script, os.X_OK), rel)
        else:
            self.missing("hooks.json: hook script exists", rel)
        timeout = handlers[0].get("timeout")
        self.check("hooks.json: timeout is a small number",
                   isinstance(timeout, (int, float)) and 0 < timeout <= 60, repr(timeout))

    def check_skills(self):
        skills_dir = self.path("skills")
        found = sorted(glob.glob(os.path.join(skills_dir, "*", "SKILL.md")))
        expected = [MAIN_SKILL] + list(ACTION_SKILLS)
        for name in expected:
            rel = "skills/%s/SKILL.md" % name
            if not os.path.isfile(self.path(rel)):
                self.missing("skill %s" % name, rel)
        for path in found:
            self.check_skill(path)

    def check_skill(self, path):
        dirname = os.path.basename(os.path.dirname(path))
        rel = os.path.relpath(path, self.root)
        label = "skill %s" % dirname
        text = open(path, "r", encoding="utf-8").read()
        fm, body = self.parse_frontmatter(text)
        if not self.check(label + ": frontmatter parses", fm is not None, rel):
            return
        name = fm.get("name")
        self.check(label + ": name == directory", name == dirname, "%r != %r" % (name, dirname))
        self.check(label + ": name matches ^[a-z0-9-]{1,64}$",
                   isinstance(name, str) and SKILL_NAME_RE.match(name), repr(name))
        desc = fm.get("description")
        self.check(label + ": description present", isinstance(desc, str) and desc.strip() != "")
        if isinstance(desc, str):
            self.check(label + ": description <= 1024 chars", len(desc) <= 1024, str(len(desc)))
            self.check(label + ": description has no < or >", "<" not in desc and ">" not in desc)
        nlines = text.count("\n") + (0 if text.endswith("\n") else 1)
        self.check(label + ": <= 500 lines", nlines <= 500, str(nlines))
        self.check(label + ": no !` preprocessing", "!`" not in text)
        if dirname in ACTION_SKILLS:
            self.check(label + ": disable-model-invocation: true",
                       fm.get("disable-model-invocation") is True, repr(fm.get("disable-model-invocation")))
            if dirname in ARGLESS_ACTION_SKILLS:
                self.check(label + ": no argument-hint (takes no arguments)",
                           "argument-hint" not in fm, repr(fm.get("argument-hint")))
            else:
                hint = fm.get("argument-hint")
                self.check(label + ": argument-hint is a non-empty string",
                           isinstance(hint, str) and hint.strip() != "", repr(hint))
        if dirname == "doctor":
            self.check(label + ": <= %d lines" % DOCTOR_MAX_LINES, nlines <= DOCTOR_MAX_LINES, str(nlines))
        if "allowed-tools" in fm:
            tools = fm.get("allowed-tools")
            if isinstance(tools, str):
                tools = [tools]
            if self.check(label + ": allowed-tools is a string or list of strings",
                          isinstance(tools, list) and tools and all(isinstance(t, str) for t in tools),
                          repr(fm.get("allowed-tools"))):
                for t in tools:
                    self.check(label + ": allowed-tools entry is a pve-api.sh GET rule",
                               bool(ALLOWED_TOOLS_RE.match(t.strip())), repr(t))
        for ref in sorted(set(re.findall(r"references/([A-Za-z0-9_.-]+\.md)", body))):
            target = os.path.join(os.path.dirname(path), "references", ref)
            if os.path.isfile(target):
                self.ok(label + ": references/%s exists" % ref)
            elif self.allow_missing:
                self.skip(label + ": references/%s exists" % ref, "not present yet")
            else:
                self.fail(label + ": references/%s exists" % ref, target)
        for script in sorted(set(re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([A-Za-z0-9_.-]+)", body))):
            target = self.path("scripts", script)
            if os.path.isfile(target):
                self.check(label + ": scripts/%s executable" % script, os.access(target, os.X_OK))
            elif self.allow_missing:
                self.skip(label + ": scripts/%s exists" % script, "not present yet")
            else:
                self.fail(label + ": scripts/%s exists" % script)

    def check_agents(self):
        agents = sorted(glob.glob(self.path("agents", "*.md")))
        if not agents:
            self.missing("agents", "agents/*.md")
            return
        for path in agents:
            rel = os.path.relpath(path, self.root)
            label = "agent %s" % os.path.basename(path)
            fm, body = self.parse_frontmatter(open(path, "r", encoding="utf-8").read())
            if not self.check(label + ": frontmatter parses", fm is not None, rel):
                continue
            name = fm.get("name")
            self.check(label + ": name present without ':'",
                       isinstance(name, str) and name and ":" not in name, repr(name))
            self.check(label + ": name == file stem",
                       name == os.path.splitext(os.path.basename(path))[0], repr(name))
            self.check(label + ": description present", bool(fm.get("description")))
            self.check(label + ": tools present", bool(fm.get("tools")))
            for key in ("permissionMode", "hooks", "mcpServers", "initialPrompt"):
                self.check(label + ": no '%s' key" % key, key not in fm)
            skills = fm.get("skills")
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            if skills:
                for sk in skills:
                    bare = sk.split(":", 1)[1] if sk.startswith(PLUGIN + ":") else sk
                    self.check(label + ": skill %s resolves" % sk,
                               os.path.isfile(self.path("skills", bare, "SKILL.md")))
            self.check(label + ": body not empty", body.strip() != "")

    def check_scripts(self):
        scripts = sorted(glob.glob(self.path("scripts", "*.sh")))
        for name in EXPECTED_SCRIPTS:
            if not os.path.isfile(self.path("scripts", name)):
                self.missing("script %s" % name, "scripts/" + name)
        for name in REMOVED_SCRIPTS:
            self.check("script %s does not exist" % name,
                       not os.path.exists(self.path("scripts", name)), "scripts/" + name)
        for path in scripts:
            label = "script %s" % os.path.basename(path)
            mode = os.stat(path).st_mode
            self.check(label + ": executable", bool(mode & stat.S_IXUSR))
            text = open(path, "r", encoding="utf-8").read()
            lines = text.split("\n")
            self.check(label + ": bash shebang", lines[0] == "#!/usr/bin/env bash", lines[0])
            self.check(label + ": set -euo pipefail", "set -euo pipefail" in text)
            code_lines = [l for l in lines if not l.lstrip().startswith("#")]
            bad = [l for l in code_lines if re.search(r"\b(python[0-9.]*|perl)\b", l)]
            self.check(label + ": no python/perl invocation", not bad, bad[0].strip() if bad else "")
            self.check(label + ": no eval", not any(re.search(r"\beval\b", l) for l in code_lines))
            self.check(label + ": ASCII only", all(ord(c) < 128 for c in text))

    def check_evals(self):
        rel = "skills/pve/evals/evals.json"
        data = self.load_json("evals.json", rel)
        if data is None:
            return
        self.check("evals.json: skill_name == 'pve'", data.get("skill_name") == MAIN_SKILL)
        evals = data.get("evals")
        if not self.check("evals.json: evals is a non-empty list", isinstance(evals, list) and evals):
            return
        ids = [e.get("id") for e in evals]
        self.check("evals.json: ids are unique ints",
                   all(isinstance(i, int) for i in ids) and len(set(ids)) == len(ids), repr(ids))
        for e in evals:
            label = "evals.json: eval %s" % e.get("id")
            self.check(label + " has prompt", bool(e.get("prompt")))
            self.check(label + " has expected_output", bool(e.get("expected_output")))
            self.check(label + " has expectations",
                       isinstance(e.get("expectations"), list) and len(e["expectations"]) > 0)
            self.check(label + " has files list", isinstance(e.get("files"), list))

    def check_trigger_evals(self):
        data = self.load_json("trigger-evals.json", TRIGGER_EVALS)
        if data is None:
            return
        if not self.check("trigger-evals.json: list of exactly %d entries" % TRIGGER_EVALS_COUNT,
                          isinstance(data, list) and len(data) == TRIGGER_EVALS_COUNT,
                          str(len(data)) if isinstance(data, list) else type(data).__name__):
            return
        shape_ok = True
        for i, e in enumerate(data):
            if not (isinstance(e, dict)
                    and isinstance(e.get("query"), str) and e["query"].strip() != ""
                    and isinstance(e.get("should_trigger"), bool)):
                shape_ok = False
                self.fail("trigger-evals.json: entry %d has query (non-empty str) and should_trigger (bool)" % i,
                          repr(e)[:120])
        if shape_ok:
            self.ok("trigger-evals.json: every entry has query and should_trigger")
            positives = sum(1 for e in data if e["should_trigger"] is True)
            negatives = len(data) - positives
            self.check("trigger-evals.json: >= %d should_trigger true" % TRIGGER_EVALS_MIN_EACH,
                       positives >= TRIGGER_EVALS_MIN_EACH, str(positives))
            self.check("trigger-evals.json: >= %d should_trigger false" % TRIGGER_EVALS_MIN_EACH,
                       negatives >= TRIGGER_EVALS_MIN_EACH, str(negatives))

    def check_references(self):
        refs = sorted(glob.glob(self.path("skills", "*", "references", "*.md")))
        if not refs:
            self.missing("references", "skills/*/references/*.md")
            return
        for path in refs:
            rel = os.path.relpath(path, self.root)
            text = open(path, "r", encoding="utf-8").read()
            nlines = text.count("\n") + (0 if text.endswith("\n") else 1)
            if nlines > CONTENTS_MIN_LINES:
                self.check("reference %s: has '## Contents' (%d lines)" % (rel, nlines),
                           re.search(r"(?m)^## Contents\s*$", text) is not None)

    def check_no_doctor_script_mentions(self):
        hits = []
        for pattern in NO_DOCTOR_GLOBS:
            for path in sorted(glob.glob(self.path(pattern), recursive=True)):
                if not os.path.isfile(path):
                    continue
                try:
                    text = open(path, "r", encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                if "pve-doctor" in text:
                    hits.append(os.path.relpath(path, self.root))
        self.check("no 'pve-doctor' mention in skills, agents, README, scripts or tests",
                   not hits, " ".join(hits))

    def check_docs(self):
        self.check("no root CLAUDE.md", not os.path.exists(self.path("CLAUDE.md")))
        if os.path.isfile(self.path("README.md")):
            self.ok("README.md exists")
        else:
            self.missing("README.md exists", "README.md")

    def run(self):
        self.check_manifests()
        self.check_hooks()
        self.check_skills()
        self.check_references()
        self.check_agents()
        self.check_scripts()
        self.check_evals()
        self.check_trigger_evals()
        self.check_docs()
        self.check_no_doctor_script_mentions()
        print("lint summary: %d passed, %d failed, %d skipped" % (self.passed, self.failed, self.skipped))
        return 1 if self.failed else 0


def main():
    parser = argparse.ArgumentParser(description="Lint the proxmox plugin layout")
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--root", default=default_root, help="plugin root (default: repo root)")
    parser.add_argument("--allow-missing", action="store_true",
                        help="report missing files as SKIP instead of FAIL")
    args = parser.parse_args()
    if yaml is None:
        print("SKIP PyYAML not installed, using the simple frontmatter parser")
    sys.exit(Lint(os.path.abspath(args.root), args.allow_missing).run())


if __name__ == "__main__":
    main()

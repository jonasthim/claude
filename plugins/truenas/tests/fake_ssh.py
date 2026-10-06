#!/usr/bin/env python3
"""Stand-in for `ssh <target> midclt call ...`, for offline tests of tn.py's ssh transport.

Installed as an executable named `ssh` on PATH by tests/test_e2e.py. It parses the ssh argv,
pulls the remote command apart, and answers like midclt would: bare ints and strings on
stdout, JSON for everything else, errors and job descriptions on stderr, exit 1 on failure,
exit 255 when "ssh" cannot connect. Set FAKE_SSH_LOG to a path to record each argv.
"""
import json
import os
import shlex
import sys

JOB = {"id": 7, "method": "pool.scrub.run", "state": "SUCCESS", "progress": {"percent": 100, "description": "done"},
       "result": "scrub started", "error": None, "time_started": {"$date": 0}, "time_finished": {"$date": 1}}
METHODS = {
    "pool.dataset.create": {"description": "Create a dataset.\n\nLong text here.", "job": False,
                            "accepts": [{"type": "object"}]},
    "pool.dataset.delete": {"description": "Delete a dataset.", "job": True},
    "pool.scrub.run": {"description": "Start a scrub.", "job": True},
}


def main(argv):
    if os.environ.get("FAKE_SSH_LOG"):
        with open(os.environ["FAKE_SSH_LOG"], "a") as f:
            f.write(json.dumps(argv) + "\n")
    rest = list(argv)
    target = None
    while rest:
        a = rest.pop(0)
        if a in ("-o", "-J", "-i", "-p", "-l", "-F"):
            rest.pop(0)
            continue
        if a.startswith("-"):
            continue
        target = a
        break
    remote = shlex.split(" ".join(rest))
    if target is None or not remote:
        print("usage: ssh [opts] target command", file=sys.stderr)
        return 255
    if target.endswith("@unreachable") or target == "unreachable":
        print("ssh: connect to host unreachable port 22: No route to host", file=sys.stderr)
        return 255
    user = target.split("@")[0] if "@" in target else "root"
    sudo = False
    if remote[:2] == ["sudo", "-n"]:
        sudo, remote = True, remote[2:]
        if user == "nosudo":
            print("sudo: a password is required", file=sys.stderr)
            return 1
    if remote[:2] != ["midclt", "call"]:
        print(f"bash: {remote[0]}: command not found", file=sys.stderr)
        return 127
    remote = remote[2:]
    job = False
    if remote and remote[0] == "-j":
        job, remote = True, remote[1:]
    method, args = remote[0], []
    for a in remote[1:]:
        try:
            args.append(json.loads(a))
        except json.JSONDecodeError:
            args.append(a)

    def out(v):
        print(v if isinstance(v, (int, str)) and not isinstance(v, bool) else json.dumps(v))
        return 0

    if method == "core.ping":
        return out("pong")
    if method == "system.info":
        return out({"hostname": "fakenas-ssh", "version": "25.10.6", "system_product": "Fake", "uptime": "2 days",
                    "cores": 4, "physmem": 16 * 2**30, "loadavg": [0.5, 0.4, 0.3]})
    if method == "alert.list":
        return out([{"uuid": "a1", "level": "CRITICAL", "formatted": "Pool tank is DEGRADED", "dismissed": False}])
    if method == "core.get_jobs":
        filters = args[0] if args else []
        if filters and filters[0][0] == "id":
            return out([JOB] if filters[0][2] == 7 else [])
        return out([])
    if method == "core.get_methods":
        return out(METHODS)
    if method == "pool.dataset.query":
        return out({"echo": {"filters": args[0] if args else [], "options": args[1] if len(args) > 1 else {},
                             "sudo": sudo}})
    if method == "pool.scrub.run":
        if job:
            print("starting", file=sys.stderr)
            print("scrubbing", file=sys.stderr)
            return out("scrub started")
        return out(7)
    if method == "pool.dataset.create":
        print("[EEXIST] pool_dataset_create.name: Dataset 'bad' already exists", file=sys.stderr)
        return 1
    if method == "pool.dataset.delete":
        print("Dataset is busy", file=sys.stderr)
        print("Traceback (most recent call last):\n  File \"x\", line 1\nCallError: [EBUSY] Dataset is busy",
              file=sys.stderr)
        return 1
    print(f"Method {method} not found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

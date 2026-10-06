#!/usr/bin/env python3
"""A tiny stand-in for the TrueNAS middleware JSON-RPC websocket API, for offline tests.

Usage: python fake_middleware.py PORT   (needs the `websockets` package)

It speaks just enough of the protocol for tn.py: core.set_options, API-key login, a few read
methods, a legacy-style job (pool.scrub.run) that streams core.get_jobs events, a validation
error, and a CallError. GET /api/versions is served over plain HTTP like the real box.
"""
import asyncio
import http
import json
import sys

import websockets

GOOD_KEY = "good-key"
JOB = {"id": 7, "method": "pool.scrub.run", "arguments": ["tank"], "state": "RUNNING",
       "progress": {"percent": 0, "description": "starting"}, "result": None, "error": None,
       "exception": None, "exc_info": None, "time_started": {"$date": 0}, "time_finished": None}

METHODS = {
    "pool.dataset.create": {"description": "Create a dataset.\n\nLong text here.", "job": False,
                            "accepts": [{"type": "object", "properties": {"name": {"type": "string"}}}]},
    "pool.dataset.delete": {"description": "Delete a dataset.", "job": True, "accepts": []},
    "pool.dataset.query": {"description": "Query datasets.", "filterable": True},
    "pool.scrub.run": {"description": "Start a scrub.", "job": True},
}


async def handler(ws):
    authed = False
    async for raw in ws:
        msg = json.loads(raw)
        method, params, mid = msg["method"], msg.get("params", []), msg.get("id")

        def reply(result):
            return ws.send(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}))

        def error(code, message, data=None):
            return ws.send(json.dumps({"jsonrpc": "2.0", "id": mid,
                                       "error": {"code": code, "message": message, "data": data}}))

        if method == "core.set_options":
            await reply(None)
        elif method == "auth.login_with_api_key":
            authed = params[0] == GOOD_KEY
            await reply(authed)
        elif method == "auth.login_ex":
            p = params[0]
            authed = p.get("mechanism") == "API_KEY_PLAIN" and p.get("api_key") == GOOD_KEY and p.get("username") == "admin"
            await reply({"response_type": "SUCCESS" if authed else "AUTH_ERR"})
        elif not authed:
            await error(-32001, "Not authenticated", {"error": 1, "errname": "EPERM", "reason": "Not authenticated",
                                                      "trace": None, "extra": None})
        elif method == "system.info":
            await reply({"hostname": "fakenas", "version": "25.10.0", "system_product": "Fake",
                         "uptime": "1 day", "cores": 8, "physmem": 32 * 2**30, "loadavg": [0.1, 0.2, 0.3]})
        elif method == "alert.list":
            await reply([{"uuid": "a1", "level": "WARNING", "formatted": "Pool tank is 85% full", "dismissed": False},
                         {"uuid": "a2", "level": "INFO", "formatted": "old", "dismissed": True}])
        elif method == "core.get_jobs":
            filters = params[0] if params else []
            if filters and filters[0][0] == "id":
                await reply([dict(JOB, state="SUCCESS", result="scrub started")] if filters[0][2] == 7 else [])
            else:
                await reply([])
        elif method == "core.subscribe":
            await reply("sub-1")
        elif method == "core.get_methods":
            await reply(METHODS)
        elif method == "pool.dataset.query":
            await reply({"echo": {"filters": params[0] if params else [], "options": params[1] if len(params) > 1 else {}}})
        elif method == "pool.scrub.run":
            await reply(JOB["id"])
            for msg_type, fields in [
                ("added", dict(JOB)),
                ("changed", dict(JOB, progress={"percent": 50, "description": "scrubbing"})),
                ("changed", dict(JOB, state="SUCCESS", progress={"percent": 100, "description": "done"},
                                 result="scrub started", time_finished={"$date": 1})),
            ]:
                await asyncio.sleep(0.05)
                await ws.send(json.dumps({"jsonrpc": "2.0", "method": "collection_update",
                                          "params": {"msg": msg_type, "collection": "core.get_jobs",
                                                     "id": JOB["id"], "fields": fields}}))
        elif method == "cloudsync.credentials.query":
            await reply([{"id": 1, "name": "b2", "provider": {"type": "B2", "account": "acct",
                                                               "key": "k3y-material", "endpoint": ""},
                          "attributes": {"type": "S3", "access_key_id": "AKIA", "secret_access_key": "sekrit"}}])
        elif method == "app.config":
            await reply({"network": {"web_port": 8096}, "jellyfin": {"admin_password": "hunter2", "name": "jf"}})
        elif method == "pool.dataset.export_key":
            await reply("0123456789abcdef")
        elif method == "test.big":
            await reply(list(range(params[0])))
        elif method == "test.failing_job":
            await reply(8)
            await asyncio.sleep(0.05)
            await ws.send(json.dumps({"jsonrpc": "2.0", "method": "collection_update",
                                      "params": {"msg": "changed", "collection": "core.get_jobs", "id": 8,
                                                 "fields": {"id": 8, "state": "FAILED", "error": "[EIO] disk on fire",
                                                            "exception": "Traceback...\nCallError: [EIO] disk on fire\n",
                                                            "exc_info": {"type": "CallError", "errno": 5, "extra": None},
                                                            "progress": {"percent": 10, "description": "x"}}}}))
        elif method == "pool.dataset.create":
            await error(-32602, "Invalid params", {"error": 22, "errname": "EINVAL", "reason": "validation",
                        "trace": None, "extra": [["pool_dataset_create.name", "Dataset 'bad' already exists", 17]]})
        elif method == "pool.dataset.delete":
            await error(-32001, "CallError", {"error": 16, "errname": "EBUSY", "reason": "Dataset is busy",
                        "trace": {"class": "CallError", "frames": [], "formatted": "Traceback...\nCallError: [EBUSY] Dataset is busy",
                                  "repr": "CallError(EBUSY, 'Dataset is busy')"}, "extra": None})
        else:
            await error(-32601, "Method not found", {"error": 201, "errname": "ENOMETHOD",
                        "reason": f"Method {method} not found", "trace": None, "extra": None})


def process_request(connection, request):
    if request.path == "/api/versions":
        return connection.respond(http.HTTPStatus.OK, json.dumps(["v25.04.0", "v25.10.0"]))
    return None


async def main(port):
    async with websockets.serve(handler, "127.0.0.1", port, process_request=process_request):
        print("ready", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))

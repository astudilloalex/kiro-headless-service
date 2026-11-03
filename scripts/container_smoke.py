"""Exercise a running instance in explicitly simulated mode, using only the standard library."""

import json
import os
import time
import urllib.error
import urllib.request

base = os.environ.get("SERVICE_URL", "http://127.0.0.1:8000")
token = os.environ["SERVICE_API_TOKEN"]


def call(path, body=None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


deadline = time.monotonic() + 120
while True:
    try:
        call("/health/ready")
        break
    except (urllib.error.URLError, TimeoutError):
        if time.monotonic() > deadline:
            raise TimeoutError("Instance did not become ready") from None
        time.sleep(1)
assert call("/v1/capabilities")["engine"] == "simulated"
job = call("/v1/jobs", {"input": "container smoke"})
while job["state"] in {"QUEUED", "RUNNING"}:
    if time.monotonic() > deadline:
        raise TimeoutError("Job did not finish")
    time.sleep(0.25)
    job = call("/v1/jobs/" + job["id"])
assert job["state"] == "SUCCEEDED", job
assert call(f"/v1/jobs/{job['id']}/result")["text"] == "container smoke"
assert call("/v1/capabilities")["cli_version"] == "2.37.1"
assert call("/v1/capabilities")["agent_engine"] == "v3"
# Exercise both mounted native formats through the same job queue without provider inference.
assert {agent["id"] for agent in call("/v1/agents")} >= {"concise", "reviewer"}
for agent in ("concise", "reviewer"):
    job = call("/v1/jobs", {"input": "native config smoke", "agent": agent})
    while job["state"] in {"QUEUED", "RUNNING"}:
        if time.monotonic() > deadline:
            raise TimeoutError("Native-agent job did not finish")
        time.sleep(0.25)
        job = call("/v1/jobs/" + job["id"])
    assert job["state"] == "SUCCEEDED", job
print("API/worker/PostgreSQL/native-config smoke passed (explicitly simulated inference)")

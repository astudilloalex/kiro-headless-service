"""Exercise a running instance in explicitly simulated mode, using only the standard library."""

import json
import os
import time
import urllib.error
import urllib.request


def call(base, token, path, body=None, *, timeout=5):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def wait_for_ready(base, token, *, timeout=120):
    deadline = time.monotonic() + timeout
    last_failure = "no readiness response"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Instance did not become ready ({last_failure})") from None
        try:
            call(base, token, "/health/ready", timeout=min(5, remaining))
            return
        except urllib.error.HTTPError as exc:
            # Readiness is 503 until the database and matching worker are ready.
            # Authentication, routing and application errors must fail immediately.
            if exc.code != 503:
                raise
            last_failure = "HTTP 503"
            exc.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            # A published port can accept connections before the API is listening,
            # including after a container restart. Retry only this read-only probe.
            last_failure = type(exc).__name__
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def main():
    base = os.environ.get("SERVICE_URL", "http://127.0.0.1:8000")
    token = os.environ["SERVICE_API_TOKEN"]
    wait_for_ready(base, token)
    capabilities = call(base, token, "/v1/capabilities")
    assert capabilities["engine"] == "simulated"
    assert capabilities["cli_version"] == "2.37.1"
    assert capabilities["agent_engine"] == "v3"
    assert {agent["id"] for agent in call(base, token, "/v1/agents")} >= {
        "concise",
        "reviewer",
    }
    # Exercise no agent and both mounted native formats without provider inference.
    for agent in (None, "concise", "reviewer"):
        request = {"input": "container smoke"}
        if agent:
            request["agent"] = agent
        job = call(base, token, "/v1/jobs", request)
        deadline = time.monotonic() + 120
        while job["state"] in {"QUEUED", "RUNNING"}:
            if time.monotonic() > deadline:
                raise TimeoutError("Job did not finish")
            time.sleep(0.25)
            job = call(base, token, "/v1/jobs/" + job["id"])
        assert job["state"] == "SUCCEEDED", job
        assert call(base, token, f"/v1/jobs/{job['id']}/result")["text"] == "container smoke"
    print("API/worker/PostgreSQL/native-config smoke passed (explicitly simulated inference)")


if __name__ == "__main__":
    main()

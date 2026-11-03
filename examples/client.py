"""Minimal authenticated consumer; run with SERVICE_API_TOKEN and optionally SERVICE_URL."""

import os
import time

import httpx

with httpx.Client(
    base_url=os.environ.get("SERVICE_URL", "http://localhost:8000"),
    headers={"Authorization": "Bearer " + os.environ["SERVICE_API_TOKEN"]},
    timeout=10,
) as client:
    accepted = client.post(
        "/v1/jobs",
        json={"input": "Explain what an HTTP API does."},
        headers={"Idempotency-Key": "python-example-1"},
    )
    accepted.raise_for_status()
    job = accepted.json()
    deadline = time.monotonic() + 360
    while job["state"] in {"QUEUED", "RUNNING"}:
        if time.monotonic() > deadline:
            raise TimeoutError("Polling stopped; durable job continues")
        time.sleep(1)
        response = client.get("/v1/jobs/" + job["id"])
        response.raise_for_status()
        job = response.json()
    if job["state"] != "SUCCEEDED":
        raise RuntimeError(f"Job {job['state']}: {job['error']}")
    result = client.get(f"/v1/jobs/{job['id']}/result")
    result.raise_for_status()
    print(result.json())

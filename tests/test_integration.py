import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from kiro_headless_service.api.app import create_app
from kiro_headless_service.application.contracts import JobRequest
from kiro_headless_service.application.service import JobService
from kiro_headless_service.application.worker import Dispatcher, Runner
from kiro_headless_service.domain.model import (
    Artifact,
    RuntimeFailure,
    RuntimeResult,
    ServiceError,
    State,
)
from kiro_headless_service.infrastructure.config import Policy
from kiro_headless_service.infrastructure.runtime import SimulatedRuntime
from kiro_headless_service.infrastructure.store import Store
from kiro_headless_service.infrastructure.tables import ArtifactRow, Job, Outbox

pytestmark = pytest.mark.integration
TOKEN = "a-test-token-at-least-24-characters"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def submit(store, settings, value="hello", key=None, session=None, **kwargs):
    policy = Policy(settings)
    return JobService(store, policy).submit(
        policy.authenticate(TOKEN),
        JobRequest(input=value, **kwargs),
        key,
        "test-correlation",
        session,
    )


def test_api_auth_owner_and_openapi(settings, store):
    client = TestClient(create_app(settings, store))
    assert client.post("/v1/jobs", json={"input": "hello"}).status_code == 401
    accepted = client.post("/v1/jobs", json={"input": "hello"}, headers=HEADERS)
    assert accepted.status_code == 202
    job = accepted.json()["id"]
    bob = {"Authorization": "Bearer b-test-token-at-least-24-characters"}
    for suffix in ["", "/result", "/events", "/artifacts"]:
        assert client.get(f"/v1/jobs/{job}{suffix}", headers=bob).status_code == 404
    assert client.post(f"/v1/jobs/{job}/cancel", headers=bob).status_code == 404
    assert client.get("/docs").status_code == 404
    schema = client.get("/openapi.json").json()
    assert "/v1/jobs/{jobId}/result" in schema["paths"]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]
    assert client.get("/health/ready").status_code == 503
    store.heartbeat("runner:test", settings.fingerprint())
    store.heartbeat("dispatcher:test", settings.fingerprint())
    assert client.get("/health/ready").status_code == 200
    invalid = client.post("/v1/jobs", json={"input": "x", "unexpected": 1}, headers=HEADERS)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["correlation_id"] == invalid.headers["x-correlation-id"]
    assert client.post("/v1/jobs", content=b"x" * 262145, headers=HEADERS).status_code == 413


def test_idempotency_and_parallel_acceptance(settings, store):
    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = list(executor.map(lambda _: submit(store, settings, key="same"), range(8)))
    assert len({job["id"] for job in jobs}) == 1
    with pytest.raises(ServiceError, match="different input") as error:
        submit(store, settings, "different", key="same")
    assert error.value.status == 409
    who = Policy(settings).authenticate("b-test-token-at-least-24-characters")
    other = JobService(store, Policy(settings)).submit(
        who, JobRequest(input="different"), "same", "c"
    )
    assert other["id"] != jobs[0]["id"]


@pytest.mark.parametrize(
    "value,fmt,state",
    [
        ("plain answer", None, "SUCCEEDED"),
        (
            '{"answer":42}',
            {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "integer"}},
                    "additionalProperties": False,
                },
            },
            "SUCCEEDED",
        ),
        (
            '{"answer":"wrong"}',
            {
                "type": "json_schema",
                "schema": {"type": "object", "properties": {"answer": {"type": "integer"}}},
            },
            "FAILED",
        ),
        ("not json", {"type": "json"}, "FAILED"),
    ],
)
async def test_job_results(settings, store, value, fmt, state):
    job = submit(store, settings, value, response_format=fmt)
    assert await Runner(store, SimulatedRuntime(), 6).once()
    assert store.job("alice", job["id"])["state"] == state
    if state == "SUCCEEDED":
        assert store.result("alice", job["id"])["format"] == (fmt or {"type": "text"})["type"]
    else:
        assert store.job("alice", job["id"])["error"] == "INVALID_RESULT"


def test_shared_concurrency_and_fencing(settings, store):
    settings.max_running = 2
    jobs = [submit(store, settings) for _ in range(3)]
    second = Store(settings)
    with ThreadPoolExecutor(max_workers=6) as executor:
        claims = list(executor.map(lambda i: (store if i % 2 else second).claim(str(i)), range(6)))
    claimed = [c for c in claims if c]
    assert len(claimed) == 1  # consumer max_running=1, enforced across connections
    old = claimed[0]
    with store.db.begin() as db:
        db.execute(
            update(Job)
            .where(Job.id == old["id"])
            .values(lease_until=func.now() - timedelta(seconds=1))
        )
    assert not second.finish(old["id"], old["token"], State.SUCCEEDED, {"text": "stale"}, [])
    next_job = second.claim("replacement")
    assert next_job and next_job["id"] != old["id"]
    assert store.job("alice", old["id"])["error"] == "EXECUTION_AMBIGUOUS_WORKER_LOST"
    assert store.job("alice", old["id"])["attempts"] == 1
    assert not store.renew(old["id"], old["token"])
    assert len(jobs) == 3
    second.engine.dispose()


def test_global_concurrency_and_queue_quotas(settings, store):
    settings.max_running, settings.max_queued = 1, 2
    submit(store, settings)
    who = Policy(settings).authenticate("b-test-token-at-least-24-characters")
    service = JobService(store, Policy(settings))
    service.submit(who, JobRequest(input="hello"), None, "c")
    with pytest.raises(ServiceError) as error:
        submit(store, settings)
    assert error.value.status == 429
    assert store.claim("one")
    assert store.claim("two") is None


async def test_cancel_pending_and_running(settings, store):
    pending = submit(store, settings)
    assert store.cancel("alice", pending["id"])["state"] == "CANCELLED"
    assert store.claim("worker") is None
    started, stopped = asyncio.Event(), asyncio.Event()

    class Blocking:
        async def execute(self, snapshot, prompt, emit):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                stopped.set()

    job = submit(store, settings)
    task = asyncio.create_task(Runner(store, Blocking(), 6).once())
    await asyncio.wait_for(started.wait(), timeout=5)
    store.cancel("alice", job["id"])
    await asyncio.wait_for(task, timeout=5)
    assert stopped.is_set()
    assert store.job("alice", job["id"])["state"] == "CANCELLED"


async def test_timeout_and_safe_retry_limit(settings, store):
    class PrelaunchFailure:
        async def execute(self, snapshot, prompt, emit):
            raise RuntimeFailure("RUNTIME_LAUNCH_FAILED", safe_to_retry=True)

    job = submit(store, settings)
    runner = Runner(store, PrelaunchFailure(), 6)
    assert await runner.once()
    assert store.job("alice", job["id"])["state"] == "QUEUED"
    with store.db.begin() as db:
        db.execute(update(Job).values(available_at=func.now()))
    assert await runner.once()
    assert store.job("alice", job["id"])["state"] == "FAILED"
    assert store.job("alice", job["id"])["attempts"] == 2

    class TimedOut:
        async def execute(self, snapshot, prompt, emit):
            raise RuntimeFailure("EXECUTION_TIMED_OUT")

    job = submit(store, settings)
    await Runner(store, TimedOut(), 6).once()
    assert store.job("alice", job["id"])["state"] == "TIMED_OUT"


async def test_persistent_chat_order_restart_failures_and_exclusion(settings, store):
    session = store.create_session("alice", {}, "session-key")
    assert store.create_session("alice", {}, "session-key")["id"] == session["id"]
    job = submit(store, settings, "first", session=session["id"], key="turn")
    assert submit(store, settings, "first", session=session["id"], key="turn")["id"] == job["id"]
    with pytest.raises(ServiceError):
        submit(store, settings, "concurrent", session=session["id"])
    with pytest.raises(ServiceError):
        store.session("bob", session["id"])
    await Runner(store, SimulatedRuntime(), 6).once()
    restarted = Store(settings)
    seen = []

    class InspectHistory:
        async def execute(self, snapshot, prompt, emit):
            seen.append(prompt)
            return RuntimeResult("second answer")

    submit(restarted, settings, "second", session=session["id"])
    await Runner(restarted, InspectHistory(), 6).once()
    assert '"role": "user", "content": "first"' in seen[0]
    assert '"role": "assistant", "content": "first"' in seen[0]
    failed = submit(
        restarted, settings, "failed", session=session["id"], response_format={"type": "json"}
    )
    await Runner(restarted, SimulatedRuntime(), 6).once()
    assert store.job("alice", failed["id"])["state"] == "FAILED"
    assert len(restarted.history(session["id"])) == 4
    history = store.messages("alice", session["id"], 0, 100)
    assert [m["seq"] for m in history] == [1, 2, 3, 4, 5]
    assert history[-1]["status"] == "FAILED"
    assert len(store.messages("alice", session["id"], 2, 2)) == 2
    store.close_session("alice", session["id"])
    with pytest.raises(ServiceError):
        submit(store, settings, "after close", session=session["id"])
    restarted.engine.dispose()


async def test_context_limit_is_explicit(settings, store):
    settings.max_history_messages = 2
    session = store.create_session("alice", {}, None)
    submit(store, settings, session=session["id"])
    await Runner(store, SimulatedRuntime(), 6).once()
    with pytest.raises(ServiceError) as error:
        submit(store, settings, session=session["id"])
    assert error.value.code == "CONTEXT_LIMIT"


async def test_atomic_result_artifacts_outbox_download_and_retry(settings, store):
    class WithFile:
        async def execute(self, snapshot, prompt, emit):
            return RuntimeResult("done", artifacts=[Artifact("report.txt", b"report")])

    job = submit(store, settings, callback="https://hooks.example.com/job")
    await Runner(store, WithFile(), 6).once()
    with store.db() as db:
        assert db.scalar(select(func.count()).select_from(Outbox)) == 1
        assert db.scalar(select(func.count()).select_from(ArtifactRow)) == 1
    client = TestClient(create_app(settings, store))
    artifacts = client.get(f"/v1/jobs/{job['id']}/artifacts", headers=HEADERS).json()
    item = artifacts[0]
    url = f"/v1/jobs/{job['id']}/artifacts/{item['id']}"
    assert client.get(url, headers=HEADERS).content == b"report"
    assert (
        client.get(
            url, headers={"Authorization": "Bearer b-test-token-at-least-24-characters"}
        ).status_code
        == 404
    )
    delivered = []

    class FlakyReceiver:
        async def deliver(self, url, event):
            delivered.append(event)
            if len(delivered) == 1:
                raise ValueError("temporary failure")

    dispatcher = Dispatcher(store, FlakyReceiver())
    await dispatcher.once()
    with store.db.begin() as db:
        db.execute(update(Outbox).values(available_at=func.now()))
    await dispatcher.once()
    assert len(delivered) == 2 and delivered[0]["id"] == delivered[1]["id"]
    assert "result" not in delivered[0]
    assert await dispatcher.once() is False


def test_transaction_rolls_back_all_outputs(settings, store):
    job = submit(store, settings, callback="https://hooks.example.com/job")
    claim = store.claim("worker")
    with pytest.raises(IntegrityError):
        store.finish(
            job["id"], claim["token"], State.SUCCEEDED, {"text": "done"}, [Artifact(None, b"x")]
        )
    assert store.job("alice", job["id"])["state"] == "RUNNING"
    with store.db() as db:
        assert db.scalar(select(func.count()).select_from(Outbox)) == 0
        assert db.scalar(select(func.count()).select_from(ArtifactRow)) == 0


async def test_sse_resume_and_retention(settings, store):
    job = submit(store, settings)
    await Runner(store, SimulatedRuntime(), 6).once()
    client = TestClient(create_app(settings, store))
    url = f"/v1/jobs/{job['id']}/events"
    first = client.get(url + "?limit=1", headers=HEADERS).json()
    cursor = first["next_cursor"]
    with client.stream(
        "GET", url + "?stream=true", headers={**HEADERS, "Last-Event-ID": str(cursor)}
    ) as stream:
        body = "".join(stream.iter_text())
        assert "event: completed" in body and f"id: {cursor}\n" not in body
    with store.db.begin() as db:
        db.execute(update(Job).values(updated_at=func.now() - timedelta(days=30)))
    assert store.cleanup() == 1
    assert client.get(url, headers=HEADERS).status_code == 404


def test_outbox_crashes_are_bounded_and_fenced(settings, store):
    settings.notification_attempts = 2
    job = submit(store, settings, callback="https://hooks.example.com/job")
    claim = store.claim("runner")
    store.finish(job["id"], claim["token"], State.SUCCEEDED, {"text": "done"}, [])
    first = store.claim_notification()
    with store.db.begin() as db:
        db.execute(update(Outbox).values(lease_until=func.now() - timedelta(seconds=1)))
    second = store.claim_notification()
    assert first["id"] == second["id"] and first["token"] != second["token"]
    store.settle_notification(first["id"], first["token"], True)
    with store.db() as db:
        assert db.get(Outbox, first["id"]).delivered is False
    with store.db.begin() as db:
        db.execute(update(Outbox).values(lease_until=func.now() - timedelta(seconds=1)))
    assert store.claim_notification() is None
    with store.db() as db:
        assert db.get(Outbox, first["id"]).exhausted is True


def test_session_parallel_submission_and_readiness_roles(settings, store):
    session = store.create_session("alice", {}, None)

    def accept(index):
        try:
            return submit(store, settings, str(index), session=session["id"])["id"]
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(accept, range(6)))
    assert results.count("SESSION_CONFLICT") == 5
    store.heartbeat("runner:test", settings.fingerprint())
    assert store.ready(settings.fingerprint()) is False
    store.heartbeat("dispatcher:test", settings.fingerprint())
    assert store.ready(settings.fingerprint()) is True
    store.heartbeat("runner:incompatible", "different")
    assert store.ready(settings.fingerprint()) is False


async def test_simulation_does_not_claim_mcp_execution(settings, store):
    job = submit(store, settings, mcp_servers=["echo"])
    await Runner(store, SimulatedRuntime(), 6).once()
    assert store.job("alice", job["id"])["error"] == "CAPABILITY_UNAVAILABLE_SIMULATED_MCP"

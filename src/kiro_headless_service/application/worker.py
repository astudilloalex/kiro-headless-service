import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from kiro_headless_service.application.contracts import bounded_json
from kiro_headless_service.application.ports import ExecutionPort, NotificationPort, PersistencePort
from kiro_headless_service.application.service import build_prompt
from kiro_headless_service.domain.model import RuntimeFailure, State

logger = logging.getLogger(__name__)


def validate_result(text: str, snapshot: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    if len(text.encode()) > snapshot["output_bytes"]:
        raise RuntimeFailure("RUNTIME_OUTPUT_LIMIT")
    if "\x00" in text:
        raise RuntimeFailure("INVALID_RESULT")
    fmt = snapshot["request"]["response_format"]
    if fmt["type"] == "text":
        return {"format": "text", "text": text, "usage": usage}
    try:

        def reject_constant(value: str) -> None:
            raise ValueError("non-finite JSON constant")

        def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON object member")
                value[key] = item
            return value

        data = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
        bounded_json(data)
        if fmt["type"] == "json_schema":
            Draft202012Validator(fmt["schema"]).validate(data)
    except (ValueError, ValidationError, RecursionError) as exc:
        raise RuntimeFailure("INVALID_RESULT") from exc
    return {"format": fmt["type"], "data": data, "usage": usage}


class Runner:
    def __init__(self, store: PersistencePort, runtime: ExecutionPort, lease_seconds: int):
        self.store, self.runtime, self.lease_seconds = store, runtime, lease_seconds
        self.id = str(uuid4())

    async def once(self) -> bool:
        job = await asyncio.to_thread(self.store.claim, self.id)
        if job is None:
            return False
        snapshot = job["snapshot"]

        async def emit(kind: str, data: dict[str, Any]) -> None:
            accepted = await asyncio.to_thread(
                self.store.append_event,
                job["id"],
                job["token"],
                kind,
                data,
            )
            if not accepted:
                raise RuntimeFailure("EXECUTION_CANCELLED_OR_LEASE_LOST")

        state, result, artifacts, error, retry = State.FAILED, None, [], None, False
        task: asyncio.Task[Any] | None = None
        try:
            history = (
                await asyncio.to_thread(self.store.history, job["session_id"])
                if job["session_id"]
                else []
            )
            prompt = build_prompt(snapshot, history)
            if (
                len(history) + (2 if job["session_id"] else 0) > snapshot["max_history_messages"]
                or len(prompt.encode())
                + len(snapshot["instructions"].encode())
                + snapshot.get("context_resource_bytes", 0)
                > snapshot["max_context_bytes"]
            ):
                raise RuntimeFailure("CONTEXT_LIMIT")
            task = asyncio.create_task(self.runtime.execute(snapshot, prompt, emit))
            async with asyncio.timeout(snapshot["timeout_seconds"] + 5):
                while True:
                    done, _ = await asyncio.wait({task}, timeout=self.lease_seconds / 3)
                    # Cancellation wins even if the provider just completed.
                    if not await asyncio.to_thread(self.store.renew, job["id"], job["token"]):
                        raise RuntimeFailure("EXECUTION_CANCELLED_OR_LEASE_LOST")
                    if done:
                        output = task.result()
                        result = validate_result(output.text, snapshot, output.usage)
                        artifacts = output.artifacts
                        state = State.SUCCEEDED
                        break
        except RuntimeFailure as exc:
            error, retry = exc.code, exc.safe_to_retry
            if error == "EXECUTION_TIMED_OUT":
                state = State.TIMED_OUT
            elif error == "EXECUTION_CANCELLED_OR_LEASE_LOST":
                state = State.CANCELLED
        except TimeoutError:
            state, error = State.TIMED_OUT, "EXECUTION_TIMED_OUT"
        except asyncio.CancelledError:
            error = "EXECUTION_AMBIGUOUS_WORKER_SHUTDOWN"
        except Exception:
            # Database/provider exception strings can contain content or secrets; discard them.
            error = "INTERNAL_RUNTIME_FAILURE"
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            saved = await asyncio.to_thread(
                self.store.finish,
                job["id"],
                job["token"],
                state,
                result,
                artifacts,
                error,
                retry,
            )
            logger.info(
                "job_finished",
                extra={
                    "job_id": job["id"],
                    "consumer_id": job["owner"],
                    "session_id": job["session_id"],
                    "correlation_id": job["correlation_id"],
                    "state": state,
                    "error_code": error,
                    "reservation_accepted": saved,
                },
            )
        return True


class Dispatcher:
    def __init__(self, store: PersistencePort, sender: NotificationPort):
        self.store, self.sender = store, sender

    async def once(self) -> bool:
        event = await asyncio.to_thread(self.store.claim_notification)
        if event is None:
            return False
        success = False
        try:
            await asyncio.wait_for(self.sender.deliver(event["url"], event["payload"]), timeout=15)
            success = True
        except (Exception, asyncio.CancelledError):
            logger.warning("notification_delivery_failed", extra={"event_id": event["id"]})
        await asyncio.to_thread(
            self.store.settle_notification, event["id"], event["token"], success
        )
        return True

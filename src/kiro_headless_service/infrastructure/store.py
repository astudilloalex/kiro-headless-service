import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import create_engine, delete, func, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from kiro_headless_service.domain.model import (
    TERMINAL,
    Artifact,
    Consumer,
    ServiceError,
    State,
    transition,
)
from kiro_headless_service.infrastructure.config import Settings
from kiro_headless_service.infrastructure.tables import (
    ArtifactRow,
    Event,
    Heartbeat,
    Job,
    Message,
    Outbox,
    SessionRow,
)


def uid() -> str:
    return str(uuid4())


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
        self.db = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _lock(db: Session) -> datetime:
        # A short, installation-wide scheduler lock serializes quota decisions, never inference.
        db.execute(text("SELECT pg_advisory_xact_lock(73489012)"))
        now = db.scalar(select(func.clock_timestamp()))
        assert now is not None
        return cast(datetime, now)

    @staticmethod
    def _job(db: Session, owner: str, job_id: str, lock: bool = False) -> Job:
        query = select(Job).where(Job.id == job_id, Job.owner == owner)
        if lock:
            query = query.with_for_update()
        job = db.scalar(query)
        if job is None:
            raise ServiceError("NOT_FOUND", "Job not found", 404)
        return job

    @staticmethod
    def _session(db: Session, owner: str, session_id: str, lock: bool = False) -> SessionRow:
        query = select(SessionRow).where(SessionRow.id == session_id, SessionRow.owner == owner)
        if lock:
            query = query.with_for_update()
        session = db.scalar(query)
        if session is None:
            raise ServiceError("NOT_FOUND", "Session not found", 404)
        return session

    @staticmethod
    def _view(job: Job) -> dict[str, Any]:
        return {
            "id": job.id,
            "state": job.state,
            "session_id": job.session_id,
            "correlation_id": job.correlation_id,
            "metadata": job.metadata_,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "attempts": job.attempts,
            "cancel_requested": job.cancel_requested,
            "error": job.error,
        }

    @staticmethod
    def _session_view(session: SessionRow) -> dict[str, Any]:
        return {
            "id": session.id,
            "closed": session.closed,
            "active_job": session.active_job,
            "metadata": session.metadata_,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "context_strategy": "service_history",
        }

    def submit(
        self,
        consumer: Consumer,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        key: str | None,
        correlation: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        operation = "job" if session_id is None else "turn:" + session_id
        hashed = digest(request)
        with self.db.begin() as db:
            now = self._lock(db)
            if key:
                old = db.scalar(
                    select(Job).where(
                        Job.owner == consumer.id,
                        Job.operation == operation,
                        Job.idempotency_key == key,
                    )
                )
                if old:
                    if old.request_hash != hashed:
                        raise ServiceError(
                            "IDEMPOTENCY_CONFLICT", "Key was used for different input", 409
                        )
                    return self._view(old)
            running_fingerprints = set(
                db.scalars(
                    select(Heartbeat.fingerprint).where(
                        Heartbeat.seen_at > now - timedelta(seconds=self.settings.lease_seconds * 2)
                    )
                )
            )
            if running_fingerprints - {self.settings.fingerprint()}:
                raise ServiceError(
                    "CONFIGURATION_MISMATCH", "Drain workers before changing configuration", 503
                )
            queued = db.scalar(
                select(func.count()).select_from(Job).where(Job.state == State.QUEUED)
            )
            own = db.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.state == State.QUEUED,
                    Job.owner == consumer.id,
                )
            )
            if (queued or 0) >= self.settings.max_queued or (own or 0) >= consumer.max_queued:
                raise ServiceError("QUOTA_EXCEEDED", "Durable queue capacity reached", 429)
            session = self._session(db, consumer.id, session_id, True) if session_id else None
            if session:
                if session.closed or session.active_job:
                    raise ServiceError(
                        "SESSION_CONFLICT", "Session is closed or has an active turn", 409
                    )
                history = self._history(db, session.id)
                from kiro_headless_service.application.service import build_prompt

                stored_count = (
                    db.scalar(
                        select(func.count())
                        .select_from(Message)
                        .where(
                            Message.session_id == session.id,
                        )
                    )
                    or 0
                )
                if (
                    stored_count + 2 > snapshot["max_history_messages"]
                    or len(build_prompt(snapshot, history).encode())
                    + len(snapshot["instructions"].encode())
                    + snapshot.get("context_resource_bytes", 0)
                    > snapshot["max_context_bytes"]
                ):
                    raise ServiceError(
                        "CONTEXT_LIMIT", "Conversation context limit reached; create a session", 409
                    )
            job = Job(
                id=uid(),
                owner=consumer.id,
                operation=operation,
                idempotency_key=key,
                request_hash=hashed,
                correlation_id=correlation,
                session_id=session_id,
                snapshot=snapshot,
                metadata_=request["metadata"],
                state=State.QUEUED,
                consumer_max_running=consumer.max_running,
                created_at=now,
                updated_at=now,
                available_at=now,
                attempts=0,
                event_count=1,
                cancel_requested=False,
            )
            db.add(job)
            db.flush()
            db.add(Event(job_id=job.id, kind="queued", data={"state": State.QUEUED}))
            if session:
                session.active_job, session.updated_at = job.id, now
                db.add(
                    Message(
                        session_id=session.id,
                        job_id=job.id,
                        seq=session.next_seq,
                        role="user",
                        content=request["input"],
                        status=State.QUEUED,
                    )
                )
                session.next_seq += 1
            return self._view(job)

    def job(self, owner: str, job_id: str) -> dict[str, Any]:
        with self.db() as db:
            return self._view(self._job(db, owner, job_id))

    def result(self, owner: str, job_id: str) -> dict[str, Any]:
        with self.db() as db:
            job = self._job(db, owner, job_id)
            if job.result is None:
                raise ServiceError("RESULT_UNAVAILABLE", "No successful result is available", 409)
            return job.result

    def _terminal(
        self,
        db: Session,
        job: Job,
        state: State,
        now: datetime,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        transition(job.state, state)
        job.state, job.result, job.error, job.updated_at = state, result, error, now
        job.token, job.lease_until = None, None
        db.add(Event(job_id=job.id, kind="completed", data={"state": state, "error": error}))
        job.event_count += 1
        if job.session_id:
            session = db.get(SessionRow, job.session_id, with_for_update=True)
            assert session is not None
            if session.active_job == job.id:
                session.active_job, session.updated_at = None, now
            db.execute(update(Message).where(Message.job_id == job.id).values(status=state))
            if state == State.SUCCEEDED:
                assert result is not None
                db.add(
                    Message(
                        session_id=session.id,
                        job_id=job.id,
                        seq=session.next_seq,
                        role="assistant",
                        content=result.get("text", result.get("data")),
                        status=state,
                    )
                )
                session.next_seq += 1
        url = job.snapshot["request"].get("callback")
        if url:
            event_id = uid()
            db.add(
                Outbox(
                    id=event_id,
                    job_id=job.id,
                    url=url,
                    available_at=now,
                    payload={
                        "id": event_id,
                        "version": 1,
                        "type": "job.completed",
                        "job_id": job.id,
                        "state": state,
                        "correlation_id": job.correlation_id,
                        "result_url": f"/v1/jobs/{job.id}/result",
                    },
                )
            )

    def cancel(self, owner: str, job_id: str) -> dict[str, Any]:
        with self.db.begin() as db:
            now = self._lock(db)
            job = self._job(db, owner, job_id, True)
            if job.state not in TERMINAL:
                job.cancel_requested = True
                if job.state == State.QUEUED:
                    self._terminal(db, job, State.CANCELLED, now)
            return self._view(job)

    def claim(self, worker: str) -> dict[str, Any] | None:
        with self.db.begin() as db:
            now = self._lock(db)
            for expired in db.scalars(
                select(Job)
                .where(
                    Job.state == State.RUNNING,
                    Job.lease_until <= now,
                )
                .with_for_update()
            ):
                self._terminal(
                    db,
                    expired,
                    State.CANCELLED if expired.cancel_requested else State.FAILED,
                    now,
                    error="EXECUTION_AMBIGUOUS_WORKER_LOST",
                )
            db.flush()
            running = db.scalar(
                select(func.count()).select_from(Job).where(Job.state == State.RUNNING)
            )
            if (running or 0) >= self.settings.max_running:
                return None
            candidates = db.scalars(
                select(Job)
                .where(
                    Job.state == State.QUEUED,
                    Job.available_at <= now,
                )
                .order_by(Job.created_at, Job.id)
                .with_for_update(skip_locked=True)
            )
            for job in candidates:
                own = db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.state == State.RUNNING,
                        Job.owner == job.owner,
                    )
                )
                if (own or 0) >= job.consumer_max_running:
                    continue
                transition(job.state, State.RUNNING)
                job.state, job.worker, job.token = State.RUNNING, worker, uid()
                job.lease_until = now + timedelta(seconds=self.settings.lease_seconds)
                job.attempts += 1
                job.updated_at = now
                db.add(Event(job_id=job.id, kind="running", data={"attempt": job.attempts}))
                job.event_count += 1
                return {
                    "id": job.id,
                    "owner": job.owner,
                    "token": job.token,
                    "snapshot": job.snapshot,
                    "session_id": job.session_id,
                    "correlation_id": job.correlation_id,
                }
            return None

    @staticmethod
    def _fenced(db: Session, job_id: str, token: str, now: datetime) -> Job | None:
        job = db.scalar(
            select(Job)
            .where(
                Job.id == job_id,
                Job.token == token,
                Job.state == State.RUNNING,
                Job.lease_until > now,
            )
            .with_for_update()
        )
        current = db.scalar(select(func.clock_timestamp()))
        if job is None or job.lease_until is None or current is None or job.lease_until <= current:
            return None
        return job

    def renew(self, job_id: str, token: str) -> bool:
        with self.db.begin() as db:
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            job = self._fenced(db, job_id, token, now)
            if job is None or job.cancel_requested:
                return False
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            job.lease_until = now + timedelta(seconds=self.settings.lease_seconds)
            return True

    def append_event(self, job_id: str, token: str, kind: str, data: dict[str, Any]) -> bool:
        with self.db.begin() as db:
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            job = self._fenced(db, job_id, token, now)
            if job is None or job.cancel_requested:
                return False
            if job.event_count < job.snapshot["max_events"]:
                db.add(Event(job_id=job_id, kind=kind, data=data))
                job.event_count += 1
            return True

    def finish(
        self,
        job_id: str,
        token: str,
        state: State,
        result: dict[str, Any] | None,
        artifacts: list[Artifact],
        error: str | None = None,
        retry: bool = False,
    ) -> bool:
        with self.db.begin() as db:
            now = self._lock(db)
            job = self._fenced(db, job_id, token, now)
            if job is None:
                return False
            if job.cancel_requested:
                state, result, artifacts, retry = State.CANCELLED, None, [], False
            if retry and job.attempts < job.snapshot["max_attempts"]:
                transition(job.state, State.QUEUED)
                job.state, job.token, job.lease_until = State.QUEUED, None, None
                job.available_at = now + timedelta(seconds=2**job.attempts)
                job.updated_at = now
                db.add(Event(job_id=job_id, kind="retry_scheduled", data={"error": error}))
                return True
            self._terminal(db, job, state, now, result, error)
            for artifact in artifacts:
                db.add(
                    ArtifactRow(
                        id=uid(),
                        job_id=job_id,
                        name=artifact.name,
                        data=artifact.data,
                        media_type=artifact.media_type,
                        size=len(artifact.data),
                        sha256=hashlib.sha256(artifact.data).hexdigest(),
                    )
                )
            return True

    def events(self, owner: str, job_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        with self.db() as db:
            self._job(db, owner, job_id)
            return [
                {
                    "id": e.id,
                    "version": 1,
                    "type": e.kind,
                    "data": e.data,
                    "created_at": e.created_at.isoformat(),
                }
                for e in db.scalars(
                    select(Event)
                    .where(Event.job_id == job_id, Event.id > after)
                    .order_by(Event.id)
                    .limit(limit)
                )
            ]

    def artifacts(self, owner: str, job_id: str) -> list[dict[str, Any]]:
        with self.db() as db:
            self._job(db, owner, job_id)
            # Do not select artifact bytes for the list operation.
            return [
                dict(r._mapping)
                for r in db.execute(
                    select(
                        ArtifactRow.id,
                        ArtifactRow.name,
                        ArtifactRow.media_type,
                        ArtifactRow.size,
                        ArtifactRow.sha256,
                    )
                    .where(ArtifactRow.job_id == job_id)
                    .order_by(ArtifactRow.id)
                )
            ]

    def artifact(self, owner: str, job_id: str, artifact_id: str) -> Artifact:
        with self.db() as db:
            self._job(db, owner, job_id)
            row = db.scalar(
                select(ArtifactRow).where(
                    ArtifactRow.id == artifact_id,
                    ArtifactRow.job_id == job_id,
                )
            )
            if row is None:
                raise ServiceError("NOT_FOUND", "Artifact not found", 404)
            if len(row.data) != row.size or hashlib.sha256(row.data).hexdigest() != row.sha256:
                raise ServiceError(
                    "ARTIFACT_INTEGRITY_FAILURE", "Stored artifact integrity check failed", 500
                )
            return Artifact(row.name, row.data, row.media_type)

    def create_session(
        self,
        owner: str,
        metadata: dict[str, str],
        key: str | None,
    ) -> dict[str, Any]:
        with self.db.begin() as db:
            now = self._lock(db)
            if key:
                old = db.scalar(
                    select(SessionRow).where(
                        SessionRow.owner == owner,
                        SessionRow.idempotency_key == key,
                    )
                )
                if old:
                    if old.request_hash != digest(metadata):
                        raise ServiceError(
                            "IDEMPOTENCY_CONFLICT", "Key was used for different input", 409
                        )
                    return self._session_view(old)
            session = SessionRow(
                id=uid(),
                owner=owner,
                metadata_=metadata,
                idempotency_key=key,
                request_hash=digest(metadata),
                created_at=now,
                updated_at=now,
                closed=False,
                active_job=None,
                next_seq=1,
            )
            db.add(session)
            return self._session_view(session)

    def session(self, owner: str, session_id: str) -> dict[str, Any]:
        with self.db() as db:
            return self._session_view(self._session(db, owner, session_id))

    def close_session(self, owner: str, session_id: str) -> dict[str, Any]:
        with self.db.begin() as db:
            self._lock(db)
            session = self._session(db, owner, session_id, True)
            if session.active_job:
                raise ServiceError(
                    "SESSION_CONFLICT", "Cancel or complete the active turn first", 409
                )
            session.closed = True
            session.updated_at = db.scalar(select(func.clock_timestamp()))  # type: ignore[assignment]
            return self._session_view(session)

    def messages(self, owner: str, session_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        with self.db() as db:
            self._session(db, owner, session_id)
            return [
                {
                    "seq": m.seq,
                    "role": m.role,
                    "content": m.content,
                    "job_id": m.job_id,
                    "status": m.status,
                }
                for m in db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id, Message.seq > after)
                    .order_by(Message.seq)
                    .limit(limit)
                )
            ]

    @staticmethod
    def _history(db: Session, session_id: str) -> list[dict[str, Any]]:
        return [
            {"role": m.role, "content": m.content}
            for m in db.scalars(
                select(Message)
                .where(
                    Message.session_id == session_id,
                    Message.status == State.SUCCEEDED,
                )
                .order_by(Message.seq)
            )
        ]

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self.db() as db:
            return self._history(db, session_id)

    def claim_notification(self) -> dict[str, Any] | None:
        with self.db.begin() as db:
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            db.execute(
                update(Outbox)
                .where(
                    ~Outbox.delivered,
                    ~Outbox.exhausted,
                    Outbox.attempts >= self.settings.notification_attempts,
                    or_(Outbox.lease_until.is_(None), Outbox.lease_until <= now),
                )
                .values(exhausted=True)
            )
            row = db.scalar(
                select(Outbox)
                .where(
                    ~Outbox.delivered,
                    ~Outbox.exhausted,
                    Outbox.attempts < self.settings.notification_attempts,
                    Outbox.available_at <= now,
                    or_(Outbox.lease_until.is_(None), Outbox.lease_until <= now),
                )
                .order_by(Outbox.available_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            row.token, row.lease_until = uid(), now + timedelta(seconds=30)
            row.attempts += 1
            return {"id": row.id, "token": row.token, "url": row.url, "payload": row.payload}

    def settle_notification(self, event_id: str, token: str, success: bool) -> None:
        with self.db.begin() as db:
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            row = db.scalar(
                select(Outbox)
                .where(
                    Outbox.id == event_id,
                    Outbox.token == token,
                    Outbox.lease_until > now,
                )
                .with_for_update()
            )
            if row:
                row.delivered = success
                row.exhausted = not success and row.attempts >= self.settings.notification_attempts
                row.token, row.lease_until = None, None
                row.available_at = now + timedelta(
                    seconds=min(
                        3600,
                        self.settings.notification_base_seconds * 2 ** min(row.attempts - 1, 12),
                    )
                )

    def heartbeat(self, worker: str, fingerprint: str) -> None:
        from sqlalchemy.dialects.postgresql import insert

        with self.db.begin() as db:
            db.execute(
                insert(Heartbeat)
                .values(worker=worker, fingerprint=fingerprint)
                .on_conflict_do_update(
                    index_elements=[Heartbeat.worker],
                    set_={
                        "fingerprint": fingerprint,
                        "seen_at": func.clock_timestamp(),
                    },
                )
            )

    def ready(self, fingerprint: str) -> bool:
        try:
            with self.db() as db:
                revision = db.scalar(text("SELECT version_num FROM alembic_version"))
                beats = list(
                    db.scalars(
                        select(Heartbeat).where(
                            Heartbeat.seen_at
                            > func.now() - timedelta(seconds=self.settings.lease_seconds * 2)
                        )
                    )
                )
                fingerprints = {beat.fingerprint for beat in beats}
                runner = any(beat.worker.startswith("runner:") for beat in beats)
                dispatcher = any(beat.worker.startswith("dispatcher:") for beat in beats)
                return (
                    revision == "0001"
                    and fingerprints == {fingerprint}
                    and runner
                    and (not self.settings.callback_urls or dispatcher)
                )
        except Exception:
            return False

    def cleanup(self) -> int:
        with self.db.begin() as db:
            now = self._lock(db)
            jobs = db.execute(
                delete(Job).where(
                    Job.state.in_(TERMINAL),
                    Job.updated_at < now - timedelta(seconds=self.settings.retention_seconds),
                )
            )
            db.execute(
                delete(SessionRow).where(
                    SessionRow.active_job.is_(None),
                    SessionRow.updated_at
                    < now - timedelta(seconds=self.settings.session_retention_seconds),
                )
            )
            db.execute(delete(Heartbeat).where(Heartbeat.seen_at < now - timedelta(days=1)))
            return jobs.rowcount  # type: ignore[attr-defined,no-any-return]

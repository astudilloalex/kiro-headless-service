import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kiro_headless_service.api.models import (
    ArtifactView,
    ErrorEnvelope,
    EventPage,
    JobView,
    MessagePage,
    ResultView,
    SessionView,
)
from kiro_headless_service.application.contracts import JobRequest, SessionRequest
from kiro_headless_service.application.service import JobService
from kiro_headless_service.domain.model import TERMINAL, Consumer, ServiceError
from kiro_headless_service.infrastructure.config import Policy, Settings
from kiro_headless_service.infrastructure.store import Store


class RequestBoundary:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        requested = headers.get(b"x-correlation-id", b"").decode("latin1")
        correlation = (
            requested if re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", requested) else str(uuid4())
        )
        scope.setdefault("state", {})["correlation_id"] = correlation
        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.extend(message.get("body", b""))
            if len(chunks) > 262144:
                await JSONResponse(
                    {
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "Body exceeds 256 KiB",
                            "correlation_id": correlation,
                        }
                    },
                    status_code=413,
                    headers={"X-Correlation-ID": correlation},
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(chunks), "more_body": False}
            return await receive()

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-correlation-id", correlation.encode()),
                ]
            await send(message)

        await self.app(scope, bounded_receive, correlated_send)


def create_app(settings: Settings, store: Store | None = None) -> FastAPI:
    store = store or Store(settings)
    policy = Policy(settings)
    service = JobService(store, policy)
    app = FastAPI(
        title="Kiro Headless Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        responses={
            code: {"model": ErrorEnvelope} for code in (400, 401, 403, 404, 409, 413, 422, 429, 503)
        },
    )
    app.add_middleware(RequestBoundary)
    app.state.store, app.state.settings = store, settings
    bearer = HTTPBearer(auto_error=False)

    def consumer(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Consumer:
        if credentials is None:
            raise ServiceError("UNAUTHENTICATED", "API bearer token is required", 401)
        return policy.authenticate(credentials.credentials)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "correlation_id": request.state.correlation_id,
                }
            },
            status_code=exc.status,
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return await service_error(
            request, ServiceError("INVALID_INPUT", "Request validation failed", 422)
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return await service_error(
            request, ServiceError("HTTP_ERROR", "HTTP request failed", exc.status_code)
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        return await service_error(
            request, ServiceError("INTERNAL_ERROR", "Internal service failure", 500)
        )

    auth = Depends(consumer)
    key_header = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9._:-]+$",
    )

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        if not store.ready(settings.fingerprint()):
            raise ServiceError(
                "NOT_READY", "Database, migrations or matching runner is unavailable", 503
            )
        return {"status": "ready"}

    @app.post("/v1/jobs", status_code=202, response_model=JobView, tags=["jobs"])
    def submit(
        body: JobRequest,
        request: Request,
        response: Response,
        who: Consumer = auth,
        key: str | None = key_header,
    ) -> Any:
        job = service.submit(who, body, key, request.state.correlation_id)
        response.headers["Location"] = "/v1/jobs/" + job["id"]
        return job

    @app.get("/v1/jobs/{jobId}", response_model=JobView, tags=["jobs"])
    def get_job(jobId: UUID, who: Consumer = auth) -> Any:
        return store.job(who.id, str(jobId))

    @app.get(
        "/v1/jobs/{jobId}/result",
        response_model=ResultView,
        response_model_exclude_unset=True,
        tags=["jobs"],
    )
    def result(jobId: UUID, who: Consumer = auth) -> Any:
        return store.result(who.id, str(jobId))

    @app.post("/v1/jobs/{jobId}/cancel", response_model=JobView, tags=["jobs"])
    def cancel(jobId: UUID, who: Consumer = auth) -> Any:
        return store.cancel(who.id, str(jobId))

    @app.get(
        "/v1/jobs/{jobId}/events",
        response_model=EventPage,
        tags=["events"],
        responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    )
    async def events(
        jobId: UUID,
        request: Request,
        who: Consumer = auth,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        stream: bool = False,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        job_id = str(jobId)
        await asyncio.to_thread(store.job, who.id, job_id)
        if not stream:
            items = await asyncio.to_thread(store.events, who.id, job_id, after, limit)
            return {"items": items, "next_cursor": items[-1]["id"] if items else after}
        if not settings.sse_enabled:
            raise ServiceError("CAPABILITY_UNAVAILABLE", "SSE is disabled", 422)
        if last_event_id is not None:
            if not re.fullmatch(r"[0-9]{1,18}", last_event_id):
                raise ServiceError(
                    "INVALID_CURSOR", "Last-Event-ID must be a nonnegative integer", 400
                )
            after = max(after, int(last_event_id))

        async def generate() -> AsyncIterator[str]:
            cursor = after
            for _ in range(60):
                if await request.is_disconnected():
                    break
                items = await asyncio.to_thread(store.events, who.id, job_id, cursor, limit)
                for event in items:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                job = await asyncio.to_thread(store.job, who.id, job_id)
                if job["state"] in TERMINAL and len(items) < limit:
                    break
                if not items:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/jobs/{jobId}/artifacts", response_model=list[ArtifactView], tags=["artifacts"])
    def artifacts(jobId: UUID, who: Consumer = auth) -> Any:
        return store.artifacts(who.id, str(jobId))

    @app.get(
        "/v1/jobs/{jobId}/artifacts/{artifactId}",
        tags=["artifacts"],
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                }
            }
        },
    )
    def download(jobId: UUID, artifactId: UUID, who: Consumer = auth) -> Response:
        artifact = store.artifact(who.id, str(jobId), str(artifactId))
        return Response(
            artifact.data,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(artifact.name, safe=""),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/sessions", status_code=201, response_model=SessionView, tags=["sessions"])
    def new_session(
        body: SessionRequest, who: Consumer = auth, key: str | None = key_header
    ) -> Any:
        return store.create_session(who.id, body.metadata, key)

    @app.get("/v1/sessions/{sessionId}", response_model=SessionView, tags=["sessions"])
    def session(sessionId: UUID, who: Consumer = auth) -> Any:
        return store.session(who.id, str(sessionId))

    @app.post("/v1/sessions/{sessionId}/close", response_model=SessionView, tags=["sessions"])
    def close(sessionId: UUID, who: Consumer = auth) -> Any:
        return store.close_session(who.id, str(sessionId))

    @app.get("/v1/sessions/{sessionId}/messages", response_model=MessagePage, tags=["sessions"])
    def messages(
        sessionId: UUID,
        who: Consumer = auth,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> Any:
        items = store.messages(who.id, str(sessionId), after, limit)
        return {"items": items, "next_cursor": items[-1]["seq"] if items else after}

    @app.post(
        "/v1/sessions/{sessionId}/messages",
        status_code=202,
        response_model=JobView,
        tags=["sessions"],
    )
    def turn(
        sessionId: UUID,
        body: JobRequest,
        request: Request,
        response: Response,
        who: Consumer = auth,
        key: str | None = key_header,
    ) -> Any:
        job = service.submit(who, body, key, request.state.correlation_id, str(sessionId))
        response.headers["Location"] = "/v1/jobs/" + job["id"]
        return job

    @app.get("/v1/profiles", tags=["capabilities"])
    def profiles(who: Consumer = auth) -> list[dict[str, str]]:
        return [
            {"id": key, "description": settings.profiles[key].description}
            for key in sorted(who.profiles)
        ]

    @app.get("/v1/agents", tags=["capabilities"])
    def agents(who: Consumer = auth) -> list[dict[str, str]]:
        return [
            {"id": key, "description": settings.agents[key].get("description", "Native V3 agent")}
            for key in sorted(who.agents)
        ]

    @app.get("/v1/capabilities", tags=["capabilities"])
    def capabilities(who: Consumer = auth) -> dict[str, Any]:
        return {
            "engine": settings.engine,
            "cli_version": settings.cli_version,
            "agent_engine": settings.agent_engine,
            "models": settings.models,
            "mcp_servers": sorted(who.mcp) if settings.engine == "kiro" else [],
            "mcp_transports": ["stdio", "streamable-http"] if settings.engine == "kiro" else [],
            "sse": settings.sse_enabled,
            "context_strategy": "service_history",
            "response_formats": ["text", "json", "json_schema"],
            "max_timeout_seconds": settings.max_timeout_seconds,
            "max_output_bytes": settings.max_output_bytes,
            "mandatory_os_sandbox": False,
        }

    return app

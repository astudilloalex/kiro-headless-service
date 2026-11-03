import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, PrivateAttr, model_validator

from kiro_headless_service.application.contracts import ResponseFormat, StrictModel
from kiro_headless_service.domain.model import Consumer, ServiceError
from kiro_headless_service.infrastructure.agents import AgentCatalog, parse_agent
from kiro_headless_service.infrastructure.bootstrap import DEFAULT_CACHE
from kiro_headless_service.infrastructure.release import AGENT_ENGINE, CLI_VERSION

ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
TOOL = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def secret(ref: str) -> str:
    """Service secret references; never persist the resolved value."""
    if ref.startswith("env:"):
        value = os.environ.get(ref[4:], "")
    elif ref.startswith("file:"):
        value = Path(ref[5:]).read_text().strip()
    else:
        raise ValueError("secret reference must start with env: or file:")
    if not value:
        raise ValueError("required secret reference is unavailable")
    return value


class ConsumerConfig(StrictModel):
    token_ref: str
    profiles: list[str] = Field(default_factory=lambda: ["default"])
    agents: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)
    max_running: int = Field(default=1, ge=1, le=128)
    max_queued: int = Field(default=100, ge=1, le=10000)


class Profile(StrictModel):
    description: str = "Conservative text execution"
    instructions: str = Field(default="", max_length=32768)
    tools: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    response_format: ResponseFormat = Field(default_factory=ResponseFormat)
    permissions: dict[str, Any] = Field(default_factory=lambda: {"rules": []})

    @model_validator(mode="after")
    def permission_rules(self) -> Self:
        parse_agent(json.dumps({"permissions": self.permissions}).encode(), ".json")
        return self


class MCP(StrictModel):
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_refs: dict[str, str] = Field(default_factory=dict)
    header_refs: dict[str, str] = Field(default_factory=dict)
    tools: list[str] = Field(min_length=1, max_length=100)
    env_templates: dict[str, str] = Field(default_factory=dict)
    header_templates: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=60000, ge=100, le=120000)
    request_timeout: int = Field(default=120000, ge=100, le=3600000)

    @classmethod
    def from_native(cls, config: dict[str, Any], tools: list[str]) -> "MCP":
        if not isinstance(config, dict):
            raise ValueError("native MCP server must be an object")
        for field in ("env", "headers"):
            values = config.get(field, {})
            if not isinstance(values, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()
            ):
                raise ValueError("native MCP environment/headers must contain strings")
        if set(config) - {"command", "args", "env", "url", "headers", "timeout", "requestTimeout"}:
            raise ValueError(
                "unsupported native MCP field; interactive OAuth/trust overrides are unavailable"
            )
        for key, value in config.get("headers", {}).items():
            if key.lower() not in {"accept", "content-type"} and "${" not in value:
                raise ValueError("native MCP authentication headers require ${ENV_VAR} references")
        for key, value in config.get("env", {}).items():
            if re.search(r"SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL", key, re.I) and "${" not in value:
                raise ValueError("native MCP credentials require ${ENV_VAR} references")
        return cls(
            command=config.get("command"),
            args=config.get("args", []),
            url=config.get("url"),
            env_templates=config.get("env", {}),
            header_templates=config.get("headers", {}),
            timeout=config.get("timeout", 60000),
            request_timeout=config.get("requestTimeout", 120000),
            tools=tools,
        )

    @model_validator(mode="after")
    def transport(self) -> Self:
        if bool(self.command) == bool(self.url):
            raise ValueError("MCP needs exactly one of command or url")
        if self.command and not Path(self.command).is_absolute():
            raise ValueError("local MCP command must be an absolute installed executable")
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("remote MCP requires HTTPS (HTTP permitted for loopback)")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("MCP credentials belong in header_refs, not the URL")
        if any(not TOOL.fullmatch(t) for t in self.tools):
            raise ValueError("MCP tool names must be exact identifiers")
        for ref in [*self.env_refs.values(), *self.header_refs.values()]:
            if not ref.startswith(("env:", "file:")):
                raise ValueError("MCP credentials require secret references")
        return self


class Settings(StrictModel):
    database_url: str = "postgresql+psycopg://kiro@localhost:5432/kiro"
    database_url_file: str | None = None
    engine: Literal["kiro", "simulated"] = "kiro"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    cli_path: str = "/opt/kiro/bin/kiro-cli"
    cli_bootstrap: Literal["installed", "download", "offline"] = "installed"
    cli_cache_dir: str = DEFAULT_CACHE
    kiro_proxy_env_refs: dict[str, str] = Field(default_factory=dict)
    kiro_config_dir: str | None = None
    max_agent_files: int = Field(default=128, ge=1, le=1024)
    max_agent_file_bytes: int = Field(default=262144, ge=1024, le=1048576)
    max_agent_bundle_bytes: int = Field(default=2097152, ge=1024, le=8388608)
    kiro_key_ref: str = "env:KIRO_API_KEY"
    temp_dir: str = "/tmp/kiro-service"
    require_os_sandbox: bool = False
    models: list[str] = Field(default_factory=list)
    consumers: dict[str, ConsumerConfig] = Field(default_factory=dict)
    profiles: dict[str, Profile] = Field(default_factory=lambda: {"default": Profile()})
    mcp_servers: dict[str, MCP] = Field(default_factory=dict)
    callback_urls: list[str] = Field(default_factory=list)
    callback_private_cidrs: list[str] = Field(default_factory=list)
    max_running: int = Field(default=4, ge=1, le=128)
    max_queued: int = Field(default=1000, ge=1, le=100000)
    max_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_output_bytes: int = Field(default=1048576, ge=128, le=8388608)
    max_events: int = Field(default=2000, ge=10, le=100000)
    max_artifacts: int = Field(default=10, ge=0, le=100)
    max_artifact_bytes: int = Field(default=1048576, ge=1, le=8388608)
    max_artifacts_bytes: int = Field(default=4194304, ge=1, le=33554432)
    max_history_messages: int = Field(default=100, ge=2, le=1000)
    max_context_bytes: int = Field(default=262144, ge=1024, le=2097152)
    lease_seconds: int = Field(default=30, ge=6, le=300)
    max_attempts: int = Field(default=2, ge=1, le=5)
    poll_seconds: float = Field(default=0.5, ge=0.05, le=10)
    retention_seconds: int = Field(default=604800, ge=60)
    session_retention_seconds: int = Field(default=2592000, ge=60)
    notification_attempts: int = Field(default=8, ge=1, le=30)
    notification_base_seconds: int = Field(default=5, ge=1, le=300)
    sse_enabled: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    _catalog: AgentCatalog = PrivateAttr()
    _native_mcp: dict[str, MCP] = PrivateAttr(default_factory=dict)

    @property
    def cli_version(self) -> str:
        return CLI_VERSION

    @property
    def agent_engine(self) -> str:
        return AGENT_ENGINE

    @property
    def agents(self) -> dict[str, dict[str, Any]]:
        return self._catalog.agents

    def agent_snapshot(self, identifier: str | None) -> dict[str, Any]:
        return self._catalog.snapshot(identifier)

    def mcp(self, identifier: str) -> MCP:
        return self.mcp_servers.get(identifier) or self._native_mcp[identifier]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not Path(self.cli_cache_dir).is_absolute():
            raise ValueError("cli_cache_dir must be absolute")
        if set(self.kiro_proxy_env_refs) - {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}:
            raise ValueError(
                "only explicit HTTP_PROXY/HTTPS_PROXY/NO_PROXY references are supported"
            )
        if any(not ref.startswith(("env:", "file:")) for ref in self.kiro_proxy_env_refs.values()):
            raise ValueError("proxy configuration requires secret references")
        if self.database_url_file:
            self.database_url = secret("file:" + self.database_url_file)
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("database_url requires postgresql+psycopg")
        if self.require_os_sandbox:
            raise ValueError(
                "mandatory OS sandbox is unsupported by this adapter; use external isolation"
            )
        if not self.consumers:
            raise ValueError("configure at least one consumer and an independent API token")
        if "default" not in self.profiles:
            raise ValueError("default profile is required")
        self._catalog = AgentCatalog(
            self.kiro_config_dir,
            self.max_agent_files,
            self.max_agent_file_bytes,
            self.max_agent_bundle_bytes,
        )
        self._native_mcp = {}
        for agent in self.agents.values():
            for name, definition in agent.get("mcpServers", {}).items():
                if not ID.fullmatch(name):
                    raise ValueError("invalid native MCP identifier")
                tools = [
                    t.split("/", 1)[1] for t in agent["tools"] if t.startswith("@" + name + "/")
                ]
                native = MCP.from_native(definition, tools)
                existing = self.mcp_servers.get(name) or self._native_mcp.get(name)
                if existing and existing != native:
                    raise ValueError("conflicting definitions for a native MCP identifier")
                self._native_mcp[name] = native
        for names in [
            list(self.consumers),
            list(self.profiles),
            list(self.agents),
            list(self.mcp_servers),
        ]:
            if any(not ID.fullmatch(name) for name in names):
                raise ValueError("configuration identifiers must match [a-zA-Z0-9_-]{1,64}")
        for tools in [p.tools for p in self.profiles.values()]:
            if any(not TOOL.fullmatch(t) for t in tools):
                raise ValueError("tools require exact builtin identifiers; wildcards are forbidden")
        for consumer in self.consumers.values():
            if not set(consumer.profiles) <= self.profiles.keys():
                raise ValueError("consumer references unknown profile")
            if not set(consumer.agents) <= self.agents.keys():
                raise ValueError("consumer references unknown agent")
            if not set(consumer.mcp) <= self.mcp_servers.keys() | self._native_mcp.keys():
                raise ValueError("consumer references unknown MCP")
        from kiro_headless_service.infrastructure.notifications import validate_url

        for url in self.callback_urls:
            validate_url(url)
        return self

    def fingerprint(self) -> str:
        # Shared limits must agree between every API and worker. No secret values in this digest.
        data = self.model_dump(
            exclude={
                "database_url",
                "database_url_file",
                "host",
                "port",
                "cli_path",
                "cli_bootstrap",
                "cli_cache_dir",
            }
        )
        data["agent_catalog_digest"] = self._catalog.digest
        data["cli_version"], data["agent_engine"] = self.cli_version, self.agent_engine
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def load_settings() -> Settings:
    if "KHS_CLI_VERSION" in os.environ or "KHS_AGENT_ENGINE" in os.environ:
        raise ValueError(
            "CLI version/engine are supplied by the image release; remove runtime overrides"
        )
    path = os.environ.get("KHS_CONFIG")
    data = json.loads(Path(path).read_text()) if path else {}
    for name in Settings.model_fields:
        value = os.environ.get("KHS_" + name.upper())
        if value is not None:
            try:
                data[name] = json.loads(value)
            except json.JSONDecodeError:
                data[name] = value
    return Settings.model_validate(data)


class Policy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tokens: list[tuple[bytes, Consumer]] = []
        for name, cfg in settings.consumers.items():
            token = secret(cfg.token_ref)
            if len(token) < 24:
                raise ValueError("API tokens must contain at least 24 characters")
            digest = hashlib.sha256(token.encode()).digest()
            if any(hmac.compare_digest(digest, old) for old, _ in self.tokens):
                raise ValueError("API tokens must be unique per consumer")
            self.tokens.append(
                (
                    digest,
                    Consumer(
                        name,
                        frozenset(cfg.profiles),
                        frozenset(cfg.agents),
                        frozenset(cfg.mcp),
                        cfg.max_running,
                        cfg.max_queued,
                    ),
                )
            )

    def authenticate(self, token: str) -> Consumer:
        digest = hashlib.sha256(token.encode()).digest()
        for expected, consumer in self.tokens:
            if hmac.compare_digest(digest, expected):
                return consumer
        raise ServiceError("UNAUTHENTICATED", "A valid API bearer token is required", 401)

    def snapshot(self, consumer: Consumer, request: dict[str, Any]) -> dict[str, Any]:
        s = self.settings
        if request["profile"] not in consumer.profiles:
            raise ServiceError("ACCESS_DENIED", "Profile is not authorized", 403)
        if request["agent"] and request["agent"] not in consumer.agents:
            raise ServiceError("ACCESS_DENIED", "Agent is not authorized", 403)
        if not set(request["mcp_servers"]) <= consumer.mcp:
            raise ServiceError("ACCESS_DENIED", "MCP selection is not authorized", 403)
        if request["model"] and request["model"] not in s.models:
            raise ServiceError("CAPABILITY_UNAVAILABLE", "Model is not enabled", 422)
        if request["callback"] and request["callback"] not in s.callback_urls:
            raise ServiceError("ACCESS_DENIED", "Callback URL is not allowlisted", 403)
        profile = s.profiles[request["profile"]]
        bundle = s.agent_snapshot(request["agent"])
        agent = bundle["config"]
        builtin_tools = {t for t in agent.get("tools", []) if not t.startswith("@")}
        if not builtin_tools <= set(profile.tools):
            raise ServiceError("ACCESS_DENIED", "Agent tools exceed profile permissions", 403)
        native_mcp = set(agent.get("mcpServers", {}))
        if not native_mcp <= consumer.mcp:
            raise ServiceError(
                "ACCESS_DENIED", "Agent MCP servers exceed consumer permissions", 403
            )
        selected_mcp = set(request["mcp_servers"]) | native_mcp
        for tool in agent.get("tools", []):
            if tool.startswith("@"):
                name, _, tool_name = tool[1:].partition("/")
                if name not in selected_mcp or tool_name not in s.mcp(name).tools:
                    raise ServiceError(
                        "ACCESS_DENIED", "Agent references an unavailable MCP tool", 403
                    )
        model = request["model"] or agent.get("model")
        if model and model not in s.models:
            raise ServiceError("CAPABILITY_UNAVAILABLE", "Agent model is not enabled", 422)
        request = json.loads(json.dumps(request))
        request["model"] = model
        request["mcp_servers"] = sorted(selected_mcp)
        request["response_format"] = request[
            "response_format"
        ] or profile.response_format.model_dump(mode="json", by_alias=True)
        return {
            "request": request,
            "engine": s.engine,
            "cli_version": s.cli_version,
            "agent_engine": s.agent_engine,
            "native_agent": bundle,
            "context_resource_bytes": sum(
                len(base64.b64decode(raw)) for raw in bundle["files"].values()
            ),
            "profile_permissions": profile.permissions,
            "kiro_key_ref": s.kiro_key_ref,
            "kiro_proxy_env_refs": s.kiro_proxy_env_refs,
            "instructions": profile.instructions + "\n" + agent.get("prompt", ""),
            "tools": agent.get("tools", []) if request["agent"] else profile.tools,
            "mcp": {k: s.mcp(k).model_dump() for k in request["mcp_servers"]},
            "timeout_seconds": min(
                request["limits"]["timeout_seconds"] or profile.timeout_seconds,
                profile.timeout_seconds,
                s.max_timeout_seconds,
            ),
            "output_bytes": min(
                request["limits"]["output_bytes"] or s.max_output_bytes,
                s.max_output_bytes,
            ),
            "max_events": s.max_events,
            "max_artifacts": s.max_artifacts,
            "max_artifact_bytes": s.max_artifact_bytes,
            "max_artifacts_bytes": s.max_artifacts_bytes,
            "max_history_messages": s.max_history_messages,
            "max_context_bytes": s.max_context_bytes,
            "max_attempts": s.max_attempts,
        }

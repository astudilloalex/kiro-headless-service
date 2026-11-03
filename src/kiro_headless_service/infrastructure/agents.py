"""Read trusted native V3 agent files; capture immutable, bounded configuration bundles."""

import base64
import copy
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import stat
from pathlib import Path
from typing import Any

import yaml
from yaml.tokens import AliasToken

from kiro_headless_service.application.contracts import bounded_json

IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
FIELDS = {
    "name",
    "description",
    "prompt",
    "model",
    "tools",
    "excludedTools",
    "permissions",
    "resources",
    "hooks",
    "includeMcpJson",
    "includePowers",
    "welcomeMessage",
    "mcpServers",
}


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ValueError("native YAML object keys must be strings")
            if key in result:
                raise ValueError("duplicate YAML field")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_agent(raw: bytes, extension: str) -> dict[str, Any]:
    text = raw.decode("utf-8")
    if extension == ".json":

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        data = json.loads(text, object_pairs_hook=unique)
    else:
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError("Markdown agents require YAML frontmatter")
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("unclosed Markdown frontmatter")
        frontmatter = "".join(lines[1:end])
        try:
            if any(isinstance(token, AliasToken) for token in yaml.scan(frontmatter)):
                raise ValueError("YAML aliases are unsupported")
            data = yaml.load(frontmatter, Loader=UniqueLoader)
        except yaml.YAMLError as exc:
            raise ValueError("invalid native agent YAML frontmatter") from exc
        if isinstance(data, dict):
            if "prompt" in data:
                raise ValueError("Markdown instructions belong after the frontmatter")
            data["prompt"] = "".join(lines[end + 1 :]).strip()
    if not isinstance(data, dict) or not set(data) <= FIELDS:
        raise ValueError("unknown or legacy agent fields; use the native V3 schema")
    try:
        bounded_json(data)
    except TypeError as exc:
        raise ValueError("native agents require JSON-compatible values") from exc
    for field in ("description", "prompt", "model", "welcomeMessage"):
        if field in data and not isinstance(data[field], str):
            raise ValueError(f"agent {field} must be text")
    for field in ("tools", "excludedTools"):
        if not isinstance(data.get(field, []), list) or any(
            not isinstance(item, str) for item in data.get(field, [])
        ):
            raise ValueError(f"agent {field} must be a list of selectors")
    if any(
        t in {"*", "@builtin", "@mcp", "subagent"}
        or t.startswith("subagent/")
        or (t.startswith("@") and "/" not in t)
        for t in data.get("tools", [])
    ):
        raise ValueError(
            "use bounded tool selectors; wildcard trust and sub-agents are unsupported"
        )
    if any(
        not re.fullmatch(r"[A-Za-z0-9_-]+|@[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", tool)
        for tool in data.get("tools", [])
    ):
        raise ValueError("invalid native tool selector")
    if (
        data.get("includeMcpJson", False) is not False
        or data.get("includePowers", False) is not False
    ):
        raise ValueError(
            "external MCP/Powers inheritance is disabled; configure explicit integrations"
        )
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict) or set(permissions) - {"rules"}:
        raise ValueError("agent permissions must contain rules")
    rules = permissions.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("permissions.rules must be a list")
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or set(rule) - {"capability", "match", "effect", "exclude"}
            or not isinstance(rule.get("capability"), str)
            or rule.get("effect") not in {"allow", "ask", "deny"}
        ):
            raise ValueError("invalid capability permission rule")
        for field in ("match", "exclude"):
            if field in rule and (
                not isinstance(rule[field], list)
                or any(not isinstance(pattern, str) for pattern in rule[field])
            ):
                raise ValueError("permission patterns must be lists of strings")
    if not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError("mcpServers must be an object")
    if not isinstance(data.get("resources", []), list):
        raise ValueError("resources must be a list")
    if any(not isinstance(resource, str) for resource in data.get("resources", [])):
        raise ValueError(
            "only file:// and skill:// resources are supported; indexing is unavailable"
        )
    if data.get("hooks"):
        raise ValueError(
            "agent hooks are unsupported by this service's controlled execution policy"
        )
    data.setdefault("tools", [])
    return data


class AgentCatalog:
    def __init__(self, directory: str | None, max_files: int, max_file_bytes: int, max_bytes: int):
        self.agents: dict[str, dict[str, Any]] = {}
        self.files: dict[str, str] = {}
        if directory is None:
            self.digest = hashlib.sha256(b"").hexdigest()
            return
        root = Path(directory)
        if not root.is_absolute():
            raise ValueError("kiro_config_dir must be an absolute directory")
        total = 0

        def read(fd: int, prefix: str = "") -> None:
            nonlocal total
            with os.scandir(fd) as entries:
                for entry in entries:
                    name = prefix + entry.name
                    if entry.name.startswith(".") or "\\" in entry.name:
                        raise ValueError(
                            "hidden files and unsafe names are not allowed in Kiro configuration"
                        )
                    if not prefix and entry.name not in {
                        "agents",
                        "resources",
                        "skills",
                        "steering",
                    }:
                        raise ValueError(
                            "Kiro configuration root supports agents/resources/skills/steering"
                        )
                    child = os.open(
                        entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                    )
                    try:
                        info = os.fstat(child)
                        if stat.S_ISDIR(info.st_mode):
                            if name.count("/") >= 16:
                                raise ValueError("configuration nesting limit exceeded")
                            read(child, name + "/")
                        else:
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                raise ValueError(
                                    "configuration files must be regular, without links"
                                )
                            if len(self.files) >= max_files or info.st_size > max_file_bytes:
                                raise ValueError("agent configuration file limit exceeded")
                            with os.fdopen(child, "rb", closefd=False) as stream:
                                raw = stream.read(max_file_bytes + 1)
                            total += len(raw)
                            if len(raw) > max_file_bytes or total > max_bytes:
                                raise ValueError("agent configuration byte limit exceeded")
                            self.files[name] = base64.b64encode(raw).decode("ascii")
                    finally:
                        os.close(child)

        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            read(fd)
        finally:
            os.close(fd)
        self.files = dict(sorted(self.files.items()))
        for name, encoded in sorted(self.files.items()):
            path = Path(name)
            if path.parent.as_posix() != "agents" or path.suffix not in {".json", ".md"}:
                continue
            identifier = path.stem
            if not IDENTIFIER.fullmatch(identifier) or identifier == "service-default":
                raise ValueError("invalid or reserved native agent filename")
            if identifier in self.agents:
                raise ValueError("duplicate JSON/Markdown agent identifier")
            config = parse_agent(base64.b64decode(encoded), path.suffix)
            if config.get("name", identifier) != identifier:
                raise ValueError("native agent name must match its filename")
            config["name"] = identifier
            if config.get("prompt", "").startswith("file://"):
                reference = self.reference(config["prompt"], "agents")
                if reference not in self.files:
                    raise ValueError("agent prompt file is missing")
                config["prompt"] = base64.b64decode(self.files[reference]).decode("utf-8")
            for resource in config.get("resources", []):
                pattern = self.reference(resource)
                if not any(
                    fnmatch.fnmatch(name, pattern) or name.startswith(pattern + "/")
                    for name in self.files
                ) and not any(c in pattern for c in "*?["):
                    raise ValueError("agent resource file or directory is missing")
            self.agents[identifier] = config
        self.digest = hashlib.sha256(bounded_json(self.files).encode()).hexdigest()

    @staticmethod
    def reference(uri: str, base: str = "") -> str:
        if not isinstance(uri, str) or not uri.startswith(("file://", "skill://")):
            raise ValueError("only local file:// and skill:// resources are supported")
        path = uri.split("://", 1)[1]
        result = posixpath.normpath(posixpath.join(base, path))
        if not base and result.startswith(".kiro/"):
            result = result[len(".kiro/") :]
        if (
            path.startswith("/")
            or "\\" in path
            or result == ".."
            or result.startswith("../")
            or result.split("/", 1)[0] not in {"agents", "resources", "skills", "steering"}
        ):
            raise ValueError("agent references must remain inside the external configuration root")
        return result

    def snapshot(self, identifier: str | None) -> dict[str, Any]:
        if identifier is None:
            return {
                "id": "service-default",
                "config": {"name": "service-default", "tools": []},
                "files": {},
                "digest": hashlib.sha256(b"").hexdigest(),
            }
        config = copy.deepcopy(self.agents[identifier])
        selected: dict[str, str] = {}
        for uri in config.get("resources", []):
            pattern = self.reference(uri)
            for name, encoded in self.files.items():
                if fnmatch.fnmatch(name, pattern) or name.startswith(pattern + "/"):
                    if name.split("/", 1)[0] == "agents":
                        raise ValueError("resources cannot expose agent definition files")
                    selected[name] = encoded
                    if uri.startswith("skill://"):
                        parent = posixpath.dirname(name) + "/"
                        selected.update(
                            {p: raw for p, raw in self.files.items() if p.startswith(parent)}
                        )
        digest = hashlib.sha256(
            bounded_json({"config": config, "files": selected}).encode()
        ).hexdigest()
        return {"id": identifier, "config": config, "files": selected, "digest": digest}


def materialize(bundle: dict[str, Any], workspace: Path) -> dict[str, Any]:
    config_root = workspace / ".kiro"
    config_root.mkdir(mode=0o700)
    for name, encoded in bundle["files"].items():
        # Catalog snapshots contain only validated regular paths. Recheck persisted paths as well.
        AgentCatalog.reference("file://" + name)
        path = config_root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(base64.b64decode(encoded, validate=True))
        path.chmod(0o600)
    # Only the selected normalized agent is exposed to Kiro; other consumers' agents are absent.
    for path in (config_root / "agents").glob("*"):
        if path.is_file():
            path.unlink()
    config: dict[str, Any] = copy.deepcopy(bundle["config"])
    resources = []
    for uri in config.get("resources", []):
        scheme = uri.split("://", 1)[0]
        value = scheme + "://" + str(config_root / AgentCatalog.reference(uri))
        resources.append(value)
    config["resources"] = resources
    config["includeMcpJson"] = False
    config["includePowers"] = False
    return config

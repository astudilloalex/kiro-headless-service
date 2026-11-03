import json
from typing import Any, Literal, Self

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator


def bounded_json(value: Any) -> str:
    """JSON compatible with PostgreSQL, with finite numbers and bounded nesting."""

    def inspect(item: Any, depth: int = 0) -> None:
        if depth > 64:
            raise ValueError("JSON nesting exceeds 64 levels")
        if isinstance(item, str) and "\x00" in item:
            raise ValueError("NUL characters are not supported")
        if isinstance(item, dict):
            for key, child in item.items():
                inspect(key, depth + 1)
                inspect(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                inspect(child, depth + 1)

    inspect(value)
    result = json.dumps(value, ensure_ascii=False, allow_nan=False)
    result.encode("utf-8")
    return result


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResponseFormat(StrictModel):
    type: Literal["text", "json", "json_schema"] = "text"
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")

    @model_validator(mode="after")
    def validate_schema(self) -> Self:
        if (self.type == "json_schema") != (self.schema_ is not None):
            raise ValueError("schema is required only for json_schema")
        if self.schema_ is not None:
            if len(bounded_json(self.schema_)) > 32768:
                raise ValueError("schema exceeds 32768 characters")
            try:
                Draft202012Validator.check_schema(self.schema_)
            except (SchemaError, RecursionError) as exc:
                raise ValueError("invalid JSON Schema") from exc

            def check(value: Any) -> None:
                if isinstance(value, dict):
                    for key, child in value.items():
                        if (
                            key == "pattern" and isinstance(child, str)
                        ) or key == "patternProperties":
                            raise ValueError("regular-expression schema keywords are not supported")
                        if key in {"$ref", "$dynamicRef"} and (
                            not isinstance(child, str) or not child.startswith("#")
                        ):
                            raise ValueError("only local JSON Schema references are supported")
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)

            check(self.schema_)
        return self


class Limits(StrictModel):
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    output_bytes: int | None = Field(default=None, ge=128, le=8388608)


class JobRequest(StrictModel):
    input: str | dict[str, Any]
    profile: str = Field(default="default", min_length=1, max_length=64)
    agent: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    mcp_servers: list[str] = Field(default_factory=list, max_length=16)
    response_format: ResponseFormat | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    callback: str | None = Field(default=None, max_length=2048)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if not self.input or len(bounded_json(self.input).encode()) > 131072:
            raise ValueError("input must be nonempty and at most 128 KiB")
        if len(bounded_json(self.metadata)) > 4096:
            raise ValueError("metadata exceeds 4096 characters")
        if len(self.mcp_servers) != len(set(self.mcp_servers)):
            raise ValueError("duplicate MCP identifiers")
        return self


class SessionRequest(StrictModel):
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(bounded_json(self.metadata)) > 4096:
            raise ValueError("metadata exceeds 4096 characters")
        return self

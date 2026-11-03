import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text

from kiro_headless_service.api.app import create_app
from kiro_headless_service.composition import worker
from kiro_headless_service.domain.model import RuntimeFailure
from kiro_headless_service.infrastructure.bootstrap import DEFAULT_CACHE, prepare_cli
from kiro_headless_service.infrastructure.config import Policy, load_settings, secret
from kiro_headless_service.infrastructure.release import RELEASE
from kiro_headless_service.infrastructure.runtime import KiroRuntime
from kiro_headless_service.infrastructure.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent API for durable Kiro execution")
    parser.add_argument(
        "mode",
        choices=[
            "api",
            "worker",
            "migrate",
            "diagnose",
            "openapi",
            "cleanup",
            "runtime-info",
            "prepare-runtime",
        ],
    )
    parser.add_argument("--component", choices=["all", "runner", "dispatcher"], default="all")
    parser.add_argument("--archive", type=Path, help="Official CLI archive for offline preparation")
    parser.add_argument("--cache-dir", help="Absolute runtime cache directory")
    args = parser.parse_args()
    try:
        if args.mode in {"runtime-info", "prepare-runtime"}:
            cli_path = prepare_cli(
                "offline"
                if args.archive
                else (
                    "download"
                    if args.mode == "prepare-runtime"
                    else os.environ.get("KHS_CLI_BOOTSTRAP", "installed")
                ),
                args.cache_dir or os.environ.get("KHS_CLI_CACHE_DIR", DEFAULT_CACHE),
                os.environ.get("KHS_CLI_PATH", "/opt/kiro/bin/kiro-cli"),
                args.archive,
            )
            with tempfile.TemporaryDirectory(prefix="khs-runtime-info-") as directory:
                asyncio.run(
                    KiroRuntime(cli_path, directory).check(
                        RELEASE["version"], "env:KIRO_API_KEY", require_key=False
                    )
                )
            print(
                json.dumps(
                    {
                        "cli_version": RELEASE["version"],
                        "agent_engine": RELEASE["engine"],
                        "verified": "version_and_v3_help_no_inference",
                    }
                )
            )
            return
        if args.mode == "migrate":
            config = Config(os.environ.get("KHS_ALEMBIC_CONFIG", "alembic.ini"))
            command.upgrade(config, "head")
            return
        settings = load_settings()
        if args.mode == "api":
            uvicorn.run(
                "kiro_headless_service.composition:api",
                factory=True,
                host=settings.host,
                port=settings.port,
                access_log=False,
                log_config=None,
            )
        elif args.mode == "worker":
            asyncio.run(worker(settings, args.component))
        elif args.mode == "openapi":
            print(json.dumps(create_app(settings).openapi(), indent=2))
        elif args.mode == "cleanup":
            print(json.dumps({"deleted_jobs": Store(settings).cleanup()}))
        else:
            report: dict[str, object] = {
                "configuration": "valid",
                "engine": settings.engine,
                "cli_expected": settings.cli_version,
                "agent_engine": settings.agent_engine,
                "agents": sorted(settings.agents),
            }
            try:
                Policy(settings)
                report["api_credentials"] = "available"
            except (OSError, ValueError):
                report["api_credentials"] = "unavailable"
            store = Store(settings)
            try:
                with store.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    report["migration"] = conn.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                report["database"] = "reachable"
            except Exception:
                report["database"] = "unavailable_or_unmigrated"
            report["runner_ready"] = store.ready(settings.fingerprint())
            if settings.engine == "kiro":
                Path(settings.temp_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    asyncio.run(
                        KiroRuntime(
                            prepare_cli(
                                settings.cli_bootstrap, settings.cli_cache_dir, settings.cli_path
                            ),
                            settings.temp_dir,
                        ).check(
                            settings.cli_version,
                            settings.kiro_key_ref,
                            require_key=False,
                        )
                    )
                    report["kiro"] = "version_verified_no_inference"
                except RuntimeFailure as exc:
                    report["kiro"] = exc.code
                try:
                    secret(settings.kiro_key_ref)
                    report["kiro_credential"] = "available_not_authenticated"
                except (ValueError, OSError):
                    report["kiro_credential"] = "KIRO_CREDENTIAL_MISSING"
            print(json.dumps(report, indent=2))
            store.engine.dispose()
            if (
                report.get("database") != "reachable"
                or report.get("api_credentials") != "available"
                or str(report.get("kiro", "ok")).startswith(("KIRO_", "RUNTIME_"))
                or report.get("kiro_credential") == "KIRO_CREDENTIAL_MISSING"
            ):
                sys.exit(1)
    except RuntimeFailure as exc:
        print("Startup failed: " + exc.code, file=sys.stderr)
        sys.exit(1)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"]) or "configuration"
            print(f"Invalid configuration at {field}: {error['msg']}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError):
        # Configuration validation values can contain credentials; suppress raw exception text.
        print(
            "Startup failed: invalid configuration, unavailable secret or unsupported runtime. "
            "Run diagnose and check configuration references.",
            file=sys.stderr,
        )
        sys.exit(1)

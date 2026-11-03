import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI

from kiro_headless_service.api.app import create_app
from kiro_headless_service.application.worker import Dispatcher, Runner
from kiro_headless_service.infrastructure.bootstrap import prepare_cli
from kiro_headless_service.infrastructure.config import Settings, load_settings
from kiro_headless_service.infrastructure.logging import configure_logging
from kiro_headless_service.infrastructure.notifications import WebhookSender
from kiro_headless_service.infrastructure.runtime import KiroRuntime, RuntimeRouter
from kiro_headless_service.infrastructure.store import Store

logger = logging.getLogger(__name__)


def api() -> FastAPI:
    settings = load_settings()
    configure_logging(settings.log_level)
    return create_app(settings)


async def worker(
    settings: Settings, component: Literal["all", "runner", "dispatcher"] = "all"
) -> None:
    configure_logging(settings.log_level)
    store = Store(settings)
    cli_path = settings.cli_path
    if component != "dispatcher" and settings.engine == "kiro":
        cli_path = await asyncio.to_thread(
            prepare_cli, settings.cli_bootstrap, settings.cli_cache_dir, settings.cli_path
        )
    kiro = KiroRuntime(cli_path, settings.temp_dir)
    runtime = RuntimeRouter(kiro)
    await asyncio.to_thread(Path(settings.temp_dir).mkdir, parents=True, exist_ok=True, mode=0o700)
    if component != "dispatcher" and settings.engine == "kiro":
        await kiro.check(settings.cli_version, settings.kiro_key_ref)
    runner = Runner(store, runtime, settings.lease_seconds)
    dispatcher = Dispatcher(
        store, WebhookSender(settings.callback_urls, settings.callback_private_cidrs)
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def repeat(role: str) -> None:
        while not stop.is_set():
            try:
                action = runner.once if role == "runner" else dispatcher.once
                busy = await action()
            except Exception:
                logger.error("worker_iteration_failed")
                busy = False
            if not busy:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)

    async def maintenance() -> None:
        while not stop.is_set():
            try:
                if component != "dispatcher":
                    if settings.engine == "kiro":
                        await kiro.check(settings.cli_version, settings.kiro_key_ref)
                    await asyncio.to_thread(
                        store.heartbeat, "runner:" + runner.id, settings.fingerprint()
                    )
                if component != "runner":
                    await asyncio.to_thread(
                        store.heartbeat, "dispatcher:" + runner.id, settings.fingerprint()
                    )
                await asyncio.to_thread(store.cleanup)
            except Exception:
                logger.error("worker_maintenance_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.lease_seconds / 3)

    tasks = [asyncio.create_task(maintenance())]
    if component != "dispatcher":
        tasks += [asyncio.create_task(repeat("runner")) for _ in range(settings.max_running)]
    if component != "runner":
        tasks.append(asyncio.create_task(repeat("dispatcher")))
    try:
        await stop.wait()
        _, pending = await asyncio.wait(tasks, timeout=10)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        store.engine.dispose()

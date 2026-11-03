import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path

from kiro_headless_service.domain.model import RuntimeFailure


async def terminate(process: asyncio.subprocess.Process) -> None:
    """Kill the original POSIX process group, including children after the parent exits."""

    async def discard(reader: asyncio.StreamReader | None) -> None:
        if reader is not None:
            while await reader.read(8192):
                pass

    drains = [asyncio.create_task(discard(reader)) for reader in (process.stdout, process.stderr)]
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except TimeoutError:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()
    await asyncio.gather(*drains)


async def supervise(  # noqa: ASYNC109 -- timeout covers concurrent pipe drains and process exit
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: bytes,
    on_line: Callable[[bytes], Awaitable[None]],
    output_limit: int,
    timeout: float,
    on_stderr: Callable[[bytes], None] | None = None,
) -> int:
    if os.name != "posix":
        raise RuntimeFailure("CAPABILITY_UNAVAILABLE_PROCESS_GROUPS")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=output_limit + 65536,
        )
    except OSError as exc:
        raise RuntimeFailure("RUNTIME_LAUNCH_FAILED", safe_to_retry=True) from exc

    async def write() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(stdin)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    async def read_stdout() -> None:
        assert process.stdout is not None
        count = 0
        while True:
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError) as exc:
                raise RuntimeFailure("RUNTIME_OUTPUT_LIMIT") from exc
            if not line:
                return
            count += len(line)
            if count > output_limit * 3 + 65536:
                raise RuntimeFailure("RUNTIME_OUTPUT_LIMIT")
            if not line.endswith(b"\n"):
                raise RuntimeFailure("RUNTIME_PROTOCOL_INCOMPLETE")
            await on_line(line)

    async def read_stderr() -> None:
        assert process.stderr is not None
        count = 0
        while chunk := await process.stderr.read(8192):
            count += len(chunk)
            # Provider stderr is deliberately discarded: it can contain prompts and secrets.
            if count > 262144:
                raise RuntimeFailure("RUNTIME_STDERR_LIMIT")
            if on_stderr:
                on_stderr(chunk)

    tasks = [asyncio.create_task(coro()) for coro in (write, read_stdout, read_stderr)]
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*tasks)
            return await process.wait()
    except TimeoutError as exc:
        raise RuntimeFailure("EXECUTION_TIMED_OUT") from exc
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await terminate(process)

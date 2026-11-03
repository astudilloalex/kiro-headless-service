# Verification ledger

Current implementation verified on 2026-11-03 (America/Guayaquil), Linux amd64. The release
manifest fixes **Kiro CLI 2.37.1 / engine V3**. Earlier V2 and runtime-version-selection designs
have been superseded. No KIRO_API_KEY was available; provider inference tests were not enabled.

| Check | Actual result |
|---|---|
| Ruff format/check across repository | Passed, 54 formatted Python files |
| Strict mypy | Passed, 23 source files |
| Frozen dependency lock | Passed with uv lock --check --offline, 46 packages |
| Full suite with PostgreSQL 17 | **119 passed, 1 skipped** (explicit provider smoke), 6.13 seconds |
| Alembic check | Passed; no model/schema drift. Initial upgrade/downgrade/re-upgrade previously passed; this migration changes no database schema |
| Separate API/worker source HTTP smoke | Passed before and after restart: no-agent job, JSON/Markdown native-agent jobs and persisted four-message conversation, explicitly simulated inference |
| Python wheel and source distribution | Built with uv build --no-sources --offline; release JSON present in both; isolated wheel resource import returns 2.37.1/v3 |
| OpenAPI JSON versus implemented routes | Passed via scripts/check_openapi.py |
| Compose configurations | Base/external PostgreSQL variants parse, each with and without the rootless Podman overlay |
| Workflow YAML, shell syntax and local documentation links | Parsed/checked successfully; parsing does not execute hosted Actions or an OCI engine |
| Official Kiro amd64 archive | Downloaded, size/SHA-256 verified against official release metadata, selected companion binaries extracted |
| Actual Kiro version/help | 2.37.1 verified; V3/headless/stream-json/trust-workspace/required-MCP flags checked by scripts/check_cli.py and kiro-service runtime-info |
| Actual Kiro isolated settings | chat.enableKnowledge=false and chat.enableCodeIntelligence=false written/read through the official CLI in a temporary HOME |
| Actual native-agent validation | Signed-out agent validate probe returned login-required; **not a successful validation** |
| Real provider authentication/inference/MCP | Not verified; no valid credential. Executable-double success does not verify provider wire compatibility |
| Docker image build/startup | Not executed: Docker daemon socket access denied; sudo fallback blocked by no-new-privileges |
| Rootless Podman image build/startup | Not executed: newuidmap cannot create the user namespace (Operation not permitted) |
| Native arm64 Kiro execution | Not executed locally; official metadata pinned and native arm64 build/runtime-info job prepared in CI |
| GitHub-hosted CI | Prepared, not run remotely during implementation |
| GHCR publication / anonymous pull | Not performed. Complete CLI-image publication is opt-in and requires a private target package |

The final suite covers strict input, schema validation, ownership, idempotency, shared quotas,
leases/fencing, conservative retries, ordered chat, retention, SSE, artifacts, webhook outbox
atomicity, repeated notification IDs and destination restrictions. Native-agent cases cover
JSON/Markdown parsing, unsafe/duplicate YAML, file limits, link/path rejection, prompt/resources,
immutable snapshots, profile tool ceilings, required native MCP and secret references. The
publication guard rejects public/internal packages and failed visibility checks.

Subprocess tests use an executable double and exercise the real process supervisor, V3
arguments/configuration, required MCP handshake/tool calls, malformed/partial/excessive output,
timeouts, cancellation, child-process cleanup and files. One earlier sandboxed subset run timed
out waiting for an exited test process; the final full suite passed outside that sandbox. No
skip or production workaround was introduced for that transient failure.

The test client emits one Starlette/httpx deprecation warning. It was not suppressed.

## Repeat the checks

Use a **dedicated disposable database**: the integration fixture truncates service tables.

```sh
uv sync --frozen
export KHS_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost/kiro_test'
export TEST_DATABASE_URL="$KHS_DATABASE_URL"
uv run alembic upgrade head
uv run alembic check
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
uv run python scripts/check_openapi.py
uv build --no-sources
uv run python scripts/check_distribution.py
```

With a working rootless Podman host, CI builds the default complete image, verifies its CLI,
then runs scripts/podman_smoke.sh against API/worker/PostgreSQL and restarts. The Docker job
does the equivalent simulated application smoke. The native arm64 job executes the actual CLI
version/help inside the image; Python compilation alone is not treated as platform support.
These hosted jobs still need to run successfully; local source checks do not replace them.

Real inference remains opt-in:

```sh
RUN_KIRO_SMOKE=1 KHS_CLI_PATH=/path/to/kiro-cli uv run pytest -q -m kiro
```

Provide KIRO_API_KEY separately. This consumes provider inference when credentials are valid.
Verify successful stream completion, real MCP usage and target-platform execution before
claiming those provider capabilities have been demonstrated. See
[compatibility](kiro-compatibility.md) and [publishing](publishing.md) for evidence and release
constraints.

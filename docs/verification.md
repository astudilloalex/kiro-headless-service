# Verification ledger

Current implementation verified on 2026-11-03 (America/Guayaquil), Linux amd64. The release
manifest fixes **Kiro CLI 2.37.1 / engine V3**. Earlier V2 and runtime-version-selection designs
have been superseded. No KIRO_API_KEY was available; provider inference tests were not enabled.

| Check | Actual result |
|---|---|
| Ruff format/check across repository | Passed, 58 formatted Python files |
| Strict mypy | Passed, 25 source files |
| Frozen dependency lock | Passed with uv lock --check --offline, 46 packages |
| Full suite with PostgreSQL 17 | **136 passed, 1 skipped** (explicit provider smoke), 6.75 seconds |
| Alembic check | Passed; no model/schema drift. Initial upgrade/downgrade/re-upgrade previously passed; this migration changes no database schema |
| Separate API/worker source HTTP smoke | Passed before and after restart: no-agent job, JSON/Markdown native-agent jobs and persisted four-message conversation, explicitly simulated inference |
| Python wheel and source distribution | Built with uv build --no-sources --offline; release JSON present in both; isolated wheel resource import returns 2.37.1/v3 |
| OpenAPI JSON versus implemented routes | Passed via scripts/check_openapi.py |
| Compose configurations | Base/external PostgreSQL variants, with and without the Podman overlay, produce one valid /tmp tmpfs mount per service; read-only mounts and rootless UID mapping checked |
| Workflow YAML, shell syntax and local documentation links | Parsed/checked successfully; parsing does not execute hosted Actions or an OCI engine |
| Official Kiro amd64 archive | Downloaded, size/SHA-256 verified against official release metadata, selected companion binaries extracted |
| Actual Kiro version/help | 2.37.1 verified; V3/headless/stream-json/trust-workspace/required-MCP flags checked by scripts/check_cli.py and kiro-service runtime-info |
| Actual Kiro isolated settings | chat.enableKnowledge=false and chat.enableCodeIntelligence=false written/read through the official CLI in a temporary HOME |
| Actual native-agent validation | Signed-out agent validate probe returned login-required; **not a successful validation** |
| Real provider authentication/inference/MCP | Not verified; no valid credential. Executable-double success does not verify provider wire compatibility |
| Docker image build/startup | Not executed: Docker daemon socket access denied; sudo fallback blocked by no-new-privileges |
| Rootless Podman image build/startup | **Passed**: public bootstrap amd64 image build, first official CLI download/checksum verification, PostgreSQL/migrations/API/worker, native-agent jobs, UID 10001, restart, cleanup and no-network/read-only CLI-cache verification; inference explicitly simulated |
| Native arm64 Kiro execution | Not executed locally; official metadata pinned and native arm64 build/runtime-info job prepared in CI |
| GitHub-hosted CI | User supplied failing Docker/Podman logs; fixes tested locally. No hosted rerun dispatched during this repair |
| GHCR publication / anonymous pull | Existing public v1.0.0 queried anonymously, pulled and inspected offline: no CLI in /opt or PATH. No new image publication or visibility change performed |

The final suite covers strict input, schema validation, ownership, idempotency, shared quotas,
leases/fencing, conservative retries, ordered chat, retention, SSE, artifacts, webhook outbox
atomicity, repeated notification IDs and destination restrictions. Native-agent cases cover
JSON/Markdown parsing, unsafe/duplicate YAML, file limits, link/path rejection, prompt/resources,
immutable snapshots, profile tool ceilings, required native MCP and secret references. The
publication tests require the installer-based target and anonymous pull verification.

Subprocess tests use an executable double and exercise the real process supervisor, V3
arguments/configuration, required MCP handshake/tool calls, malformed/partial/excessive output,
timeouts, cancellation, child-process cleanup and files. One earlier sandboxed subset run timed
out waiting for an exited test process; the final full suite passed outside that sandbox. No
skip or production workaround was introduced for that transient failure.

The test client emits one Starlette/httpx deprecation warning. It was not suppressed.

## CI startup regression repair

User-supplied CI logs exposed two deployment-test bugs:

- The unquoted YAML flow list for tmpfs split its comma-separated options into separate
  mounts. Docker rejected size=512m as a nonabsolute mount path. Both Compose files now use
  one quoted entry. docker compose config alone had accepted the malformed list; new tests
  check the interpreted mounts as well as the read-only root filesystem.
- Podman's port could accept a connection and reset/close it before the API finished startup.
  The readiness probe now retries connection resets, refusal, disconnects, timeouts and HTTP
  503 within a bounded deadline. HTTP 401/403/404/500 still fail immediately. Job-submission
  requests are not retried. Each job has a separate completion deadline.

Fourteen regression cases include an actual HTTP server that closes its first connection,
then returns 503, then succeeds. PostgreSQL readiness now probes TCP to avoid accepting the
entrypoint's temporary Unix-socket-only initialization server. Docker and Podman report
container state/logs before cleaning up failed checks; their output does not inspect secret
environment values. The repaired scripts/podman_smoke.sh passed against the built complete
image, including a restart and credential-free actual Kiro version/help checks. The temporary
containers/network/database volume were cleaned up.

Docker daemon access remains denied locally, so the Docker-engine run and hosted CI rerun
remain unverified. The Compose-generated Docker configuration was checked structurally for
all four deployment combinations. No provider inference or publication was performed.

## Public installer image and restricted-server support

The public default target now ships the installer and pinned manifest, with no CLI binary.
Users chose download-on-start after reviewing the standard redistribution restrictions.
The worker acquires/verifies its exact runtime on the deployer's machine. An imported cache
supports servers that cannot reach registries/download sites but can reach Kiro/AWS.
The earlier private preinstalled-image workflow was removed; its access restriction did not
itself grant redistribution rights.

- First acquisition and immutable cache reuse are tested; checksum failures never publish a
  partial runtime. Cache corruption/symlinks fail explicitly without automatic replacement.
- Offline missing-cache checks perform no network access and create no cache directory.
- Prepared-cache reuse performs no writes and passed inside an actual container with
  --network=none, read-only root and a read-only cache volume.
- The new public bootstrap image built and passed scripts/podman_smoke.sh end-to-end locally.
- Base/external PostgreSQL Compose variants and their rootless/offline overlays were checked
  structurally. The offline overlay selects offline mode and a read-only external cache.
- Explicit proxy env:/file: references reach the actual executable double; resolved proxy
  credentials stay out of snapshots and result echoes are rejected. Real proxy/private-route
  inference remains unverified without KIRO_API_KEY.
- After the final proxy-password component redaction adjustment, the installer/bootstrap/
  publication/runtime subset passed again: 38 tests. The full regression above preceded that
  narrow adjustment; unrelated database behavior did not change.
- Wheel/sdist built after this change and contain the immutable release manifest.

The old published v1.0.0 remains unchanged. Its anonymous GHCR metadata reports kiro-service api
as the default command; running that artifact offline confirmed that /opt/kiro/bin/kiro-cli
does not exist and kiro-cli is absent from PATH. A new release is needed for the bootstrap
behavior. The publication workflow verifies a genuinely anonymous pull using fresh Docker
credentials; it was not dispatched here.

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

With a working rootless Podman host, CI builds the default bootstrap image, prepares its CLI,
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

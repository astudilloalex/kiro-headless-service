# Implementation progress

## Current release: CI-owned Kiro CLI 2.37.1 / V3

1. [done] Verify official release metadata, amd64 binary/help, V3 interface and license terms.
2. [done] Package immutable release metadata; reject runtime version/engine overrides.
3. [done] Load external native JSON/Markdown agents, bounded resources and immutable job snapshots.
4. [done] Execute isolated V3 agents with native permissions and explicitly authorized MCP.
5. [done] Prepare complete-image CI builds, native arm64 checks and rootless Podman deployment.
6. [done] Preserve public service-only publishing; prepare opt-in private complete-image publishing.
7. [done] Update examples/documentation and execute all locally available verification.

## Implemented foundation

FastAPI v1 API with separate worker/dispatcher, PostgreSQL durable jobs and leases/fencing,
idempotency, ownership, shared quotas, ordered persistent conversations, validated results,
events/SSE, bounded database artifacts, transactional notification outbox and retention.
Domain/application ports remain independent of Kiro commands and SQLAlchemy. No graphical UI.

## Configuration contract

- CI owns src/kiro_headless_service/kiro-release.json: version 2.37.1, engine v3, official archive
  hashes/sizes for amd64/arm64. Deployers choose an image tag/digest.
- KHS_KIRO_CONFIG_HOST_DIR selects the external host directory mounted read-only at /config/kiro.
  Its agents/ folder contains native JSON/Markdown definitions. KHS_KIRO_CONFIG_DIR is the path
  the service reads (absolute host path for source execution).
- Consumer agent IDs remain explicit allowlists in service.json. No custom agent is required
  for a basic job. Restart API/workers after configuration changes; accepted jobs keep snapshots.
- Fresh HOME/KIRO_HOME/workspace per run, explicit --agent-engine v3, no implicit global
  MCP/Powers/resources. Native hooks, sub-agents and knowledge indexing are unsupported.
- Rootless Podman maps the invoking user to service UID/GID 10001; shared read-only mounts use
  SELinux :z. No privileged service container, engine socket or recursive ownership changes.
- Default Dockerfile target includes Kiro. Public publication explicitly selects service-only
  target; complete-image publishing is manual/opt-in to a private package under applicable terms.

## Verification

- Final regression: 119 passed, 1 skipped with temporary PostgreSQL; provider smoke omitted.
- Ruff format/check, strict mypy, frozen lock, OpenAPI and Alembic checks passed.
- Wheel/sdist built; release JSON present and readable from an isolated wheel import.
- API/worker source smoke passed, including native JSON/Markdown agents and chat after restart.
- Four Compose combinations parse; shell/workflow syntax and local documentation links checked.
- Official amd64 CLI 2.37.1 version/V3 help/settings verified without inference. Signed-out
  native-agent validation required login and was not counted as success.
- Docker socket denied and rootless Podman user namespace creation denied by this environment.
  OCI builds/startup, real inference/MCP and native arm64 execution remain for the prepared CI
  and credentialed smoke checks. No remote Actions run, image publication or pull was performed.

Full evidence and repeat commands: docs/verification.md. No code implementation milestone
remains open; unavailable external verification is recorded explicitly.

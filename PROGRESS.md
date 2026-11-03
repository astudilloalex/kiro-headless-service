# Implementation progress

## Current direction: public bootstrap image, Kiro CLI 2.37.1 / V3

1. [done] Pin official release metadata and verify amd64 CLI version/V3 help.
2. [done] Native external JSON/Markdown agents, immutable resource snapshots and isolated runs.
3. [done] Repair CI tmpfs YAML/readiness races and preserve failure diagnostics.
4. [done] Public image carries an installer/manifest; it acquires the CLI on the deployer's machine.
5. [done] Verified cache by version/architecture/archive hash, serialized atomic preparation,
   explicit offline mode and offline archive preparation.
6. [done] Rootless Podman volumes/UID mapping, offline cache overlay, proxy references and docs.
7. [done] Publish runtime target with anonymous-pull verification; remove private binary-image workflow.
8. [done] Available local verification; external provider/hosted checks recorded as unavailable.

## User decisions

- The service/image must be publicly downloadable without registry login.
- User has no separate redistribution authorization and selected downloading Kiro from its
  official origin at installation/startup. A private package does not itself grant rights.
- Restricted server cannot fetch installation artifacts but can reach Kiro/AWS through its
  approved network/proxy. Ship OCI archives and a prepared cache; inference still reaches Kiro.
- CI owns the release manifest/version/engine. Runtime cannot select another CLI version.
- Custom agents remain in a user-owned read-only external directory; no custom agent is
  required for ordinary requests. Restart API/workers after configuration changes.

## Implemented foundation

FastAPI v1 API, separate worker/dispatcher, PostgreSQL jobs/leases/fencing/idempotency/quotas,
ordered persistent chat, validated results, events/SSE, bounded artifacts and webhook outbox.
Domain/application remain independent of Kiro commands and SQLAlchemy. No graphical UI.

## Verification

- Full regression with temporary PostgreSQL: 136 passed, 1 skipped (real provider inference).
- Ruff (58 Python files), strict mypy (25 source files), OpenAPI, Alembic and wheel/sdist passed.
- Public bootstrap image built. Rootless Podman smoke passed: verified initial download,
  API/worker/PostgreSQL/native-agent jobs, restart, UID 10001 and cache reuse without network
  using a read-only cache/root. Application inference was explicitly simulated.
- Existing public GHCR v1.0.0 was anonymously queried/pulled and run offline: service-only,
  default kiro-service api, no CLI installed. It remains unchanged; use a new release tag.
- Proxy references tested with an actual subprocess double, including secret-echo rejection.
- Docker daemon access is denied locally. Hosted CI, native arm64 execution and credentialed
  provider/proxy/private-endpoint inference were not performed. No publication/visibility
  change was performed. Full ledger: docs/verification.md.

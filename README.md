# kiro-headless-service

An independent, self-hosted HTTP API for Kiro: durable jobs, optional native V3 agents,
persistent conversations, administrator-approved MCP servers, validated results, artifacts,
events/SSE and durable webhooks. There is no frontend or graphical documentation.
This is not an official Kiro product and does not expose every Kiro capability automatically.

**This release uses Kiro CLI 2.37.1 and engine V3. CI builds the deployment image with that CLI
inside. Deployers select an image tag/digest; they configure their own agents and credentials.**
The sole release manifest is [kiro-release.json](src/kiro_headless_service/kiro-release.json).
Runtime `KHS_CLI_VERSION` / `KHS_AGENT_ENGINE` overrides and inline `agents` definitions are
rejected. Updating Kiro means updating the release manifest, testing and producing a new image.

## Architecture

```mermaid
flowchart LR
    Consumer --> API[FastAPI v1]
    API --> Application[Application contracts and ports]
    Application --> Domain[Framework-free domain]
    Application --> PG[(PostgreSQL)]
    Runner[Separate Runner] --> PG
    Runner --> Kiro[Kiro 2.37.1 / V3 subprocess]
    Files[External native agent configuration] --> Application
    Kiro --> MCP[Approved MCP servers]
    Dispatcher[Independent Dispatcher] --> PG
    Dispatcher --> Webhook[Authorized webhook]
```

Code lives in `src/kiro_headless_service/`: `domain`, `application`, `infrastructure`, `api`,
with composition in `composition.py`. The domain imports no FastAPI, SQLAlchemy or Kiro adapter.
API and worker are separate processes. PostgreSQL stores jobs, queue reservations, sessions,
messages, events, bounded artifact bytes and notification outbox. No transaction spans inference.
Redis, Celery, Kafka, Kubernetes and business-specific integrations are not required.

## Where to put your V3 agents

Create a configuration directory on your machine:

```text
my-installation/
├── .env
├── service.json
└── kiro/
    ├── agents/
    │   ├── concise.json
    │   └── reviewer.md
    ├── resources/
    │   └── review-guide.md
    ├── skills/           # optional, explicitly referenced
    └── steering/         # optional, explicitly referenced
```

**Your native agent files go in `kiro/agents/`.** In Compose set
`KHS_KIRO_CONFIG_HOST_DIR=./kiro`; it is mounted read-only at `/config/kiro`. The service sees
that root through `KHS_KIRO_CONFIG_DIR`. For source execution set the latter to an absolute
host path. The source directory can be anywhere the deployer chooses; HTTP consumers cannot
supply arbitrary host paths.

Start with the native JSON/Markdown files in [examples/kiro](examples/kiro). A JSON agent:

```json
{
  "name": "analyst",
  "description": "Analyzes supplied information",
  "prompt": "file://../resources/analyst.md",
  "tools": [],
  "permissions": {"rules": []},
  "includeMcpJson": false,
  "includePowers": false
}
```

Save it as `kiro/agents/analyst.json`. Put its instructions in `kiro/resources/analyst.md`.
Markdown agents use YAML frontmatter and instructions after the closing `---`.
Names must match filenames; duplicate JSON/Markdown names are rejected. Prompt file URIs are
relative to the agent file. Resource URIs are relative to the external root; the usual
`file://.kiro/steering/...` form is also accepted. References must stay within that root.

Authorize the name under the appropriate consumer in your `service.json`:

```json
"agents": ["analyst"]
```

That list is inside a consumer entry, not a top-level definition. The native files contain
the actual agent definitions. `GET /v1/agents` lists authorized IDs/descriptions only.
Supported fields include prompt, model, tools, excludedTools, permissions, resources,
welcomeMessage and explicit mcpServers. Native rules are preserved; the profile's built-in
tool allowlist is a ceiling, and profile deny rules also apply. A native model must be enabled
in the service's model list, unless an authorized request model overrides it.

This initial V3 integration explicitly rejects hooks, sub-agent delegation, broad tool selectors,
legacy V2 fields, knowledge-base indexing and global MCP/Powers inheritance. Interactive OAuth
is unsupported. These features are not silently stripped or advertised as supported. `ask`
rules cannot receive a human response during headless execution. Configure suitable
`allow`/`deny` rules instead.

Configuration is validated and captured at startup. Restart API/workers after editing it.
Accepted jobs retain an immutable bounded snapshot of their selected agent and referenced
resources, excluding resolved secrets. Their execution does not reread edited host files.
Each run gets a fresh HOME/KIRO_HOME/workspace and a normalized V3 JSON agent with the selected
name. The original mounted files remain untouched. No host login/configuration is inherited.

## Start with rootless Podman

Requirements: a working rootless Podman installation with subordinate UID/GID mappings, a
compatible Compose provider, and PostgreSQL 17 (provided in the example). Python/container/CI
versions are 3.13.13 and uv 0.12.5. The main deployer uses a CI-produced image, not a local build.

```sh
cp .env.example .env
# Set KHS_IMAGE to your CI-built deployment image tag/digest.
# Set independent SERVICE_API_TOKEN and POSTGRES_PASSWORD values, and your KIRO_API_KEY.
# Point KHS_CONFIG_FILE and KHS_KIRO_CONFIG_HOST_DIR at your own files.
podman pull IMAGE_FROM_YOUR_CI
podman compose -f compose.yaml -f compose.podman.yaml up -d --no-build
```

The image with Kiro is a separate private deployment artifact because public redistribution
rights for the proprietary CLI are not assumed. [Publishing](docs/publishing.md) explains the
public service-only image and opt-in private CLI-image workflow. Nothing was published by the
implementation agent. For a local build of the same complete image, use:

```sh
podman build -t kiro-headless-service:local .
```

Rootless Compose maps the invoking host user to container UID/GID 10001 so owner-readable
configuration/secret mounts work without chmod 777 or recursive ownership changes. Shared
read-only mounts use SELinux `:z`, because API, worker and migrations all read them. Service
containers use a read-only root filesystem, bounded writable `/tmp`, dropped capabilities,
no-new-privileges and an init reaper. No privileged mode or engine socket is needed.
PostgreSQL persists in a named volume. API is published on localhost:8000; provide your own TLS
reverse proxy for public access. [Rootless smoke](scripts/podman_smoke.sh) uses only Podman CLI.

Docker uses `docker compose up -d --no-build` with the same complete image and base Compose
file. For an external database set `KHS_DATABASE_URL` and use `compose.external.yaml` (plus
`compose.podman.yaml` for Podman). Configure PostgreSQL TLS/certificates and URL-encode passwords.
Only real target-platform runtime checks establish provider compatibility; see the ledger.

## Credentials and source execution

Credentials are separate: deployer's `KIRO_API_KEY`; independent API consumer tokens (random,
at least 24 characters); PostgreSQL credentials; optional MCP/service secrets. Registry login
may also be needed to pull a private image. One instance uses one Kiro account. Consumers
cannot submit their own Kiro keys per request. Builds/migrations/ordinary tests need no Kiro key.

```sh
uv sync --frozen
export KHS_CONFIG="$PWD/examples/service.json"
export KHS_KIRO_CONFIG_DIR="$PWD/examples/kiro"
export SERVICE_API_TOKEN="$(openssl rand -hex 32)"
export KHS_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/kiro'
export KHS_ENGINE=simulated   # explicit account-free testing
uv run kiro-service migrate
uv run kiro-service api
# In another shell with the same environment:
uv run kiro-service worker
```

For real source execution install exactly the release-manifest CLI and set `KHS_CLI_PATH`.
The included installer reads that manifest, verifies size/SHA-256 and extracts selected regular
members without executing the downloaded installer. CI performs the same operation in the
Dockerfile. No binaries or secrets are committed to the repository.

## API examples

Export SERVICE_API_TOKEN in your client shell. No custom agent is required:

```sh
curl -sS http://localhost:8000/v1/jobs \
  -H "Authorization: Bearer $SERVICE_API_TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: request-1' -d '{"input":"Explain durable jobs in one sentence."}'
# HTTP 202 and Location. Set JOB_ID from the returned id.
curl -sS "http://localhost:8000/v1/jobs/$JOB_ID" -H "Authorization: Bearer $SERVICE_API_TOKEN"
curl -sS "http://localhost:8000/v1/jobs/$JOB_ID/result" -H "Authorization: Bearer $SERVICE_API_TOKEN"
curl -sS -X POST "http://localhost:8000/v1/jobs/$JOB_ID/cancel" -H "Authorization: Bearer $SERVICE_API_TOKEN"
```

Select a native agent by authorized ID:

```sh
curl -sS http://localhost:8000/v1/jobs \
  -H "Authorization: Bearer $SERVICE_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"input":"Review this explanation of queues.","agent":"reviewer"}'
```

Optional fields: profile, agent, model, effort, mcp_servers, response_format, metadata, callback,
limits. Input is text or a JSON object. A service profile is distinct from a native Kiro agent:
it supplies instructions, deadlines, formats, tool ceilings and default-agent permissions.
Explicit models must be enabled; omission uses the agent model or provider default.

Text results have format/text/usage. Structured results have format/data/usage and accept any
complete JSON value. Request `response_format` as text, json, or json_schema with an object
schema; request values override profile defaults. Draft 2020-12 validation uses local references
only and excludes regular-expression keywords. Duplicate keys, non-finite values, fences,
commentary and schema violations fail INVALID_RESULT, without extraction or repair. Provider
JSON events and successful exit status do not establish business-result validity.

For chat, create a session and submit turns to its message resource:

```sh
curl -sS http://localhost:8000/v1/sessions \
  -H "Authorization: Bearer $SERVICE_API_TOKEN" -H 'Content-Type: application/json' -d '{}'
# Set SESSION_ID. Wait for each returned job to finish before the next turn.
curl -sS "http://localhost:8000/v1/sessions/$SESSION_ID/messages" \
  -H "Authorization: Bearer $SERVICE_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"input":"My preferred language is English."}'
curl -sS "http://localhost:8000/v1/sessions/$SESSION_ID/messages" \
  -H "Authorization: Bearer $SERVICE_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"input":"Which language did I choose?"}'
curl -sS "http://localhost:8000/v1/sessions/$SESSION_ID/messages?after=0&limit=20" \
  -H "Authorization: Bearer $SERVICE_API_TOKEN"
curl -sS -X POST "http://localhost:8000/v1/sessions/$SESSION_ID/close" \
  -H "Authorization: Bearer $SERVICE_API_TOKEN"
```

Chat uses the same jobs and PostgreSQL mutual exclusion. Continuity after restarts reconstructs
completed service messages; it is not native Kiro/ACP session recovery. Tool state, hidden
reasoning and runtime files are not retained. Failed/cancelled turns remain visible but are
excluded from context. No assistant response is fabricated and no history is silently truncated.
Limits include replayed messages, system instructions and captured resource bytes.

## MCP, events, files and notifications

The service configuration includes a local stdio echo MCP (adjust its absolute executable/script
paths when running from source). In real mode request `"mcp_servers":["echo"]`; every selected
server is required through `--require-mcp-startup`. Native agents can also declare mcpServers,
with exact `@server/tool` selectors; the consumer must be authorized for every declared server.
Secrets in native MCP env/headers use `${ENV_NAME}` references and resolve only at execution.
Native model/permissions/resources stay distinct from the consumer's untrusted input.

Only installed administrator-configured commands or remote Streamable HTTP/header-auth servers
are supported. [Remote example](examples/remote-mcp.json). No dynamic third-party installation,
interactive OAuth or trust-all flag is used. Availability does not prove a tool was invoked.
The simulated engine identifies itself and explicitly rejects MCP execution.

Poll `/v1/jobs/{jobId}/events?after=0&limit=100`, or use `?stream=true` when SSE is enabled.
Stable event IDs/version 1 support exclusive cursors and Last-Event-ID. Streams reconnect after
30 seconds and disconnection does not cancel a durable job. Text events contain byte counts;
tool events omit raw names/arguments/outputs. Fetch validated content from `/result`.
Progress is capped; lifecycle events remain. Events expire with jobs.

List `/v1/jobs/{jobId}/artifacts` and download `/artifacts/{artifactId}` with authentication.
Only bounded direct regular files in the run's artifacts/ directory are exported. Symlinks,
hardlinks, special files, unsafe names, nesting and size/count violations fail the run.
PostgreSQL stores bytes/SHA-256 behind an artifact port and checks integrity on download.

Callbacks require an exact configured URL. The transactional outbox sends a stable event ID,
terminal state, correlation ID and result reference, never the result or API credentials.
Receivers must deduplicate; delivery is at least once within retry/retention bounds. The
Dispatcher leases/retries independently of Runner. DNS is rechecked and the validated address
is pinned with original TLS SNI/hostname verification. Redirects are rejected; private networks
and HTTP need explicit CIDR authorization. Exhausted deliveries remain for operator inspection.

## Durability, configuration and operation

QUEUED/RUNNING jobs use expiring leases and random fencing tokens. Short PostgreSQL scheduler
locks enforce global/per-consumer limits. Result, artifacts, conversation completion and outbox
commit together. Lost workers become ambiguous failures without automatic inference replay.
Only confirmed pre-launch failures retry. API idempotency does not make external MCP effects
idempotent or exactly once. Reconcile ambiguous external effects before resubmitting.

Idempotency keys scope to consumer/operation and expire with resource retention. Cancellation
is checked during renewal, then terminates the process group. Shutdown stops claims, allows
ten seconds, then terminates remaining runs as ambiguous. Mixed live configuration fingerprints
make readiness fail. Drain work before release/configuration changes; old-release jobs fail
JOB_RELEASE_INCOMPATIBLE instead of silently switching engines.

Terminal job retention removes results/events/files/outbox together; inactive session retention
controls messages separately. Completed history can outlive a job with null job references.
Back up PostgreSQL and protect its consumer content. No ephemeral file is used to promise
conversation recovery. Temporary directories separate configuration, not OS permissions:
approved tools/MCP retain worker Unix privileges and may affect external state. Dedicated
containers/hosts/network policy are necessary for stronger isolation; mandatory OS sandboxing
is explicitly rejected by this adapter. Deliberately escaping children are outside process-group
containment. Never mount sensitive host directories or an engine socket.

[Configuration reference](docs/configuration.md) covers every setting and limit. Precedence is
defaults → external KHS_CONFIG JSON → KHS_* environment. `.env` is a Compose convenience.
API tokens resolve at startup; provider/MCP `env:`/`file:` references resolve at execution.
These are service settings, not invented Kiro-native variables.

`/health/live` checks liveness. `/health/ready` requires current migrations and matching Runner
heartbeat, plus Dispatcher when callbacks are configured. Version/help/key presence checks
consume no inference; valid provider authentication and MCP reachability are checked on use.
Logs are structured IDs/error codes without full content, raw exceptions or secrets.

```sh
uv run kiro-service diagnose
uv run kiro-service runtime-info  # version + V3 help; no config, DB or key required
uv run kiro-service openapi
```

OpenAPI is [openapi.json](openapi.json) and `/openapi.json`; graphical docs are disabled.
See [errors](docs/errors.md), [Python client](examples/client.py), [contributing](CONTRIBUTING.md),
[provider compatibility](docs/kiro-compatibility.md), [publishing](docs/publishing.md) and
[verification ledger](docs/verification.md). OCI startup and real provider inference are only
claimed when actually executed, not inferred from simulated tests or Python cross-compilation.

# Configuration reference

Precedence: built-in defaults → external JSON at KHS_CONFIG → KHS_* environment overrides.
Complex overrides use JSON notation. Settings are strict; unknown JSON fields are rejected.
All ordinary settings require coordinated API/worker restart. Accepted jobs keep their immutable snapshots.

| Environment (JSON key) | Purpose | Required | Default | Bounds | Restart |
|---|---|---|---|---|---|
| `KHS_DATABASE_URL` (`database_url`) | PostgreSQL SQLAlchemy URL; configure TLS/certificates and URL-encoded credentials | no | `"postgresql+psycopg://kiro@localhost:5432/kiro"` | See notes | yes |
| `KHS_DATABASE_URL_FILE` (`database_url_file`) | Mounted database URL secret file; takes precedence over database_url | no | `null` | See notes | yes |
| `KHS_ENGINE` (`engine`) | kiro or explicitly simulated; never a fallback | no | `"kiro"` | enum=['kiro', 'simulated'] | yes |
| `KHS_HOST` (`host`) | API listen address | no | `"0.0.0.0"` | See notes | yes |
| `KHS_PORT` (`port`) | API listen port | no | `8000` | minimum=1, maximum=65535 | yes |
| `KHS_CLI_PATH` (`cli_path`) | Installed CLI path used only in installed mode; cache modes derive their path from the pinned release | no | `"/opt/kiro/bin/kiro-cli"` | See notes | yes |
| `KHS_CLI_BOOTSTRAP` (`cli_bootstrap`) | installed: use cli_path; download: acquire pinned runtime if missing; offline: require prepared cache | no | source: `installed`; public image/Compose: `download` | installed/download/offline | yes |
| `KHS_CLI_CACHE_DIR` (`cli_cache_dir`) | Absolute root containing version/architecture/hash-specific runtime caches | no | source: `/tmp/kiro-service/cli`; image: `/var/lib/kiro` | absolute directory, writable only for initial preparation | yes |
| `KHS_KIRO_PROXY_ENV_REFS` (`kiro_proxy_env_refs`) | Explicit HTTP_PROXY/HTTPS_PROXY/NO_PROXY secret references passed to Kiro subprocesses | no | `{}` | env:/file: references; no other variable names | yes |
| `KHS_KIRO_CONFIG_DIR` (`kiro_config_dir`) | Absolute native configuration root with agents/, resources/, skills/, steering/ | when agents are authorized | `null` | See notes | yes |
| `KHS_MAX_AGENT_FILES` (`max_agent_files`) | Maximum regular files captured from the native configuration root | no | `128` | minimum=1, maximum=1024 | yes |
| `KHS_MAX_AGENT_FILE_BYTES` (`max_agent_file_bytes`) | Maximum bytes per native configuration file | no | `262144` | minimum=1024, maximum=1048576 | yes |
| `KHS_MAX_AGENT_BUNDLE_BYTES` (`max_agent_bundle_bytes`) | Maximum aggregate configuration bytes captured at startup | no | `2097152` | minimum=1024, maximum=8388608 | yes |
| `KHS_KIRO_KEY_REF` (`kiro_key_ref`) | Provider secret reference, resolved by real Runner only | secret available in real Runner | `"env:KIRO_API_KEY"` | See notes | yes |
| `KHS_TEMP_DIR` (`temp_dir`) | Writable root for disposable execution directories | no | `"/tmp/kiro-service"` | See notes | yes |
| `KHS_REQUIRE_OS_SANDBOX` (`require_os_sandbox`) | Mandatory OS sandbox request; true fails explicitly in this adapter | no | `false` | See notes | yes |
| `KHS_MODELS` (`models`) | Allowed explicit native-agent/request model IDs; empty permits provider default only | no | `[]` | See notes | yes |
| `KHS_CONSUMERS` (`consumers`) | Token references and profile/agent/MCP permissions with per-consumer quotas | at least one | `{}; invalid until configured` | See notes | yes |
| `KHS_PROFILES` (`profiles`) | Service instructions, tool ceilings, default-agent permission rules, result formats and deadlines | no | `default: text, no tools, empty permission rules, timeout 120s` | See notes | yes |
| `KHS_MCP_SERVERS` (`mcp_servers`) | Installed stdio or remote header-auth servers; native agents may declare explicit servers too | no | `{}` | See notes | yes |
| `KHS_CALLBACK_URLS` (`callback_urls`) | Exact allowlisted destination URLs without userinfo/query/fragment | no | `[]` | See notes | yes |
| `KHS_CALLBACK_PRIVATE_CIDRS` (`callback_private_cidrs`) | Explicit authorized nonpublic destination networks; also required for HTTP | no | `[]` | See notes | yes |
| `KHS_MAX_RUNNING` (`max_running`) | Maximum running jobs shared across workers | no | `4` | minimum=1, maximum=128 | yes |
| `KHS_MAX_QUEUED` (`max_queued`) | Maximum queued jobs shared across processes | no | `1000` | minimum=1, maximum=100000 | yes |
| `KHS_MAX_TIMEOUT_SECONDS` (`max_timeout_seconds`) | Server execution deadline ceiling | no | `300` | minimum=1, maximum=3600 | yes |
| `KHS_MAX_OUTPUT_BYTES` (`max_output_bytes`) | Maximum final UTF-8 output size | no | `1048576` | minimum=128, maximum=8388608 | yes |
| `KHS_MAX_EVENTS` (`max_events`) | Provider record limit and retained progress budget; lifecycle events are additional | no | `2000` | minimum=10, maximum=100000 | yes |
| `KHS_MAX_ARTIFACTS` (`max_artifacts`) | Maximum direct exported files | no | `10` | minimum=0, maximum=100 | yes |
| `KHS_MAX_ARTIFACT_BYTES` (`max_artifact_bytes`) | Maximum bytes per exported file | no | `1048576` | minimum=1, maximum=8388608 | yes |
| `KHS_MAX_ARTIFACTS_BYTES` (`max_artifacts_bytes`) | Maximum combined exported bytes | no | `4194304` | minimum=1, maximum=33554432 | yes |
| `KHS_MAX_HISTORY_MESSAGES` (`max_history_messages`) | Maximum stored session messages including failed turns | no | `100` | minimum=2, maximum=1000 | yes |
| `KHS_MAX_CONTEXT_BYTES` (`max_context_bytes`) | Replay prompt, instructions and captured resource byte budget | no | `262144` | minimum=1024, maximum=2097152 | yes |
| `KHS_LEASE_SECONDS` (`lease_seconds`) | Reservation duration; renewal/heartbeat every third; heartbeat expiry twice this | no | `30` | minimum=6, maximum=300 | yes |
| `KHS_MAX_ATTEMPTS` (`max_attempts`) | Total attempts; retries only for confirmed pre-launch failures | no | `2` | minimum=1, maximum=5 | yes |
| `KHS_POLL_SECONDS` (`poll_seconds`) | Idle queue/dispatcher poll interval | no | `0.5` | minimum=0.05, maximum=10 | yes |
| `KHS_RETENTION_SECONDS` (`retention_seconds`) | Terminal job/result/event/artifact/outbox retention | no | `604800` | minimum=60 | yes |
| `KHS_SESSION_RETENTION_SECONDS` (`session_retention_seconds`) | Inactive session/message retention | no | `2592000` | minimum=60 | yes |
| `KHS_NOTIFICATION_ATTEMPTS` (`notification_attempts`) | Maximum delivery leases, including ambiguous delivery crashes | no | `8` | minimum=1, maximum=30 | yes |
| `KHS_NOTIFICATION_BASE_SECONDS` (`notification_base_seconds`) | Exponential retry base; delay capped at one hour | no | `5` | minimum=1, maximum=300 | yes |
| `KHS_SSE_ENABLED` (`sse_enabled`) | Optional resumable event streams | no | `false` | See notes | yes |
| `KHS_LOG_LEVEL` (`log_level`) | Structured logging threshold; content remains omitted | no | `"INFO"` | enum=['DEBUG', 'INFO', 'WARNING', 'ERROR'] | yes |

Release metadata is not runtime configuration:

- `cli_version=2.37.1` and `agent_engine=v3` come from the packaged release manifest.
- `KHS_CLI_VERSION`, `KHS_AGENT_ENGINE`, JSON `cli_version`, and old inline JSON `agents` definitions are rejected.
- Select a different CI-produced image to change the provider release. Do not mount another CLI over it.

Additional deployment controls:

| Name | Purpose | Default / behavior |
|---|---|---|
| `KHS_CONFIG` | External service JSON path | Image: /config/service.json |
| `KHS_CONFIG_FILE` | Compose-only host service JSON file | ./examples/service.json |
| `KHS_KIRO_CONFIG_HOST_DIR` | Compose-only host native configuration directory | ./examples/kiro |
| `KHS_IMAGE` | CI-produced OCI image tag/digest | local tag in example; replace for deployment |
| `KIRO_API_KEY` | Native provider headless key referenced by default | Real worker only; no build/migration requirement |
| `KHS_ALEMBIC_CONFIG` | Migration ini path | alembic.ini |
| CLI mode / --component | api, worker, migrate, diagnose, openapi, cleanup, runtime-info; worker component all/runner/dispatcher | Explicit process command |
| `RUN_KIRO_SMOKE` | Real provider inference opt-in | Set 1 explicitly; otherwise skipped |
| `TEST_DATABASE_URL` | Disposable PostgreSQL integration database | Fixtures truncate service tables; never production |

Native configuration rules:

- Agents are JSON or Markdown in agents/. Filenames and optional names must match [a-zA-Z0-9_-]{1,64}; service-default is reserved. Duplicate JSON/Markdown IDs fail startup.
- Only regular files are accepted. Symlinks, hardlinks, special/hidden files and paths outside the root are rejected. The root is read-only in containers. Change files and restart all components to reload.
- Prompt file:// URIs are relative to the agent file. Resource file:// and skill:// URIs are relative to the root; .kiro/ prefixes are normalized. Only referenced resource files/skill directories are captured in the selected job bundle.
- Native permissions.rules preserve allow/ask/deny. Profile tools restrict available built-ins. Profile deny rules also apply to selected native agents; profile allow rules apply to the generated default agent only. Selected approved MCP tools receive exact capability rules, with deny rules still effective.
- Native hooks, sub-agents, broad tool selectors, knowledge-base indexing, global MCP/Powers inheritance, legacy allowedTools/toolsSettings and interactive OAuth fail explicitly. This integration does not claim every native Kiro feature.
- Native MCP env/headers use ${ENV_NAME} templates; secrets resolve at execution and are not stored in snapshots. Literal credential headers and credential-like environment values are rejected. Service-configured MCP also supports env:/file: references.
- Consumers list agent and MCP IDs they may use. A native agent requiring an unauthorized MCP fails; it cannot expand consumer permissions. Inline native MCP definitions must not conflict with another configured server of the same ID.
- Secret files must be mounted separately and readable by the relevant process. Put the same kiro_key_ref in the common service JSON; only the worker needs the actual provider secret mount. Never store resolved credentials in prompts, resources, MCP arguments or URLs.
- Request body 256 KiB; input 128 KiB JSON-encoded UTF-8; metadata 4096 JSON characters; schema 32768 JSON characters; at most 16 unique requested MCP IDs. JSON nesting 64; non-finite numbers, NUL and unpaired surrogates rejected.
- Requested deadline 1–3600s; output 128–8388608 bytes. Effective values clamp to applicable profile/server ceilings. Pagination 1–200 with exclusive nonnegative cursors. SSE connections last at most 30 seconds.
- Results use object-form Draft 2020-12 schemas with local references only; regex keywords are unsupported and format assertion is not promised.

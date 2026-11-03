# Errors and diagnosis

HTTP errors contain `error.code`, safe `message` and `correlation_id`. Runtime codes appear
on terminal job status; unsuccessful jobs never expose partial results.

| Condition | Code / status | Resolution |
|---|---|---|
| Invalid input | INVALID_INPUT / 422 | Follow OpenAPI; unknown fields are rejected |
| Oversized body | REQUEST_TOO_LARGE / 413 | Reduce below 256 KiB |
| Wrong API token | UNAUTHENTICATED / 401 | Supply the independent consumer bearer token |
| Unauthorized selection | ACCESS_DENIED / 403 | Check administrator permissions |
| Missing/other consumer's resource | NOT_FOUND / 404 | Check owner and retention |
| Key reused with different input | IDEMPOTENCY_CONFLICT / 409 | Use another key |
| Session busy/closed | SESSION_CONFLICT / 409 | Wait/cancel active work or create a session |
| History/context limit | CONTEXT_LIMIT / 409 | Start another conversation explicitly |
| Queue full | QUOTA_EXCEEDED / 429 | Back off |
| Disabled model/SSE | CAPABILITY_UNAVAILABLE / 422 | Query enabled capabilities |
| Missing worker/migration | NOT_READY / 503 | Run diagnose and apply migrations |
| Mixed configuration | CONFIGURATION_MISMATCH / 503 | Drain/restart all processes consistently |
| Provider key missing | KIRO_CREDENTIAL_MISSING | Supply the configured provider secret |
| Wrong binary | KIRO_VERSION_MISMATCH | Use the CLI packaged by the selected CI image release |
| Missing V3 interface | KIRO_V3_CAPABILITY_UNAVAILABLE | Verify the CI image contains the required V3 CLI capabilities |
| Old queued release | JOB_RELEASE_INCOMPATIBLE | Drain work before upgrading; do not reinterpret old V2 jobs as V3 |
| Provider auth rejection | KIRO_AUTHENTICATION_FAILED | Revalidate the deployer's key |
| MCP missing | MCP_STARTUP_FAILED / MCP_CREDENTIAL_MISSING | Check installed server, endpoint and secret refs |
| Runtime failure | KIRO_RUNTIME_FAILED / RUNTIME_REJECTED / RUNTIME_INTERRUPTED | Check provider and tool permissions |
| Protocol failure | RUNTIME_PROTOCOL_* | Check supported version; partial output is rejected |
| Output/events too large | RUNTIME_OUTPUT_LIMIT / RUNTIME_EVENT_LIMIT / RUNTIME_STDERR_LIMIT | Reduce work or adjust trusted limits |
| Timeout | EXECUTION_TIMED_OUT | Reconcile side effects before resubmitting |
| Worker disappeared | EXECUTION_AMBIGUOUS_WORKER_* | Reconcile external changes; no automatic replay |
| Bad business output | INVALID_RESULT | Change instructions/schema; no extraction/repair |
| File rejected | ARTIFACT_* | Use bounded direct regular files under artifacts/ |
| Literal credential exposure | SENSITIVE_OUTPUT_REJECTED | Fix trusted instructions/tools |

`kiro-service diagnose` performs no inference and cannot prove provider acceptance or MCP
availability. Raw provider stderr and exception strings are omitted from public logs. Use a
separate controlled provider invocation for investigation; never include credentials in reports.

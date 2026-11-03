# Contributing

Use Python 3.13 and uv 0.12.5. Run `uv sync --frozen`. Ordinary tests do not need Kiro credentials.

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
```

For integration, start a dedicated PostgreSQL 17 database, set `KHS_DATABASE_URL` and
`TEST_DATABASE_URL` to its `postgresql+psycopg://` URL, run `uv run alembic upgrade head`,
then `uv run pytest -q`. Integration fixtures TRUNCATE all service tables.
Run `uv run alembic check` to detect model/migration drift. Migrations are immutable snapshots.

Keep domain code free of infrastructure/framework imports. Put provider-specific formats in
the adapter. Add failure and concurrency tests when changing durable execution semantics.
Use `UPDATE_OPENAPI=1 uv run python scripts/check_openapi.py` after intentional API changes.
Do not check in credentials, provider binaries, or private configurations.

Provider smoke tests require explicit `RUN_KIRO_SMOKE=1`, a valid `KIRO_API_KEY`, and the CLI
release in src/kiro_headless_service/kiro-release.json (2.37.1 / V3). Distinguish provider results
from subprocess doubles. Versions are CI-owned, not runtime configuration. Native agents live
in an external KHS_KIRO_CONFIG_DIR; see examples/kiro for JSON/Markdown files and relative resources.
Container checks require Docker Compose or working rootless Podman. Run scripts/podman_smoke.sh
with a built runtime image and temporary test credentials; it creates and removes dedicated
containers/network/database volume. Public service-only image publication excludes the CLI;
the public image downloads Kiro on the deployer's machine instead of redistributing its binary.

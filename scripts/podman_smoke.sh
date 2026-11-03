#!/usr/bin/env bash
# Dedicated rootless integration check. All resources use a unique prefix and are cleaned up.
set -euo pipefail
: "${KHS_IMAGE:?Set the built image tag}"
: "${SERVICE_API_TOKEN:?Set a temporary test API token}"
: "${POSTGRES_PASSWORD:?Set a temporary test database password}"
test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
task_prefix="khs-smoke-$$"
task_network="${task_prefix}-net"
task_volume="${task_prefix}-pg"
task_port="${KHS_SMOKE_PORT:-18080}"
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cleanup() {
  podman rm -f "${task_prefix}-api" "${task_prefix}-worker" "${task_prefix}-migrate" "${task_prefix}-pg" >/dev/null 2>&1 || true
  podman volume rm "$task_volume" >/dev/null 2>&1 || true
  podman network rm "$task_network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
podman network create "$task_network" >/dev/null
podman volume create "$task_volume" >/dev/null
export POSTGRES_DB=kiro POSTGRES_USER=kiro
podman run -d --name "${task_prefix}-pg" --network "$task_network" \
  -e POSTGRES_PASSWORD -e POSTGRES_USER -e POSTGRES_DB \
  -v "$task_volume:/var/lib/postgresql/data:Z" \
  docker.io/library/postgres:17.11-bookworm@sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5 >/dev/null
for task_attempt in $(seq 1 60); do
  if podman exec "${task_prefix}-pg" pg_isready -U kiro -d kiro >/dev/null 2>&1; then break; fi
  if [ "$task_attempt" -eq 60 ]; then exit 1; fi
  sleep 1
done
export KHS_DATABASE_URL="postgresql+psycopg://kiro:${POSTGRES_PASSWORD}@${task_prefix}-pg:5432/kiro"
export KHS_ENGINE=simulated KHS_KIRO_CONFIG_DIR=/config/kiro KHS_CONFIG=/config/service.json
task_args=(--network "$task_network" --userns=keep-id:uid=10001,gid=10001 --user 10001:10001
  --read-only --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=256 --memory=1g
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 --init
  -v "$task_root/examples/service.json:/config/service.json:ro,z"
  -v "$task_root/examples/kiro:/config/kiro:ro,z"
  -e KHS_DATABASE_URL -e KHS_ENGINE -e KHS_KIRO_CONFIG_DIR -e KHS_CONFIG -e SERVICE_API_TOKEN)
podman run --name "${task_prefix}-migrate" "${task_args[@]}" "$KHS_IMAGE" migrate
podman run -d --name "${task_prefix}-worker" "${task_args[@]}" "$KHS_IMAGE" worker >/dev/null
podman run -d --name "${task_prefix}-api" "${task_args[@]}" -p "127.0.0.1:$task_port:8000" "$KHS_IMAGE" api >/dev/null
export SERVICE_URL="http://127.0.0.1:$task_port"
python3 "$task_root/scripts/container_smoke.py"
podman exec "${task_prefix}-worker" python -c 'import os; assert os.getuid() == 10001'
podman restart "${task_prefix}-api" "${task_prefix}-worker" >/dev/null
python3 "$task_root/scripts/container_smoke.py"
# Provider presence/help is checked independently, with no mounted configuration or account key.
podman run --rm --userns=keep-id:uid=10001,gid=10001 --user 10001:10001 \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 --cap-drop=ALL \
  --security-opt=no-new-privileges "$KHS_IMAGE" runtime-info

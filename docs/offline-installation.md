# Installation on a restricted server

The server may lack access to GHCR/download sites while retaining approved connectivity to
Kiro/AWS through a firewall, proxy or private network. The steps below move installation
artifacts separately. Real inference still uses Kiro's remote services; this does not provide
local-model inference in a fully isolated network. Consult the official
[firewall requirements](https://kiro.dev/docs/privacy-and-security/firewalls/).

Use a connected preparation machine with the same CPU architecture as the server. Podman,
its Compose provider and transfer tools must already be installed on the destination.
Replace the service image below with a published **bootstrap** release newer than v1.0.0.

## Prepare on the connected machine

```sh
export KHS_IMAGE=ghcr.io/OWNER/REPO:vX.Y.Z
export PG_IMAGE=docker.io/library/postgres:17.11-bookworm@sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5
podman pull "$KHS_IMAGE"
podman pull "$PG_IMAGE"
mkdir -p runtime-cache
podman run --rm --userns=keep-id:uid=10001,gid=10001 --user 10001:10001 \
  -v "$PWD/runtime-cache:/var/lib/kiro:z" "$KHS_IMAGE" prepare-runtime
podman save --format oci-archive -o service.oci.tar "$KHS_IMAGE"
podman save --format oci-archive -o postgres.oci.tar "$PG_IMAGE"
tar -czf runtime-cache.tar.gz -C runtime-cache .
sha256sum service.oci.tar postgres.oci.tar runtime-cache.tar.gz > SHA256SUMS
```

prepare-runtime needs no config, database, KIRO_API_KEY or inference. It verifies the official
archive and executable version/V3 help. The cache contains executables/provenance, not account
credentials. Transfer these four files, the Compose files, and your external service/agent
configuration through your approved channel. Keep the integrity manifest through a trusted
channel. Supply runtime secrets separately; do not bundle them in the archive.

If you already have the official matching archive, preparation can avoid downloads as well:

```sh
podman run --rm --network=none --userns=keep-id:uid=10001,gid=10001 \
  -v "$PWD/runtime-cache:/var/lib/kiro:z" \
  -v "$PWD/kirocli.tar.gz:/transfer/kirocli.tar.gz:ro,z" \
  "$KHS_IMAGE" prepare-runtime --archive /transfer/kirocli.tar.gz
```

## Import on the server

```sh
sha256sum -c SHA256SUMS
podman load -i service.oci.tar
podman load -i postgres.oci.tar
mkdir -p runtime-cache
tar -xzf runtime-cache.tar.gz -C runtime-cache
export KHS_CLI_CACHE_HOST_DIR="$PWD/runtime-cache"
```

Configure .env with KHS_IMAGE, SERVICE_API_TOKEN, POSTGRES_PASSWORD, KIRO_API_KEY and your
external configuration paths. Start with the imported cache mounted read-only:

```sh
podman compose -f compose.yaml -f compose.podman.yaml -f compose.offline.yaml \
  up -d --no-build --pull never
```

Use a Compose provider supporting --pull never, such as Docker Compose v2 through
PODMAN_COMPOSE_PROVIDER=docker-compose. Keep the image reference identical to its imported tag.
If your provider lacks that option, configure its equivalent never-pull policy explicitly.
External PostgreSQL uses compose.external.yaml instead and does not need the PostgreSQL OCI
archive. Recreate containers after changing the release; import the cache for that release
and matching architecture. Never copy a populated PostgreSQL data volume between live servers.

The offline worker never attempts to fetch Kiro. Missing, wrong-release or corrupt caches
produce KIRO_RUNTIME_CACHE_MISSING/INVALID before accepting work. Cache verification does not
modify its read-only mount. It is distinct from disposable per-job HOME/workspaces.

## Proxy and private connectivity

If an explicit proxy is required, set HTTPS_PROXY (and HTTP_PROXY if needed) in the worker
deployment environment. Compose forwards them for installer downloads. Authorize the exact
variables for the isolated Kiro child, for example in .env:

```dotenv
HTTPS_PROXY=http://proxy.internal:8080
NO_PROXY=localhost,127.0.0.1,postgres
KHS_KIRO_PROXY_ENV_REFS={"HTTPS_PROXY":"env:HTTPS_PROXY","NO_PROXY":"env:NO_PROXY"}
```

These are service secret references; resolved proxy values are excluded from job snapshots
and rejected if echoed into results. file: references also work for child proxy settings.
Proxy values are resolved by the worker, not ordinary HTTP consumers. A private routed
connection that needs no proxy requires no proxy variables. Follow your network team's TLS
trust, DNS and endpoint requirements. Real proxy/private-endpoint inference was not tested
without credentials; an allowed-domain list alone does not demonstrate successful inference.

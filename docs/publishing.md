# Public images and publication

The default Dockerfile target `runtime` is the public deployment image. It includes the
service, installer and immutable Kiro release manifest, but no proprietary CLI binary.
The real worker downloads the official 2.37.1 archive on the deployer's machine, verifies
size/SHA-256 and atomically populates a cache named by version, architecture and archive hash.
Subsequent starts verify/reuse that cache. Offline installations import a prepared cache.
CI still selects the exact CLI version/engine; deployers select a service image tag/digest.

Target `service` remains available for installations using an explicitly installed CLI.
Builds do not include user agents, account keys, proxy credentials or database credentials.
Kiro's [license](https://kiro.dev/license/) refers to the
[AWS IP terms](https://aws.amazon.com/legal/aws-ip-license-terms/); downloading at the user's
installation avoids distributing that proprietary binary through our public registry.

## Release

1. Commit the changes to main, validate CI, and create a new vMAJOR.MINOR.PATCH Git tag.
2. Push the tag or dispatch publish.yml for that existing tag. No publish_kiro input is needed.
3. Publication builds target runtime for linux/amd64 and linux/arm64, with provenance/SBOM.
4. For a new GHCR package, open Package settings → Change visibility → Public once. Repository
   visibility does not make its container package public. Only change the visibility of the
   new installer-based image package, not an older package containing the CLI.
5. The workflow checks a pull by digest using an empty Docker credential directory. If the
   package is still private, this check fails with instructions; rerun after making it public.
6. Deploy ghcr.io/OWNER/REPO:vX.Y.Z using KHS_IMAGE. No registry login is needed for public pulls.

Only the publication job has packages:write. PR validation gets contents:read and no provider
secrets. Configure the existing ghcr-release environment protection for your release process.
No GHCR visibility change or publication was performed by this implementation.

The existing public v1.0.0 image was inspected anonymously during implementation: it is the
older service-only target, with kiro-service api as its default command and no CLI installation.
Use a new tag for the bootstrap image rather than replacing that released artifact.

## Validate before release

```sh
podman build -t localhost/kiro-headless-service:local .
KHS_IMAGE=localhost/kiro-headless-service:local \
SERVICE_API_TOKEN=temporary-test-token-at-least-24-characters \
POSTGRES_PASSWORD=temporary-test-database-only bash scripts/podman_smoke.sh
```

The smoke downloads the CLI into a temporary volume, checks simulated jobs/native agents,
restarts services, then verifies the actual CLI with no network and a read-only cache/root.
Both native architectures have CI CLI checks; compilation alone is not platform verification.

See [offline installation](offline-installation.md), [configuration](configuration.md), and
[verification](verification.md). A public image still needs KIRO_API_KEY in real workers;
anonymous registry download and provider authentication are separate concerns.

# CI images and publication

`Dockerfile` has two final targets:

- `kiro` (default): complete deployment image with the exact CLI in the release manifest.
- `service`: provider-free MIT service image, used for public publication/account-free use.

Both API and worker use the same chosen image. There is no build-version argument or runtime
version selector. CI reads `src/kiro_headless_service/kiro-release.json`; its exact archive
hashes/sizes are verified before extracting binaries. The image's `/opt/kiro/provenance.json`
records the installed release/architecture/source/checksum. No Kiro account key is used during
build or `runtime-info`. The full image labels both own-code and provider licensing separately.

```sh
podman build -t localhost/kiro-headless-service:local .
podman run --rm localhost/kiro-headless-service:local runtime-info
podman build --target service -t localhost/kiro-headless-service:base .
```

Python/uv/PostgreSQL base images have immutable registry digests; Python dependencies are frozen
in uv.lock. A release-manifest change changes the CLI installation layer. CI performs format,
types, unit/PostgreSQL/migration tests, complete-image Docker startup, native arm64 CLI checks
and a rootless Podman smoke with UID mapping, external agents, database and restarts.
Builds do not bake user agents, account keys, API tokens or database credentials into layers.

Kiro is proprietary under its [license](https://kiro.dev/license/) and applicable
[AWS IP terms](https://aws.amazon.com/legal/aws-ip-license-terms/). MIT does not provide rights
to redistribute that binary. Consequently the tag-triggered public workflow publishes only
the `service` target. Complete CLI images build in validation CI without being uploaded as
public artifacts. Their publication is a separate opt-in private deployment workflow under
the deployer's applicable agreement; do not make that package public without separate rights.

To prepare publication:

1. Protect release tags and create `ghcr-release` with required reviewers for service releases.
2. For your own authorized private CLI-image deployment, also create `ghcr-kiro-private` with
   the appropriate required reviewers. Its manual workflow input is `publish_kiro=true`.
3. Allow GITHUB_TOKEN package creation; only publication jobs have packages:write. PR jobs
   receive contents:read and no provider secrets, and never use pull_request_target.
4. Push `vMAJOR.MINOR.PATCH` or dispatch publication for an existing tag. Validation must pass.
5. Public base tags are `ghcr.io/OWNER/REPO:vX.Y.Z`. Explicitly set that service-only package
   visibility to Public; repository visibility does not set package visibility.
6. Opt-in complete-image tags are `ghcr.io/OWNER/REPO-kiro:vX.Y.Z-kiro2.37.1`. Existing target
   packages must be private; the workflow checks visibility and refuses public/internal
   targets. New GHCR packages initially default to private. Keep that visibility and limit access.
7. Configure KHS_IMAGE to the appropriate published tag or immutable digest and recreate
   API/worker together. Registry login is separate from the runtime provider/API/database keys.

For anonymous verification of a public service-only package, use a newly created empty
Docker configuration directory with no credential helpers:

```sh
mkdir -p /tmp/kiro-anonymous-docker-config
DOCKER_CONFIG=/tmp/kiro-anonymous-docker-config docker pull ghcr.io/OWNER/REPO:vX.Y.Z
```

No workflow was dispatched, image published, visibility changed or anonymous pull performed
by this implementation. Local OCI execution restrictions and actual results are recorded
in verification.md. Prepared CI jobs are not represented as completed hosted checks.

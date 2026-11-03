from pathlib import Path

import yaml


def test_publication_uses_bootstrap_image_and_checks_anonymous_pull():
    workflow = yaml.safe_load(Path(".github/workflows/publish.yml").read_text())
    steps = workflow["jobs"]["publish"]["steps"]
    build = next(s for s in steps if s.get("id") == "build")
    assert build["with"]["target"] == "runtime"
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    anonymous = next(s for s in steps if s.get("name") == "Verify anonymous pull")
    assert 'DOCKER_CONFIG="$task_auth_dir" docker pull' in anonymous["run"]
    assert "check_private_package" not in str(steps)


def test_public_default_image_does_not_copy_the_proprietary_cli():
    dockerfile = Path("Dockerfile").read_text()
    assert "FROM service AS runtime" in dockerfile
    assert "KHS_CLI_BOOTSTRAP=download" in dockerfile
    assert "COPY --from=provider" not in dockerfile
    assert "fetch_kiro.py --output" not in dockerfile

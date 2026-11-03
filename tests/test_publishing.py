import io
import json
import runpy
import urllib.error
import urllib.request

import pytest


@pytest.mark.parametrize(
    "status,visibility,allowed",
    [
        (200, "private", True),
        (404, None, True),
        (200, "public", False),
        (200, "internal", False),
        (200, None, False),
        (401, None, False),
        (403, None, False),
        (500, None, False),
    ],
)
def test_provider_publication_requires_verified_private_target(
    monkeypatch, status, visibility, allowed
):
    for key, value in {
        "OWNER": "example-org",
        "OWNER_TYPE": "Organization",
        "REPOSITORY": "Service",
        "GH_TOKEN": "test-only-token",
    }.items():
        monkeypatch.setenv(key, value)

    def response(request, *, timeout):
        assert request.full_url == (
            "https://api.github.com/orgs/example-org/packages/container/service-kiro"
        )
        assert request.get_header("Authorization") == "Bearer test-only-token"
        assert timeout == 20
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "test", {}, None)
        return io.BytesIO(json.dumps({"visibility": visibility}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", response)
    if allowed:
        runpy.run_path("scripts/check_private_package.py")
    else:
        with pytest.raises(SystemExit, match="publication|publication stopped"):
            runpy.run_path("scripts/check_private_package.py")

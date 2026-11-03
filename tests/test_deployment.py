import http.client
import http.server
import io
import json
import runpy
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("filename", ["compose.yaml", "compose.external.yaml"])
def test_compose_tmpfs_is_one_absolute_mount_with_bounded_writable_tmp(filename):
    config = yaml.safe_load(Path(filename).read_text())
    for service in ("api", "worker", "migrate"):
        definition = config["services"][service]
        # YAML flow-list commas previously turned these options into separate mounts.
        mounts = definition["tmpfs"]
        assert len(mounts) == 1
        target, options = mounts[0].split(":", 1)
        assert target == "/tmp"
        assert set(options.split(",")) == {"rw", "nosuid", "nodev", "size=512m", "mode=1777"}
        assert definition["read_only"] is True
        assert definition["user"] == "10001:10001"


@pytest.fixture
def wait_ready():
    return runpy.run_path("scripts/container_smoke.py")["wait_for_ready"]


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionResetError(104, "Connection reset by peer"),
        ConnectionRefusedError(111, "Connection refused"),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        urllib.error.URLError("not listening yet"),
        TimeoutError("connect timed out"),
        urllib.error.HTTPError("http://localhost/health/ready", 503, "not ready", {}, None),
    ],
)
def test_readiness_retries_transient_startup_failures(monkeypatch, wait_ready, failure):
    attempts = []

    def open_request(request, *, timeout):
        attempts.append(request.full_url)
        assert request.get_header("Authorization") == "Bearer test-only-token"
        assert 0 < timeout <= 5
        if len(attempts) == 1:
            raise failure
        return io.BytesIO(b'{"status":"ready"}')

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    wait_ready("http://localhost", "test-only-token")
    assert attempts == ["http://localhost/health/ready"] * 2


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_readiness_does_not_hide_http_configuration_or_application_errors(
    monkeypatch, wait_ready, status
):
    failure = urllib.error.HTTPError("http://localhost/health/ready", status, "test", {}, None)

    def open_request(*args, **kwargs):
        raise failure

    def unexpected_sleep(_):
        pytest.fail("permanent HTTP failure was retried")

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    monkeypatch.setattr(time, "sleep", unexpected_sleep)
    with pytest.raises(urllib.error.HTTPError) as exc:
        wait_ready("http://localhost", "test-only-token")
    assert exc.value is failure


def test_readiness_timeout_is_bounded_and_reports_last_failure(monkeypatch, wait_ready):
    clock = [0.0]
    attempts = []

    def open_request(request, *, timeout):
        attempts.append(timeout)
        raise ConnectionResetError("test reset")

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    with pytest.raises(TimeoutError, match="ConnectionResetError"):
        wait_ready("http://localhost", "test-only-token", timeout=1.5)
    assert clock[0] == 1.5
    assert attempts == [1.5, 0.5]


def test_readiness_recovers_from_real_closed_connection_then_http_503(monkeypatch, wait_ready):
    attempts = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            attempts.append((self.path, self.headers.get("Authorization")))
            if len(attempts) == 1:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(503 if len(attempts) == 2 else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ready"}).encode())

        def log_message(self, *args):
            pass

    monkeypatch.setattr(time, "sleep", lambda _: None)
    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            wait_ready(f"http://127.0.0.1:{server.server_port}", "test-only-token", timeout=5)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert attempts == [("/health/ready", "Bearer test-only-token")] * 3

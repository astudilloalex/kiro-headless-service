import socket

import httpx
import pytest

from kiro_headless_service.infrastructure.notifications import WebhookSender, validate_url


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "https://u:p@host/path", "https://host/p?secret=x"]
)
def test_invalid_callback_url(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
async def test_callback_blocks_private_resolution(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443))])
    with pytest.raises(ValueError, match="outside authorized"):
        await WebhookSender(["https://hooks.example.com/job"], []).deliver(
            "https://hooks.example.com/job", {"id": "stable"}
        )


async def test_callback_pins_dns_and_disables_redirects(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    sender = WebhookSender(["https://hooks.example.com/job"], ["127.0.0.1/32"])
    with pytest.raises(ValueError, match="non-success"):
        await sender.deliver("https://hooks.example.com/job", {"id": "stable"})
    assert len(observed) == 1
    assert observed[0].url.host == "127.0.0.1"
    assert observed[0].headers["Host"] == "hooks.example.com"
    assert observed[0].extensions["sni_hostname"] == "hooks.example.com"

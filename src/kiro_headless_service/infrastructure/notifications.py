import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx


def validate_url(url: str) -> None:
    p = urlsplit(url)
    if (
        p.scheme not in {"http", "https"}
        or not p.hostname
        or p.username
        or p.password
        or p.fragment
        or p.query
        or "\\" in url
    ):
        raise ValueError(
            "callback must be an absolute HTTP(S) URL without credentials, query or fragment"
        )
    _ = p.port


class WebhookSender:
    def __init__(self, allowed_urls: list[str], private_cidrs: list[str]):
        self.allowed_urls = frozenset(allowed_urls)
        self.networks = [ipaddress.ip_network(cidr) for cidr in private_cidrs]

    async def deliver(self, url: str, event: dict[str, Any]) -> None:
        validate_url(url)
        if url not in self.allowed_urls:
            raise ValueError("callback destination is no longer authorized")
        parsed = urlsplit(url)
        host = parsed.hostname
        assert host is not None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        addresses = sorted({info[4][0] for info in infos})
        if not addresses:
            raise ValueError("callback DNS returned no address")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            explicitly_allowed = any(ip in network for network in self.networks)
            if not ip.is_global and not explicitly_allowed:
                raise ValueError("callback resolved outside authorized networks")
            if parsed.scheme == "http" and not explicitly_allowed:
                raise ValueError("cleartext callbacks require an explicit destination CIDR")
        # Pin the validated address for the connection, retaining hostname for Host and TLS SNI.
        target = httpx.URL(url).copy_with(host=addresses[0])
        authority = host if parsed.port is None else f"{host}:{port}"
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            async with client.stream(
                "POST",
                target,
                json=event,
                headers={"Host": authority, "X-Kiro-Service-Event-ID": str(event["id"])},
                extensions={"sni_hostname": host},
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError("callback returned a non-success status")

#!/usr/bin/env python3.13
import asyncio
import urllib.parse

class GhostNodeClient:
    """
    Embeddable GhostNode client for use as a library backend.

    Usage:
        from client.api import GhostNodeClient, parse_gn_link

        cfg = parse_gn_link("gn://...")
        client = GhostNodeClient(cfg)

        # Start a SOCKS5 proxy
        await client.start_socks5("127.0.0.1", 1080)
        # ... do work ...
        await client.stop()

        # Or open a raw tunneled stream directly
        reader, writer = await client.open_stream("example.com", 443)

        # Or use as async context manager
        async with GhostNodeClient(cfg) as client:
            reader, writer = await client.open_stream("example.com", 80)
    """

    ATYP_DOMAIN = 0x03
    ATYP_IPV4 = 0x01
    ATYP_IPV6 = 0x04

    def __init__(self, config: dict):
        self._cfg = config
        self._server = None
        self.stats = {"active": 0, "total": 0, "sent": 0, "recv": 0}

    async def open_stream(self, host: str, port: int, atyp: int = ATYP_DOMAIN):
        from client.connector import connect_to_server
        return await connect_to_server(self._cfg, host, port, atyp)

    async def start_socks5(self, host: str = "127.0.0.1", port: int = 1080):
        from client.connector import connect_to_server
        from client.socks5 import start_socks5_server
        async def connect_fn(addr, p, atyp):
            return await connect_to_server(self._cfg, addr, p, atyp)
        self._server = await start_socks5_server(host, port, connect_fn, self.stats)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.stop()


def parse_gn_link(link: str) -> dict:
    link = link.strip()
    if not link.startswith("gn://"):
        raise ValueError("invalid gn:// link")
    rest = link[5:]
    if "@" not in rest:
        raise ValueError("missing @ in link")
    nanoid, hostpart = rest.split("@", 1)
    if "?" in hostpart:
        hostport, query = hostpart.split("?", 1)
    else:
        hostport, query = hostpart, ""
    name = ""
    if "#" in query:
        query, fragment = query.split("#", 1)
        name = urllib.parse.unquote(fragment)
    if ":" in hostport:
        host, port_str = hostport.rsplit(":", 1)
        port = int(port_str)
    else:
        host = hostport
        port = 443
    params = dict(urllib.parse.parse_qsl(query))
    transport = params.get("transport", "ws")
    path = params.get("path", "/gn")
    security = params.get("security", "none")
    sni = params.get("sni", "")
    url_scheme = "wss" if security == "tls" else "ws"
    if transport in ("h2", "http2", "hr", "http-request", "sse", "http-request-sse", "hrb", "http-request-body"):
        url_scheme = "https" if security == "tls" else "http"
    url = f"{url_scheme}://{host}:{port}"
    return {"nanoid": nanoid, "name": name or host, "url": url, "transport": transport, "path": path, "sni": sni, "allow_insecure": security == "none" and not sni, "host": host, "port": port, "fp": params.get("fp", "")}

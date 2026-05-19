[فارسی](README_FA.md)

# GhostNode

A self-hosted proxy server panel built from scratch. Manage inbounds, clients, routing, and outbounds through a clean web UI or REST API.

## Features

- Multi-inbound support (each inbound has its own port and transport)
- Multi-client management with per-client traffic limits and expiry dates
- GNP binary protocol with NanoID-20 client IDs and yamux-style multiplexing
- Routing engine with domain, IP, CIDR, port, protocol, and inbound tag matching
- GeoIP and GeoSite support via v2fly `geoip.dat` and `geosite.dat`
- Seven transport types: websocket, http2, grpc, http-request, http-request-sse, http-request-body, mixed (SOCKS5+HTTP proxy)
- Outbound types: direct, block, ghostnode, socks5, http
- Per-client traffic accounting (upload + download byte counters)
- Dark and light panel themes
- Persian and English UI
- URI-path authentication (no login page, panel hidden behind a secret path)
- `gn://` config link generation with QR code export
- Optional Xray core integration with its own inbounds, clients, and outbounds

## Quick Install

Run as root on a fresh Debian/Ubuntu server:

```bash
bash <(curl -fsSL https://github.com/FrenchToblerone54/GhostNode/releases/latest/download/install.sh)
```

## Manual Run

```bash
python3.13 main.py -c /etc/ghostnode/config.toml
```

Generate a random panel path token:
```bash
python3.13 main.py --generate-token
```

## Config File

The config is TOML. Default path: `/etc/ghostnode/config.toml`

```toml
[panel]
host = "127.0.0.1"
port = 9090
path = "yourSecretPath"
threads = 4

[database]
path = "/etc/ghostnode/ghostnode.db"

[geo]
geoip_dat_path = "/etc/ghostnode/geo/geoip.dat"
geosite_path = "/etc/ghostnode/geo/geosite.dat"

[logging]
level = "info"
file = "/var/log/ghostnode.log"

[server]
hostname = ""
auto_update = true
update_check_interval = 300
update_proxy = ""
```

`path` acts as the URI auth token. The panel is only reachable at `/{path}/`. All other paths return 404.

## Config Link Format

Clients connect using a `gn://` URI:

```
gn://NANOID@host:port?transport=ws&path=/gn&security=tls&sni=override.com&fp=BASE64#Name
```

| Parameter | Description |
|-----------|-------------|
| `NANOID` | 20-character client NanoID |
| `host:port` | Inbound address |
| `transport` | Transport type |
| `path` | HTTP path used by the transport |
| `security` | `tls` or omitted |
| `sni` | Optional TLS SNI override |
| `fp` | Server public key SHA-256 fingerprint (base64url, MITM protection) |
| `#Name` | Human-readable client label |

## Transport Types

| Type | Key | Notes |
|------|-----|-------|
| WebSocket | `ws` | Standard WebSocket upgrade |
| HTTP/2 | `h2` | Full-duplex H2 streams |
| gRPC | `grpc` | gRPC streaming |
| HTTP Request | `http-request` | Long-poll HTTP GET/POST |
| HTTP Request SSE | `http-request-sse` | Server-Sent Events downlink |
| HTTP Request Body | `http-request-body` | Chunked request body |
| Mixed | `mixed` | SOCKS5 + HTTP CONNECT proxy on one port, uses routing engine, no GNP clients |

## GeoIP and GeoSite

GhostNode uses the v2fly binary format (`geoip.dat`, `geosite.dat`). The install script downloads them automatically. To update manually:

```bash
curl -Lo /etc/ghostnode/geo/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat
curl -Lo /etc/ghostnode/geo/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat
```

## Documentation

- [REST API Reference](docs/api.md)

## Client Library

`ghostnode-client` can be embedded in other Python apps as a backend:

```python
from client.api import GhostNodeClient, parse_gn_link

cfg = parse_gn_link("gn://...")

# Start a SOCKS5 proxy
async with GhostNodeClient(cfg) as client:
    await client.start_socks5("127.0.0.1", 1080)

# Or open a raw tunneled stream directly
async with GhostNodeClient(cfg) as client:
    reader, writer = await client.open_stream("example.com", 443)
```

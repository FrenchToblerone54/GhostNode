# GhostNode

A self-hosted proxy server panel built from scratch. Manage inbounds, clients, routing, and outbounds through a clean web UI or REST API.

## Features

- Multi-inbound support (each inbound has its own port and transport)
- Multi-client management with per-client traffic limits and expiry dates
- GNP binary protocol with NanoID-20 client IDs and yamux-style multiplexing
- Routing engine with domain, IP, CIDR, port, protocol, and inbound tag matching
- GeoIP and GeoSite support via v2fly `geoip.dat` and `geosite.dat`
- Six transport types: websocket, http2, grpc, http-request, http-request-sse, http-request-body
- Outbound types: direct, block, ghostnode, socks5, http
- Per-client traffic accounting (upload + download byte counters)
- Dark and light panel themes
- Persian and English UI
- URI-path authentication (no login page, panel hidden behind a secret path)
- `gn://` config link generation with QR code export

## Quick Install

Run as root on a fresh Debian/Ubuntu server:

```bash
bash install.sh
```

The script installs Python 3.13, copies files to `/opt/ghostnode`, downloads GeoIP/GeoSite data, and sets up a systemd service.

## Manual Run

```bash
python3.13 main.py -c /etc/ghostnode/config.toml
```

## Config File

The config is TOML. Default path: `/etc/ghostnode/config.toml`

```toml
[panel]
host = "0.0.0.0"
port = 2053
panel_path = "yourSecretPath"

[database]
path = "/etc/ghostnode/ghostnode.db"

[geo]
geoip_path = "/etc/ghostnode/geoip.dat"
geosite_path = "/etc/ghostnode/geosite.dat"

[logging]
level = "info"
file = "/var/log/ghostnode.log"

[server]
# reserved for future server-level settings
```

`panel_path` acts as the URI auth token. The panel is only reachable at `/{panel_path}/`. All other paths return 404.

## Config Link Format

Clients connect using a `gn://` URI:

```
gn://NANOID@host:port?transport=ws&path=/gn&security=tls&host=override.com#Name
```

| Parameter | Description |
|-----------|-------------|
| `NANOID` | 20-character client NanoID |
| `host:port` | Inbound address |
| `transport` | Transport type (see table below) |
| `path` | HTTP path used by the transport |
| `security` | `tls` or omitted |
| `host` | Optional TLS SNI / Host header override |
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

## GeoIP and GeoSite

GhostNode uses the v2fly binary format (`geoip.dat`, `geosite.dat`). The install script downloads them automatically. To update manually:

```bash
curl -Lo /etc/ghostnode/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat
curl -Lo /etc/ghostnode/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat
```
